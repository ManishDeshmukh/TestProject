"""Training pipeline (P8/P11).

Trains on ``actual_compute_sec`` — NOT total ``runtime_sec`` — so license wait is
never baked into the compute model.  Uses XGBoost when available, else sklearn,
else a trivial percentile "model".  Models are warm-started from the previous
``.pkl`` and only promoted when the new MAE is within 5% of the old.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime

from . import features as F

try:
    import joblib  # type: ignore
    _HAVE_JOBLIB = True
except ImportError:
    _HAVE_JOBLIB = False

try:
    from xgboost import XGBRegressor  # type: ignore
    _HAVE_XGB = True
except ImportError:
    _HAVE_XGB = False

try:
    from sklearn.linear_model import LinearRegression  # type: ignore
    _HAVE_SK = True
except ImportError:
    _HAVE_SK = False


def _mae(model, X, y):
    preds = model.predict(X)
    return sum(abs(p - t) for p, t in zip(preds, y)) / max(len(y), 1)


def build_matrix(db):
    """Build (X, y_compute, y_mem) from completed jobs with actual_compute_sec."""
    rows = db.get_completed_for_training()
    X, y_compute, y_mem = [], [], []
    for r in rows:
        if not r.get("actual_compute_sec"):
            continue
        X.append(F.vectorize(F.extract(r)))
        y_compute.append(r["actual_compute_sec"])
        y_mem.append(r.get("mem_peak_mb") or 0)
    return X, y_compute, y_mem


def train_all(db, cfg, min_samples=10):
    """Train + conditionally promote models. Returns a result summary dict."""
    X, y_compute, y_mem = build_matrix(db)
    n = len(X)
    result = {"samples": n, "trained": False, "reason": ""}
    if n < min_samples:
        result["reason"] = f"insufficient samples ({n} < {min_samples})"
        return result
    if not (_HAVE_JOBLIB and (_HAVE_XGB or _HAVE_SK)):
        result["reason"] = "ML libraries not installed (sklearn/xgboost/joblib)"
        return result

    models_dir = cfg.models_path
    meta_path = models_dir / "model_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"version": 0}

    new_tat = _fit(X, y_compute, warm=models_dir / "tat_current.pkl")
    new_mem = _fit(X, y_mem, warm=models_dir / "mem_current.pkl")

    new_mae = _mae(new_tat, X, y_compute)
    old_mae = meta.get("tat_mae", float("inf"))
    promote = new_mae <= old_mae * 1.05

    if promote:
        version = meta.get("version", 0) + 1
        joblib.dump(new_tat, models_dir / f"tat_v{version}.pkl")
        joblib.dump(new_tat, models_dir / "tat_current.pkl")
        joblib.dump(new_mem, models_dir / "mem_current.pkl")
        meta.update({
            "version": version, "trained_at": datetime.now().isoformat(),
            "training_samples": n, "tat_mae": new_mae,
        })
        meta_path.write_text(json.dumps(meta, indent=2))
        result.update({"trained": True, "version": version, "mae": round(new_mae, 1)})
        # Optionally distribute to central NFS models path.
        central = cfg.get("central", "central_models_path", "")
        if central:
            try:
                shutil.copy(models_dir / "tat_current.pkl", central)
            except OSError:
                pass
    else:
        result["reason"] = f"not promoted (new MAE {new_mae:.1f} > old {old_mae:.1f} * 1.05)"
    return result


def _fit(X, y, warm=None):
    if _HAVE_XGB:
        model = XGBRegressor(n_estimators=50, learning_rate=0.05, max_depth=5)
        kwargs = {}
        if warm and warm.exists() and _HAVE_JOBLIB:
            try:
                kwargs["xgb_model"] = joblib.load(warm)
            except Exception:
                pass
        model.fit(X, y, **kwargs)
        return model
    # sklearn fallback (no incremental warm start, but still trains).
    model = LinearRegression()
    model.fit(X, y)
    return model
