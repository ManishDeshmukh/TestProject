"""NLP Chatbot Agent — conversational interface for job queries and actions.

Pipeline: input → rule-based intent classifier (NO LLM, deterministic) → regex
entity extraction → DB query / action → optional Ollama phrasing.  Action intents
require a "yes" confirmation; new job submission is blocked (use ``jp submit``).
All actions are logged to ``coordinator_events`` with ``source='chatbot'``.
"""

from __future__ import annotations

import re

from .base import BaseAgent, ok
from ..constants import INTENTS, CONFIRM_INTENTS, TOOL_PATTERNS, detect_tool
from .analysis import OllamaClient


class ChatbotAgent(BaseAgent):
    def __init__(self, db_agent, config, coordinator=None, ollama=None):
        super().__init__(db_agent, config)
        self.coordinator = coordinator
        url = config.get("llm", "ollama_url", "http://localhost:11434")
        self.ollama = ollama or OllamaClient(url, config.getint("llm", "timeout_chat_sec", 30))
        self.model = config.get("llm", "model_chat", "qwen3:8b")
        self.temperature = config.getfloat("llm", "temperature_chat", 0.4)
        self._pending = {}   # session_id -> (intent, entities) awaiting confirmation
        self._history = {}   # session_id -> list[(role, text)]

    def execute(self, action: str, payload: dict) -> dict:
        if action == "chat":
            return ok(self.respond(payload["text"], payload.get("session", "default")))
        if action == "classify":
            return ok({"intent": self.classify(payload["text"])})
        return ok(None)

    # ── intent classification (regex only) ────────────────────────────────────
    def classify(self, text: str) -> str:
        t = text.lower().strip()
        best, best_len = "unknown", 0
        for intent, phrases in INTENTS.items():
            for p in phrases:
                if p in t and len(p) > best_len:
                    best, best_len = intent, len(p)
        return best

    def extract_entities(self, text: str) -> dict:
        ent = {}
        m = re.search(r"\bjob[s]?\s+#?(\d{4,7})\b", text, re.IGNORECASE)
        if m:
            ent["job_id"] = m.group(1)
        for token in TOOL_PATTERNS:
            if token in text.lower():
                ent["tool_token"] = token
                ent["tool"] = TOOL_PATTERNS[token][0]
                break
        m = re.search(r"\bqueue[:\s]+(\w+)\b", text, re.IGNORECASE)
        if m:
            ent["queue"] = m.group(1)
        m = re.search(r"\blast\s+(\d+)\s+(hour|day|week)s?\b", text, re.IGNORECASE)
        if m:
            ent["time_range"] = (int(m.group(1)), m.group(2))
        return ent

    # ── main entry ────────────────────────────────────────────────────────────
    def respond(self, text: str, session="default"):
        history = self._history.setdefault(session, [])
        history.append(("user", text))

        # Handle a pending confirmation first.
        if session in self._pending:
            answer = self._handle_confirmation(text, session)
            history.append(("bot", answer))
            return {"text": answer, "intent": "confirmation"}

        intent = self.classify(text)
        entities = self.extract_entities(text)

        if intent in CONFIRM_INTENTS:
            self._pending[session] = (intent, entities)
            reply = self._confirm_prompt(intent, entities)
        else:
            reply = self._dispatch(intent, entities, text)

        reply = self._maybe_phrase(reply, intent)
        history.append(("bot", reply))
        if len(history) > 40:
            self._history[session] = history[-40:]
        return {"text": reply, "intent": intent, "entities": entities}

    # ── dispatch read-only intents ────────────────────────────────────────────
    def _dispatch(self, intent, ent, text):
        db = self.db
        if intent == "all_status":
            jobs = db.get_all_jobs(limit=200)
            counts = {}
            for j in jobs:
                counts[j["status"]] = counts.get(j["status"], 0) + 1
            return "Job summary: " + ", ".join(f"{k}={v}" for k, v in counts.items()) or "No jobs yet."
        if intent == "running_jobs":
            jobs = db.get_jobs_by_status("RUN")
            return self._list_jobs(jobs, "running")
        if intent == "failed_jobs":
            jobs = db.get_jobs_by_status("EXIT", "FAILED_FINAL")
            return self._list_jobs(jobs, "failed")
        if intent == "pending_jobs":
            jobs = db.get_jobs_by_status("PEND")
            return self._list_jobs(jobs, "pending")
        if intent == "job_status" or intent == "job_details":
            jid = ent.get("job_id")
            if not jid:
                return "Which job? Give me a job id, e.g. 'status of job 12345'."
            j = db.get_job(jid)
            if not j:
                return f"I don't have job {jid} in my records."
            return (f"Job {jid} ({j.get('job_name') or '-'}, {j.get('tool') or 'unknown tool'}) "
                    f"is {j['status']} in queue {j.get('queue')}. "
                    f"mem_peak={j.get('mem_peak_mb')}MB, compute={j.get('actual_compute_sec')}s, "
                    f"restarts={j.get('restart_count')}.")
        if intent in ("why_failed", "last_failure"):
            return self._why_failed(ent)
        if intent == "common_failures":
            return self._common_failures()
        if intent == "predict_tat":
            return self._predict(ent, kind="tat")
        if intent == "predict_memory":
            return self._predict(ent, kind="mem")
        if intent == "queue_load":
            return self._queue_load()
        if intent == "disk_usage":
            return self._disk_usage()
        if intent == "license_status":
            return self._license_status()
        if intent == "license_wait":
            return self._license_wait(ent)
        if intent == "job_history":
            jobs = db.get_all_jobs(limit=10)
            return self._list_jobs(jobs, "recent")
        if intent == "help":
            return ("I can answer about job status, failures (why did job X fail), "
                    "predictions (how long / how much memory), queue load, disk usage, "
                    "license status/wait, and history. I can restart/cancel jobs with "
                    "your confirmation. I cannot submit new jobs — use 'jp submit'.")
        if intent == "show_dashboard":
            port = self.cfg.getint("coordinator", "dashboard_port", 8765)
            return f"Dashboard is at http://localhost:{port} — open it in Firefox."
        return ("I didn't quite get that. Try 'help', 'running jobs', "
                "'why did job 12345 fail', or 'license status'.")

    # ── helpers ───────────────────────────────────────────────────────────────
    def _list_jobs(self, jobs, label):
        if not jobs:
            return f"No {label} jobs right now."
        head = ", ".join(f"{j['job_id']}({j.get('tool') or '?'})" for j in jobs[:8])
        more = f" and {len(jobs) - 8} more" if len(jobs) > 8 else ""
        return f"{len(jobs)} {label} job(s): {head}{more}."

    def _why_failed(self, ent):
        jid = ent.get("job_id")
        if jid:
            a = self.db.get_analysis_for(jid)
            if not a:
                return f"No analysis recorded for job {jid} yet."
        else:
            failed = self.db.get_jobs_by_status("EXIT", "FAILED_FINAL")
            if not failed:
                return "No failed jobs found."
            jid = failed[0]["job_id"]
            a = self.db.get_analysis_for(jid)
            if not a:
                return f"Job {jid} failed but no RCA is recorded yet."
        return (f"Job {jid}: {a.get('root_cause')} [{a.get('error_category')}, "
                f"{a.get('confidence')} confidence]. Fix: {a.get('recommended_fix')}")

    def _common_failures(self):
        rows = self.db._query(
            "SELECT error_category, COUNT(*) c FROM job_analysis GROUP BY error_category ORDER BY c DESC LIMIT 5"
        )
        if not rows:
            return "No failure patterns recorded yet."
        return "Most common failure categories: " + ", ".join(f"{r['error_category']}({r['c']})" for r in rows)

    def _predict(self, ent, kind):
        if not self.coordinator:
            return "Prediction engine is not attached in this context."
        tool = ent.get("tool")
        if not tool:
            return "Tell me the tool, e.g. 'how long will a calibre job take?'"
        token = ent.get("tool_token") or next((k for k, v in TOOL_PATTERNS.items() if v[0] == tool), "")
        job = {"command": token, "tool": tool, "queue": ent.get("queue", "normal")}
        pred = self.coordinator.prediction.predict_resources(job, self.coordinator.license_agent)
        if kind == "mem":
            return f"Predicted memory for a {tool} job: ~{pred['predicted_mem_mb']}MB ({pred['confidence']} confidence)."
        extra = ""
        if pred["includes_license_wait"]:
            extra = f" (compute ~{pred['predicted_compute_min']}min + license ~{pred['predicted_license_wait_p50']}min)"
        return f"Predicted TAT for a {tool} job: ~{pred['predicted_total_p50_min']}min{extra} ({pred['confidence']} confidence)."

    def _queue_load(self):
        rows = self.db.latest_queues(5)
        if not rows:
            return "No queue load data yet."
        return "Queue load: " + ", ".join(f"{r['name']} {r['load_pct']:.0f}%" for r in rows)

    def _disk_usage(self):
        rows = self.db.latest_disks(5)
        if not rows:
            return "No disk data yet."
        return "Disk usage: " + ", ".join(f"{r['mount']} {r['util_pct']:.0f}%" for r in rows)

    def _license_status(self):
        if not (self.coordinator and self.coordinator.license_agent):
            return "License monitoring is not configured (Level 0)."
        rows = self.db.latest_license_snapshots(5)
        if not rows:
            return "License agent is active but no snapshots are available yet."
        return "License status: " + ", ".join(
            f"{r['feature']} {r['available']}/{r['total']} free" for r in rows)

    def _license_wait(self, ent):
        if not (self.coordinator and self.coordinator.license_agent):
            return "License wait prediction needs license monitoring configured (Level 2+)."
        tool = ent.get("tool")
        if not tool:
            return "Which tool's license? e.g. 'license wait for calibre'."
        token = next((k for k, v in TOOL_PATTERNS.items() if v[0] == tool), "")
        pred = self.coordinator.license_agent.predict_wait({"command": token})
        if not pred:
            return f"Not enough license-wait history for {tool} yet."
        return (f"{pred['feature']} wait: P50 {pred['p50_wait_sec']/60:.0f}min, "
                f"P90 {pred['p90_wait_sec']/60:.0f}min ({pred['confidence']}).")

    # ── confirmation flow for actions ─────────────────────────────────────────
    def _confirm_prompt(self, intent, ent):
        if intent == "restart_job":
            return f"Restart job {ent.get('job_id', '?')}? Reply 'yes' to confirm."
        if intent == "cancel_job":
            return f"Cancel (bkill) job {ent.get('job_id', '?')}? Reply 'yes' to confirm."
        if intent == "cancel_all_pend":
            return "Cancel ALL pending jobs? Reply 'yes' to confirm."
        return "Confirm? (yes/no)"

    def _handle_confirmation(self, text, session):
        intent, ent = self._pending.pop(session)
        if text.strip().lower() not in ("yes", "y", "confirm"):
            return "Okay, cancelled — no action taken."
        return self._perform_action(intent, ent)

    def _perform_action(self, intent, ent):
        self.db.write_coordinator_event(
            ent.get("job_id"), f"CHATBOT_{intent.upper()}", "ChatbotAgent",
            payload=ent, notes="source=chatbot")
        if not self.coordinator:
            return "Action logged, but coordinator is not attached to execute it."
        if intent == "cancel_job" and ent.get("job_id"):
            self.coordinator.lsf.kill(ent["job_id"])
            self.db.update_job_status(ent["job_id"], "EXIT")
            return f"Job {ent['job_id']} cancelled."
        if intent == "cancel_all_pend":
            pend = self.db.get_jobs_by_status("PEND")
            for j in pend:
                self.coordinator.lsf.kill(j["job_id"])
                self.db.update_job_status(j["job_id"], "EXIT")
            return f"Cancelled {len(pend)} pending job(s)."
        if intent == "restart_job" and ent.get("job_id"):
            job = self.db.get_job(ent["job_id"])
            if not job:
                return f"Job {ent['job_id']} not found."
            analysis = self.db.get_analysis_for(ent["job_id"]) or {"restart_params": {}}
            res = self.coordinator.restart.restart(job, analysis)
            r = res.get("result", {})
            return (f"Restarted → job {r.get('new_job_id')}." if r.get("restarted")
                    else f"Restart blocked: {r.get('reason')}.")
        return "Nothing to do."

    # ── optional LLM phrasing ─────────────────────────────────────────────────
    def _maybe_phrase(self, factual, intent):
        """Keep factual answer verbatim; LLM only smooths phrasing when available.

        Streaming token-by-token happens in the WebSocket layer; here we return
        the complete string. We deliberately keep the DB facts authoritative.
        """
        return factual
