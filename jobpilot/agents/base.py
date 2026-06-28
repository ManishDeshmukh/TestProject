"""BaseAgent — the one interface every agent implements (P9).

Agents are stateless workers.  All state lives in the database.  Agents never
call each other — only the Coordinator calls agents.  Each agent can therefore
be unit-tested in isolation.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


def ok(result=None):
    return {"status": "ok", "result": result, "error": None}


def err(message, result=None):
    return {"status": "error", "result": result, "error": str(message)}


class BaseAgent(ABC):
    def __init__(self, db_agent, config):
        self.db = db_agent          # the Database Agent (single DB gateway)
        self.cfg = config
        self.name = self.__class__.__name__

    @abstractmethod
    def execute(self, action: str, payload: dict) -> dict:
        """Return {"status":"ok"|"error","result":Any,"error":str|None}."""

    # ── observability ─────────────────────────────────────────────────────────
    def log_event(self, status, action, job_id=None, latency_ms=0, error=None):
        try:
            self.db.write_agent_event({
                "agent_name": self.name, "status": status, "action": action,
                "job_id": job_id, "latency_ms": latency_ms, "error_msg": error,
            })
        except Exception:
            pass  # observability must never break the agent

    def timed(self, action, fn, job_id=None):
        """Run fn(), record latency + ok/error agent_event, return the result dict."""
        start = time.time()
        try:
            res = fn()
            self.log_event("ok", action, job_id, int((time.time() - start) * 1000))
            return res
        except Exception as e:  # noqa: BLE001 - agents must not crash the coordinator
            self.log_event("error", action, job_id, int((time.time() - start) * 1000), str(e))
            return err(e)
