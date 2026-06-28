"""Early Warning Agent — in-flight risk assessment; warn before LSF kills.

Takes a memory snapshot, computes the growth slope from prior snapshots, projects
ETA to the memory limit, and classifies risk.  Writes a ``job_snapshots`` row and
returns the risk so the Coordinator can raise an alert.
"""

from __future__ import annotations

from .base import BaseAgent, ok, err
from ..constants import RISK_MEM_WARN, RISK_MEM_CRIT, RISK_ETA_CRIT_MIN


class EarlyWarningAgent(BaseAgent):
    def execute(self, action: str, payload: dict) -> dict:
        if action == "assess":
            return self.assess(payload["job"])
        return err(f"unknown action {action}")

    def assess(self, job: dict):
        job_id = job["job_id"]
        mem_req = job.get("mem_requested_mb") or 0
        mem_now = job.get("mem_used_mb") or 0
        elapsed = job.get("runtime_sec") or 0
        warn = self.cfg.getfloat("early_warning", "mem_warn_threshold", RISK_MEM_WARN)
        crit = self.cfg.getfloat("early_warning", "mem_crit_threshold", RISK_MEM_CRIT)
        eta_crit = self.cfg.getint("early_warning", "eta_crit_minutes", RISK_ETA_CRIT_MIN)

        mem_util = (mem_now / mem_req) if mem_req else 0.0

        # Slope from previous snapshots (MB/min).
        prev = self.db.get_mem_snapshots(job_id)
        slope = 0.0
        if prev:
            last = prev[-1]
            dt_min = max((elapsed - (last["elapsed_sec"] or 0)) / 60.0, 1e-6)
            slope = (mem_now - (last["mem_current_mb"] or 0)) / dt_min

        eta = None
        if slope > 0 and mem_req:
            headroom = mem_req - mem_now
            eta = max(headroom / slope, 0)  # minutes

        if mem_util > crit or (eta is not None and eta < eta_crit):
            risk = "CRITICAL"
        elif mem_util > warn:
            risk = "WARNING"
        else:
            risk = "OK"

        snap = {
            "job_id": job_id, "elapsed_sec": int(elapsed),
            "mem_current_mb": int(mem_now), "cpu_current": job.get("cpu_used") or 0.0,
            "mem_util_pct": round(mem_util * 100, 1),
            "slope_mb_per_min": round(slope, 1),
            "eta_to_limit_min": round(eta, 1) if eta is not None else None,
            "risk_level": risk,
        }
        self.db.write_snapshot(snap)
        snap["suggested_mem_mb"] = int(mem_req * 1.5) if risk == "CRITICAL" and mem_req else None
        self.log_event("ok", f"assess:{risk}", job_id)
        return ok(snap)
