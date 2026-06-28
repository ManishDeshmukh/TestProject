"""Job Monitor Agent — polls LSF status and extracts ``bjobs -l`` on completion.

Polling cadence: PEND 120s, RUN 30s, EXIT/DONE immediate.  The Coordinator owns
the schedule; this agent just performs one poll on request and normalises the
result into the Monitor→Coordinator output contract.
"""

from __future__ import annotations

from ..lsf import LSF
from ..constants import detect_tool
from .base import BaseAgent, ok, err


class MonitorAgent(BaseAgent):
    def __init__(self, db_agent, config, lsf=None):
        super().__init__(db_agent, config)
        self.lsf = lsf or LSF(config)

    def execute(self, action: str, payload: dict) -> dict:
        if action == "poll":
            return self.poll(payload["job_id"])
        if action == "list_ids":
            return ok(self.lsf.list_ids())
        return err(f"unknown action {action}")

    def poll(self, job_id):
        rec = self.lsf.poll(job_id)
        if rec is None:
            return err(f"job {job_id} not found", result=None)
        # enrich tool/flow_stage if missing
        if not rec.get("tool"):
            tool, stage, _ = detect_tool(rec.get("command", ""))
            rec["tool"], rec["flow_stage"] = tool, stage
        self.log_event("ok", f"poll:{rec.get('status')}", job_id)
        return ok(rec)
