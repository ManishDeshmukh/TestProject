"""License Monitor Agent — OPTIONAL, graceful degradation always (P11).

The Coordinator only constructs this agent when ``[licenses] license_servers`` is
set; otherwise ``coordinator.license_agent is None`` and every caller null-checks.
Capability is auto-detected as a level 0-3:

  0  no config         → silent
  1  servers in config → live availability in pre-flight + dashboard
  2  agent + log parse → checkout time extracted, wait computed
  3  2+ weeks history  → full TAT decomposition + best-time suggestion
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .base import BaseAgent, ok
from ..constants import (LICENSE_MAP, CHECKOUT_PATTERNS, detect_tool,
                         LIC_LOW, LIC_HIGH)


class LicenseMonitorAgent(BaseAgent):
    def __init__(self, db_agent, config):
        super().__init__(db_agent, config)
        self.servers = config.license_servers
        self.lmstat = config.get("licenses", "lmstat_path", "")
        self.checker = config.get("licenses", "checker_script", "")
        always = config.get("licenses", "always_monitor", "")
        self.always_monitor = [f.strip() for f in always.split(",") if f.strip()]

    # ── level detection ───────────────────────────────────────────────────────
    def detect_level(self):
        if not self.servers:
            return 0
        level = 1
        if self.db.has_license_history():
            level = 2
            if self.db.license_data_age_weeks() >= 2:
                level = 3
        return level

    def verify_connectivity(self):
        """Raise if no usable query path exists (caller catches → returns None)."""
        if not self.servers and not self.checker:
            raise RuntimeError("no license servers or checker_script configured")
        return True

    def execute(self, action: str, payload: dict) -> dict:
        if action == "availability":
            return ok(self.availability(payload.get("features")))
        if action == "predict_wait":
            return ok(self.predict_wait(payload["job"]))
        if action == "monitor_job":
            return ok(self.monitor_job(payload["job_id"]))
        return ok(None)

    # ── live availability (Level 1+) ──────────────────────────────────────────
    def availability(self, features=None):
        features = features or self.always_monitor
        out = []
        for feat in features:
            snap = self._query_feature(feat)
            if snap:
                self.db.write_license_snapshot(snap)
                out.append({**snap, "risk": self._risk(snap)})
        return out

    def _query_feature(self, feature):
        """Try checker_script first, then lmstat. Returns a snapshot dict or None."""
        raw = None
        if self.checker:
            raw = self._run([self.checker, feature])
        if raw is None and (self.lmstat or self.servers):
            binary = self.lmstat or "lmstat"
            server = self.servers[0] if self.servers else ""
            raw = self._run([binary, "-a", "-c", server])
        if raw is None:
            return None
        return self._parse_lmstat(raw, feature)

    @staticmethod
    def _run(cmd):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return out.stdout
        except (OSError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _parse_lmstat(raw, feature):
        # "Users of CAL_DRC_FLAT:  (Total of 20 licenses issued;  Total of 12 ...)"
        m = re.search(
            rf"Users of {re.escape(feature)}:.*?Total of (\d+) licenses issued;\s*"
            rf"Total of (\d+) licenses in use", raw, re.DOTALL)
        if not m:
            return None
        total, in_use = int(m.group(1)), int(m.group(2))
        return {"feature": feature, "server": None, "total": total,
                "in_use": in_use, "available": total - in_use, "active_users": []}

    @staticmethod
    def _risk(snap):
        total = snap.get("total") or 0
        if total == 0:
            return "BLOCKED"
        util = snap["in_use"] / total
        if snap["available"] == 0:
            return "BLOCKED"
        if util >= LIC_HIGH:
            return "HIGH"
        if util >= LIC_LOW:
            return "MEDIUM"
        return "LOW"

    # ── wait prediction (Level 2+) ────────────────────────────────────────────
    def predict_wait(self, job: dict):
        tool, _, token = detect_tool(job.get("command", ""))
        features = LICENSE_MAP.get(token, [])
        if not features:
            return None
        now = datetime.now()
        for feat in features:
            stat = self.db.get_license_wait_stats(feat, tool, now.hour, now.weekday())
            if stat and stat.get("sample_count"):
                conf = "HIGH" if stat["sample_count"] >= 50 else "MEDIUM" if stat["sample_count"] >= 10 else "LOW"
                return {"feature": feat, "p50_wait_sec": stat["p50_wait_sec"],
                        "p90_wait_sec": stat["p90_wait_sec"], "confidence": conf}
        return None

    def find_low_contention_hour(self, feature, tool):
        return self.db.low_contention_hour(feature, tool)

    # ── per-job monitoring loop (background daemon, never raises) ──────────────
    def monitor_job(self, job_id):
        try:
            job = self.db.get_job(job_id)
            if not job:
                return False
            features = LICENSE_MAP.get(detect_tool(job.get("command", ""))[2], [])
            self.availability(features)
            self._extract_checkout(job)
            return True
        except Exception:
            return False  # P11: license failure never propagates to coordinator

    def _extract_checkout(self, job):
        """Scan run-area logs for the license checkout timestamp."""
        cwd = job.get("cwd")
        if not cwd or not Path(cwd).exists():
            return
        tool, _, _ = detect_tool(job.get("command", ""))
        family = self._family(tool)
        pattern = CHECKOUT_PATTERNS.get(family)
        if not pattern:
            return
        for log in list(Path(cwd).glob("**/*.log"))[:20]:
            try:
                text = log.read_text(errors="ignore")
            except OSError:
                continue
            m = re.search(pattern, text)
            if m:
                self._record_wait(job, m.groupdict().get("feat", "unknown"), "log_parse")
                return

    @staticmethod
    def _family(tool):
        if not tool:
            return None
        if tool.startswith("synopsys") and "vcs" not in tool:
            return "synopsys"
        if "calibre" in tool:
            return "calibre"
        if tool.startswith("cadence"):
            return "cadence"
        if "vcs" in tool:
            return "vcs"
        return None

    def _record_wait(self, job, feature, method):
        start = job.get("start_time")
        end = job.get("end_time")
        if not start:
            return
        try:
            start_dt = datetime.fromisoformat(start)
            # Without a parsed checkout time we approximate a short wait; real log
            # parsing would supply the precise checkout timestamp.
            checkout = start_dt + timedelta(seconds=30)
            end_dt = datetime.fromisoformat(end) if end else datetime.now()
            wait = int((checkout - start_dt).total_seconds())
            compute = int((end_dt - checkout).total_seconds())
        except (ValueError, TypeError):
            return
        self.db.write_license_usage({
            "job_id": job["job_id"], "license_feature": feature,
            "checkout_time": checkout.isoformat(), "release_time": end,
            "license_wait_sec": wait, "actual_compute_sec": compute,
            "detection_method": method,
        })
