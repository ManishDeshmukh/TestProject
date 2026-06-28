"""Analysis Agent — root cause analysis of failed jobs (advisory only, P3).

Primary path: Ollama LLM (``qwen2.5-coder:32b``) returning structured JSON.
Without Ollama it falls back to a deterministic rule engine keyed on
``term_signal`` / ``exit_code`` so the closed loop still functions air-gapped.
The agent NEVER restarts a job itself — it only returns a recommendation.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error

from .base import BaseAgent, ok
from ..constants import FAILURE_RULES, detect_tool

ANALYSIS_PROMPT = """You are an expert LSF job failure analyst in a VLSI EDA environment.
Analyze the failed job and return ONLY valid JSON. No other text. No markdown.

## Extracted Job Features
{features_json}

## License Data (if available)
{license_json}

## Similar Past Failures (same tool+queue, last 5)
{similar_jobs_json}

## Raw bjobs -l Output
{bjobs_output}

Return EXACTLY this JSON schema:
{{"root_cause":"...","error_category":"RESOURCE_LIMIT|ENVIRONMENT|TOOL_CRASH|CONSTRAINT_ERROR|CLUSTER_ISSUE|LICENSE_WAIT|USER_ERROR","confidence":"HIGH|MEDIUM|LOW","evidence":["..."],"recommended_fix":"...","restart_advised":true,"restart_params":{{"memory_mb":null,"runtime_sec":null,"queue":null,"extra_flags":null}},"license_contributed":false,"pattern_note":"..."}}
"""


class OllamaClient:
    """Minimal stdlib Ollama REST client (no SDK dependency required)."""

    def __init__(self, url, timeout=60):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def available(self):
        try:
            urllib.request.urlopen(f"{self.url}/api/tags", timeout=2)
            return True
        except Exception:
            return False

    def generate(self, model, prompt, temperature=0.1, max_tokens=1024):
        body = json.dumps({
            "model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }).encode()
        req = urllib.request.Request(
            f"{self.url}/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode())
        return data.get("response", "")


class AnalysisAgent(BaseAgent):
    def __init__(self, db_agent, config, ollama=None):
        super().__init__(db_agent, config)
        url = config.get("llm", "ollama_url", "http://localhost:11434")
        timeout = config.getint("llm", "timeout_analysis_sec", 60)
        self.ollama = ollama or OllamaClient(url, timeout)
        self.model = config.get("llm", "model_analysis", "qwen2.5-coder:32b")
        self.fallback_model = config.get("llm", "model_fallback", "qwen2.5-coder:7b")
        self.temperature = config.getfloat("llm", "temperature_analysis", 0.1)

    def execute(self, action: str, payload: dict) -> dict:
        if action == "analyze":
            return self.analyze(payload["job"], payload.get("license_data"))
        return ok(None)

    # ── public ────────────────────────────────────────────────────────────────
    def analyze(self, job: dict, license_data=None):
        result = self._rule_based(job, license_data)  # always have a baseline
        if self.ollama.available():
            llm = self._llm_analyze(job, license_data)
            if llm:
                result = llm
        self._persist(job["job_id"], result)
        self.log_event("ok", f"analyze:{result['error_category']}", job["job_id"])
        return ok(result)

    # ── rule engine (air-gap fallback, also a guard on LLM advice) ────────────
    def _rule_based(self, job, license_data):
        term = job.get("term_signal")
        exit_code = job.get("exit_code")
        restart_count = job.get("restart_count", 0)
        key = None
        if term in ("TERM_MEMLIMIT", "TERM_RUNLIMIT"):
            key = term
        elif exit_code == 137:
            key = "EXIT_137"
        elif exit_code == 127:
            key = "EXIT_127"
        elif exit_code == 134:
            key = "EXIT_134"
        elif not job.get("start_time"):
            key = "NO_START"
        # LICENSE_WAIT heuristic: long pend with license contribution.
        if license_data and license_data.get("license_wait_sec", 0) > 1800 and key is None:
            key = "LICENSE_WAIT"

        rule = FAILURE_RULES.get(key, {"category": "TOOL_CRASH", "restart": "first_only", "scale": {}})
        restart = rule["restart"]
        if restart == "first_only":
            restart = restart_count == 0

        params = {"memory_mb": None, "runtime_sec": None, "queue": None, "extra_flags": None}
        scale = rule.get("scale", {})
        if "mem" in scale and job.get("mem_requested_mb"):
            params["memory_mb"] = int(job["mem_requested_mb"] * scale["mem"])
        if "rt" in scale and job.get("runtime_sec"):
            params["runtime_sec"] = int(job["runtime_sec"] * scale["rt"])
        if rule["category"] == "CLUSTER_ISSUE" and job.get("host"):
            params["extra_flags"] = f'-R "select[hname!={job["host"]}]"'

        tool, _, _ = detect_tool(job.get("command", ""))
        evidence = [f"term_signal={term}", f"exit_code={exit_code}", f"tool={tool}"]
        return {
            "root_cause": self._describe(rule["category"], term, exit_code),
            "error_category": rule["category"],
            "confidence": "MEDIUM",
            "evidence": evidence,
            "recommended_fix": self._fix_text(rule["category"], params),
            "restart_advised": bool(restart),
            "restart_params": params,
            "license_contributed": bool(license_data and license_data.get("license_wait_sec", 0) > 600),
            "pattern_note": "rule-based analysis (Ollama unavailable)",
            "llm_model": "rule_engine",
        }

    @staticmethod
    def _describe(category, term, exit_code):
        return {
            "RESOURCE_LIMIT": f"Job hit a resource limit ({term or exit_code}).",
            "ENVIRONMENT": "Command/tool not found in environment (exit 127).",
            "TOOL_CRASH": f"Tool crashed (exit {exit_code}).",
            "CLUSTER_ISSUE": "Job never started — likely a cluster/host issue.",
            "LICENSE_WAIT": "Excessive license wait dominated turnaround time.",
        }.get(category, "Job failed; see evidence.")

    @staticmethod
    def _fix_text(category, params):
        if category == "RESOURCE_LIMIT" and params["memory_mb"]:
            return f"Resubmit with memory_mb={params['memory_mb']}."
        if category == "RESOURCE_LIMIT" and params["runtime_sec"]:
            return f"Resubmit with runtime_sec={params['runtime_sec']}."
        if category == "ENVIRONMENT":
            return "Fix tool path / module load; do not auto-restart."
        if category == "CLUSTER_ISSUE":
            return "Resubmit excluding the failed host."
        if category == "LICENSE_WAIT":
            return "Resubmit during low-contention hours; extend runtime."
        return "Inspect tool logs; restart once if transient."

    # ── LLM path ──────────────────────────────────────────────────────────────
    def _llm_analyze(self, job, license_data):
        similar = self.db.get_similar_jobs(job.get("tool"), job.get("queue"), limit=5)
        prompt = ANALYSIS_PROMPT.format(
            features_json=json.dumps({k: job.get(k) for k in (
                "tool", "flow_stage", "queue", "exit_code", "term_signal",
                "mem_requested_mb", "mem_peak_mb", "runtime_sec")}),
            license_json=json.dumps(license_data or {}),
            similar_jobs_json=json.dumps([{k: s.get(k) for k in ("job_id", "term_signal", "exit_code")} for s in similar]),
            bjobs_output=(job.get("raw_bjobs") or "")[:4000],
        )
        for attempt, model in enumerate((self.model, self.model, self.fallback_model)):
            try:
                raw = self.ollama.generate(model, prompt, self.temperature, 1024)
                parsed = self._extract_json(raw)
                if parsed:
                    parsed.setdefault("restart_params", {})
                    parsed["llm_model"] = model
                    # Coordinator applies hard rules later (P3) — we just supply advice.
                    return parsed
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                continue
        return None

    @staticmethod
    def _extract_json(text):
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

    def _persist(self, job_id, result):
        self.db.write_analysis({
            "job_id": job_id,
            "root_cause": result.get("root_cause"),
            "error_category": result.get("error_category"),
            "confidence": result.get("confidence"),
            "evidence": result.get("evidence"),
            "recommended_fix": result.get("recommended_fix"),
            "restart_advised": 1 if result.get("restart_advised") else 0,
            "restart_params": result.get("restart_params"),
            "license_contributed": 1 if result.get("license_contributed") else 0,
            "llm_model": result.get("llm_model"),
            "tokens_used": result.get("tokens_used", 0),
        })
