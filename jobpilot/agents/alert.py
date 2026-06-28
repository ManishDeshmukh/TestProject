"""Alert Agent — notify the user. Air-gap safe: terminal + log file only.

Uses ``rich`` for colour when available, plain text otherwise.  Every alert is
also appended to ``$log_path/alerts.log`` (never rotated — audit trail).
"""

from __future__ import annotations

from datetime import datetime

from .base import BaseAgent, ok

try:
    from rich.console import Console  # type: ignore
    _CONSOLE = Console()
except ImportError:
    _CONSOLE = None

SEVERITY = {
    "JOB_FAILED_NO_RESTART": "CRITICAL",
    "RESTART_BUDGET_EXHAUSTED": "CRITICAL",
    "EARLY_WARNING_CRITICAL": "HIGH",
    "LICENSE_BLOCKED": "HIGH",
    "LICENSE_HIGH_CONTENTION": "MEDIUM",
    "DISK_THRESHOLD_85": "HIGH",
    "QUEUE_OVERLOADED_90": "HIGH",
    "LONG_PEND_2X": "MEDIUM",
    "WRAPPER_FAIL_RATE_50": "HIGH",
    "SYSTEM_FAIL_RATE_20": "CRITICAL",
    "PREDICTION_ACCURACY_DROP": "MEDIUM",
}

_COLOR = {"CRITICAL": "bold red", "HIGH": "bold yellow", "MEDIUM": "cyan"}


class AlertAgent(BaseAgent):
    def execute(self, action: str, payload: dict) -> dict:
        if action == "alert":
            return self.send(payload["trigger"], payload.get("message", ""),
                             payload.get("job_id"))
        return ok(None)

    def send(self, trigger, message, job_id=None):
        severity = SEVERITY.get(trigger, "MEDIUM")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {severity:8s} {trigger}: {message}" + (f" (job {job_id})" if job_id else "")

        if self.cfg.getbool("alerts", "terminal_output", True):
            if _CONSOLE:
                _CONSOLE.print(line, style=_COLOR.get(severity, "white"))
            else:
                print(line)

        try:
            with open(self.cfg.log_path / "alerts.log", "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

        self.log_event("ok", f"alert:{trigger}", job_id)
        return ok({"severity": severity, "trigger": trigger})
