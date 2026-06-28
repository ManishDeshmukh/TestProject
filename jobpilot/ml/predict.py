"""Inference wrapper.

Tries trained ``.pkl`` models (joblib + sklearn/xgboost) first; otherwise falls
back to historical percentiles from SQLite, and finally to cold-start
``TOOL_DEFAULTS``.  A confidence label is *always* returned alongside the number.
"""

from __future__ import annotations

from . import features as F
from ..constants import TOOL_DEFAULTS, DEFAULT_RESOURCES

try:
    import joblib  # type: ignore
    _HAVE_JOBLIB = True
except ImportError:
    _HAVE_JOBLIB = False


def _percentile(values, pct):
    if not values:
        return None
    vals = sorted(values)
    k = (len(vals) - 1) * pct
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return vals[f]
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def confidence_label(n, cfg=None):
    high = cfg.getint("prediction", "confidence_high", 1000) if cfg else 1000
    med = cfg.getint("prediction", "confidence_medium", 200) if cfg else 200
    low = cfg.getint("prediction", "confidence_low", 10) if cfg else 10
    if n >= high:
        return "HIGH"
    if n >= med:
        return "MEDIUM"
    if n >= low:
        return "LOW"
    return "NONE"


class Predictor:
    def __init__(self, db, cfg):
        self.db = db
        self.cfg = cfg
        self.models = self._load_models()

    def _load_models(self):
        models = {}
        if not _HAVE_JOBLIB:
            return models
        for name in ("tat_current", "mem_current", "fail_current"):
            path = self.cfg.models_path / f"{name}.pkl"
            if path.exists():
                try:
                    models[name] = joblib.load(path)
                except Exception:
                    pass
        return models

    def predict_compute_and_mem(self, job: dict):
        """Return (compute_sec, mem_mb, confidence, sample_count)."""
        feat = F.extract(job)
        tool, stage, queue = feat["tool"], feat["flow_stage"], feat["queue"]
        n = self.db.count_similar(tool, stage, queue) if (tool and stage) else 0
        conf = confidence_label(n, self.cfg)

        # Model path (if trained models present and enough samples).
        if self.models.get("tat_current") and n >= 10:
            try:
                vec = [F.vectorize(feat)]
                compute = float(self.models["tat_current"].predict(vec)[0])
                mem = (float(self.models["mem_current"].predict(vec)[0])
                       if self.models.get("mem_current") else None)
                if mem:
                    return int(compute), int(mem), conf, n
            except Exception:
                pass

        # Historical percentile path.
        if n >= 10:
            sims = self.db.get_similar_jobs(tool, queue, limit=500)
            comps = [s["actual_compute_sec"] for s in sims if s.get("actual_compute_sec")]
            mems = [s["mem_peak_mb"] for s in sims if s.get("mem_peak_mb")]
            if comps and mems:
                buf = self.cfg.getfloat("prediction", "mem_safety_buffer", 1.2)
                return (int(_percentile(comps, 0.5)),
                        int(_percentile(mems, 0.9) * buf), conf, n)

        # Cold start.
        d = TOOL_DEFAULTS.get(tool, DEFAULT_RESOURCES)
        return (d["p90_rt_min"] * 60, d["p90_mem_mb"], "NONE", n)
