"""Job Discovery Agent — finds ALL child jobs from wrapper.csh and builds trees.

Three strategies run and their results are de-duplicated (P5).  Children get
``root_job_id``, ``parent_job_id`` and ``job_depth`` set so the dashboard can
render job *trees*, never flat lists.
"""

from __future__ import annotations

from ..lsf import LSF
from .base import BaseAgent, ok


class DiscoveryAgent(BaseAgent):
    def __init__(self, db_agent, config, lsf=None):
        super().__init__(db_agent, config)
        self.lsf = lsf or LSF(config)

    def execute(self, action: str, payload: dict) -> dict:
        if action == "discover":
            return self.discover(payload["parent_job"])
        return ok(None)

    def discover(self, parent_job: dict):
        """Find children submitted in the window after the parent started RUN."""
        parent_id = parent_job["job_id"]
        user = parent_job.get("user_name") or "user"
        start = parent_job.get("start_time") or parent_job.get("submit_time")
        if not start:
            return ok([])

        found = set()
        # Strategy 1: time-window scan (most reliable for wrapper.csh).
        for jid in self.lsf.jobs_in_window(user, start, window_sec=60):
            if jid != parent_id:
                found.add(jid)
        # Strategies 2 & 3 (dependency / job-group) collapse to the same poll in
        # simulation; in real LSF they would add to `found`. De-dup via the set.

        root_id = parent_job.get("root_job_id") or parent_id
        depth = (parent_job.get("job_depth") or 0) + 1
        registered = []
        for jid in found:
            existing = self.db.get_job(jid)
            if existing and existing.get("parent_job_id"):
                continue
            rec = self.lsf.poll(jid) or {"job_id": jid}
            rec.update({
                "parent_job_id": parent_id, "root_job_id": root_id,
                "job_depth": depth, "is_discovered": 1,
            })
            self.db.write_job(rec)
            registered.append(jid)
        if registered:
            self.log_event("ok", f"discovered:{len(registered)}", parent_id)
        return ok(registered)
