"""Restart Agent — resubmits with corrected params. Never *decides* restart (P3).

The loop guard (P4) is mandatory: all THREE checks must pass before any restart.
The bsub command is logged to ``coordinator_events`` BEFORE execution (P10).
"""

from __future__ import annotations

import time

from ..lsf import LSF
from .base import BaseAgent, ok, err


class RestartAgent(BaseAgent):
    def __init__(self, db_agent, config, lsf=None):
        super().__init__(db_agent, config)
        self.lsf = lsf or LSF(config)
        self._hourly = []  # timestamps of recent restarts (global rate)

    def execute(self, action: str, payload: dict) -> dict:
        if action == "restart":
            return self.restart(payload["job"], payload["analysis"])
        if action == "check_guard":
            passed, reason = self._loop_guard(payload["job"])
            return ok({"allowed": passed, "reason": reason})
        return err(f"unknown action {action}")

    # ── loop guard (P4) ───────────────────────────────────────────────────────
    def _loop_guard(self, job):
        root = job.get("root_job_id") or job["job_id"]
        max_restarts = self.cfg.getint("restart", "max_restarts_per_job", 3)
        cooldown = self.cfg.getint("restart", "cooldown_seconds", 300)
        hourly_limit = self.cfg.getint("restart", "global_hourly_limit", 50)

        # CHECK 1: lineage restart count.
        if self.db.lineage_restart_count(root) >= max_restarts:
            return False, f"lineage restart budget exhausted (>= {max_restarts})"

        # CHECK 2: cooldown since the last restart in this lineage.
        now = time.time()
        if self._cooldown_active(root, cooldown):
            return False, f"cooldown active (< {cooldown}s since last restart)"

        # CHECK 3: global hourly rate.
        self._hourly = [t for t in self._hourly if now - t < 3600]
        if len(self._hourly) >= hourly_limit:
            return False, f"global hourly restart limit reached ({hourly_limit})"

        return True, "ok"

    _last_restart = {}

    def _cooldown_active(self, root, cooldown):
        last = self._last_restart.get(root)
        return last is not None and (time.time() - last) < cooldown

    # ── restart ───────────────────────────────────────────────────────────────
    def restart(self, job: dict, analysis: dict):
        passed, reason = self._loop_guard(job)
        if not passed:
            self.log_event("ok", f"restart_blocked:{reason}", job["job_id"])
            return ok({"restarted": False, "reason": reason, "status": "FAILED_BUDGET"})

        params = analysis.get("restart_params", {}) or {}
        mem = params.get("memory_mb") or job.get("mem_requested_mb")
        runtime = params.get("runtime_sec") or job.get("runtime_sec")
        extra = params.get("extra_flags") or ""
        queue = params.get("queue") or job.get("queue")
        command = job.get("command", "")
        name = job.get("job_name") or f"jp_{job['job_id']}"
        root = job.get("root_job_id") or job["job_id"]

        # P10: log bsub BEFORE execution.
        self.db.write_coordinator_event(
            job["job_id"], "BSUB_RESTART", self.name,
            payload={"name": name, "queue": queue, "mem_mb": mem,
                     "runtime_sec": runtime, "extra": extra, "command": command},
            notes=f"restart of {job['job_id']} ({analysis.get('error_category')})",
        )

        new_id = self.lsf.submit(name, queue, command, mem_mb=mem,
                                 runtime_sec=runtime, extra_flags=extra,
                                 user=job.get("user_name"))
        if not new_id:
            return err("bsub returned no job id", result={"restarted": False})

        self._last_restart[root] = time.time()
        self._hourly.append(time.time())
        new_count = (job.get("restart_count") or 0) + 1

        self.db.write_job({
            "job_id": new_id, "job_name": name, "status": "PEND", "queue": queue,
            "user_name": job.get("user_name"), "command": command, "tool": job.get("tool"),
            "flow_stage": job.get("flow_stage"), "mem_requested_mb": mem,
            "runtime_sec": runtime, "parent_job_id": job["job_id"], "root_job_id": root,
            "restart_count": new_count, "job_depth": job.get("job_depth", 0),
        })
        self.db.update_job_status(job["job_id"], "RESTARTED")
        self.log_event("ok", f"restarted:{job['job_id']}->{new_id}", new_id)
        return ok({"restarted": True, "new_job_id": new_id, "restart_count": new_count})
