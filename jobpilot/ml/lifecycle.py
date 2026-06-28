"""Nightly aggregate + retrain orchestration (P8).

Rotates raw records on schedule, recomputes license wait statistics, and triggers
a warm-start retrain.  Models (.pkl) are never deleted.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from . import train


def _percentile(values, pct):
    if not values:
        return 0.0
    vals = sorted(values)
    k = (len(vals) - 1) * pct
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] if f == c else vals[f] + (vals[c] - vals[f]) * (k - f)


def update_license_wait_stats(db):
    """Recompute P50/P90 license wait by (feature, tool, hour, dow) from raw usage."""
    rows = db._query(
        "SELECT u.license_feature feature, j.tool tool, j.start_time start, "
        "       u.license_wait_sec wait FROM job_license_usage u "
        "JOIN jobs j ON u.job_id=j.job_id WHERE u.license_wait_sec IS NOT NULL"
    )
    buckets = defaultdict(list)
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["start"]) if r["start"] else datetime.now()
        except (ValueError, TypeError):
            dt = datetime.now()
        key = (r["feature"], r["tool"], dt.hour, dt.weekday())
        buckets[key].append(r["wait"])
    for (feature, tool, hour, dow), waits in buckets.items():
        db.upsert_license_wait_stat({
            "feature": feature, "tool": tool, "hour_of_day": hour, "day_of_week": dow,
            "sample_count": len(waits), "avg_wait_sec": sum(waits) / len(waits),
            "p50_wait_sec": _percentile(waits, 0.5), "p90_wait_sec": _percentile(waits, 0.9),
            "max_wait_sec": max(waits),
        })
    return len(buckets)


def nightly_retrain(db, cfg):
    """Full nightly cycle. Returns a summary dict for logging."""
    retention = cfg.getint("database", "retention_days", 7)
    purged = db.purge_old_raw(retention)
    lic_buckets = update_license_wait_stats(db)
    min_samples = cfg.getint("prediction", "min_samples_for_ml", 10)
    train_result = train.train_all(db, cfg, min_samples=min_samples)
    return {
        "purged_raw": purged,
        "license_buckets": lic_buckets,
        "train": train_result,
        "ran_at": datetime.now().isoformat(timespec="seconds"),
    }
