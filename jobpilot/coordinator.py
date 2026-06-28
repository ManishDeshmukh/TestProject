"""Coordinator — global job state machine. Makes ALL decisions (P3).

Owns the audit trail, routes between agents (agents never call each other), and
runs the background daemons: per-job monitoring, early warning, discovery,
resource/disk/license snapshots, and the DB sync thread.  The LLM is advisory:
the coordinator applies the loop guard and hard rules before any restart.
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time
from datetime import datetime

from .config import Config
from .lsf import LSF
from .constants import detect_tool, license_features_for
from .agents.database import DatabaseAgent
from .agents.monitor import MonitorAgent
from .agents.discovery import DiscoveryAgent
from .agents.earlywarning import EarlyWarningAgent
from .agents.analysis import AnalysisAgent
from .agents.prediction import PredictionAgent
from .agents.restart import RestartAgent
from .agents.alert import AlertAgent
from .agents.license import LicenseMonitorAgent
from .agents.chatbot import ChatbotAgent

try:
    import psutil  # type: ignore
    _HAVE_PSUTIL = True
except ImportError:
    _HAVE_PSUTIL = False


class Coordinator:
    def __init__(self, config: Config | None = None):
        self.cfg = config or Config()
        self.user = os.environ.get("USER", "user")
        self.host = socket.gethostname()
        self.pid = os.getpid()

        self.lsf = LSF(self.cfg)
        # Database Agent is the shared gateway; all other agents take it as self.db.
        self.db = DatabaseAgent(self.cfg, self.user, self.host)
        self.monitor = MonitorAgent(self.db, self.cfg, self.lsf)
        self.discovery = DiscoveryAgent(self.db, self.cfg, self.lsf)
        self.early_warning = EarlyWarningAgent(self.db, self.cfg)
        self.analysis = AnalysisAgent(self.db, self.cfg)
        self.prediction = PredictionAgent(self.db, self.cfg)
        self.restart = RestartAgent(self.db, self.cfg, self.lsf)
        self.alert = AlertAgent(self.db, self.cfg)
        self.license_agent = self._init_license_agent()
        self.license_level = self._detect_license_level()
        self.chatbot = ChatbotAgent(self.db, self.cfg, coordinator=self)

        self._stop = threading.Event()
        self._threads = []
        self._monitored = set()
        self._monitored_lock = threading.Lock()

    # ── license init (P11) ────────────────────────────────────────────────────
    def _init_license_agent(self):
        try:
            if not self.cfg.license_servers:
                return None  # Level 0
            agent = LicenseMonitorAgent(self.db, self.cfg)
            agent.verify_connectivity()
            return agent
        except Exception as e:  # noqa: BLE001
            self.db.write_coordinator_event(None, "LICENSE_AGENT_UNAVAILABLE",
                                            "Coordinator", notes=str(e))
            return None  # graceful — never blocks startup

    def _detect_license_level(self):
        if self.license_agent is None:
            return 0
        return self.license_agent.detect_level()

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start_background(self):
        """Start sync + snapshot collectors + monitor loop (daemon threads)."""
        self.db.start_sync()
        self._spawn(self._snapshot_loop, "snapshots")
        self._spawn(self._monitor_loop, "monitor")
        # Resume any jobs already in flight.
        for j in self.db.get_jobs_by_status("PEND", "RUN"):
            self.track(j["job_id"])
        self.db.write_coordinator_event(None, "COORDINATOR_START", "Coordinator",
                                        notes=f"license_level={self.license_level}")

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self):
        self._stop.set()
        self.db.stop()

    def status_banner(self):
        port = self.cfg.getint("coordinator", "dashboard_port", 8765)
        mode = "SIMULATED LSF" if self.lsf.simulated else "LSF"
        return (f"JobPilot started. Dashboard: http://localhost:{port}\n"
                f"License level: {self.license_level} | Data: {self.cfg.data_path} | "
                f"Mode: {mode} | PID: {self.pid}")

    # ── submission + pre-flight ───────────────────────────────────────────────
    def preflight(self, command, queue, mem_mb=None):
        """Predict resources + license risk before submit."""
        tool, stage, _ = detect_tool(command)
        job = {"command": command, "queue": queue, "tool": tool, "flow_stage": stage,
               "mem_requested_mb": mem_mb, "submit_time": datetime.now().isoformat()}
        pred = self.prediction.predict_resources(job, self.license_agent)
        out = {"tool": tool, "flow_stage": stage, "prediction": pred, "license": []}
        if self.license_agent:
            features = license_features_for(command)
            out["license"] = self.license_agent.availability(features)
        return out

    def submit_job(self, name, queue, command, mem_mb=None, runtime_sec=None, extra_flags=""):
        """Run pre-flight, log bsub (P10), submit, persist, and start tracking."""
        pf = self.preflight(command, queue, mem_mb)
        tool, stage, _ = detect_tool(command)
        if mem_mb is None:
            mem_mb = pf["prediction"]["predicted_mem_mb"]

        # P10: log bsub BEFORE execution.
        self.db.write_coordinator_event(
            None, "BSUB_SUBMIT", "Coordinator",
            payload={"name": name, "queue": queue, "mem_mb": mem_mb,
                     "runtime_sec": runtime_sec, "command": command},
            notes="initial submission")

        job_id = self.lsf.submit(name, queue, command, mem_mb=mem_mb,
                                 runtime_sec=runtime_sec, extra_flags=extra_flags,
                                 user=self.user, host="-")
        if not job_id:
            return None, pf
        self.db.write_job({
            "job_id": job_id, "job_name": name, "status": "PEND", "queue": queue,
            "user_name": self.user, "command": command, "tool": tool, "flow_stage": stage,
            "mem_requested_mb": mem_mb, "runtime_sec": runtime_sec,
            "has_spef": 1 if ".spef" in command else 0,
            "root_job_id": job_id, "job_depth": 0,
            "submit_time": datetime.now().isoformat(timespec="seconds"),
        })
        self.track(job_id)
        return job_id, pf

    # ── monitoring ────────────────────────────────────────────────────────────
    def track(self, job_id):
        with self._monitored_lock:
            self._monitored.add(job_id)

    def _monitor_loop(self):
        run_int = self.cfg.getint("coordinator", "poll_interval_run", 30)
        # In simulation the lifecycle is compressed; poll fast so demos are snappy.
        tick = 2 if self.lsf.simulated else min(run_int, 30)
        while not self._stop.wait(tick):
            with self._monitored_lock:
                ids = list(self._monitored)
            for jid in ids:
                try:
                    self._poll_one(jid)
                except Exception as e:  # noqa: BLE001
                    self.db.write_coordinator_event(jid, "MONITOR_ERROR", "Coordinator", notes=str(e))

    def _poll_one(self, job_id):
        res = self.monitor.poll(job_id)
        if res["status"] != "ok":
            return
        rec = res["result"]
        status = rec["status"]
        prev = self.db.get_job(job_id)
        prev_status = prev["status"] if prev else None

        # Persist the freshest known fields.
        self.db.write_job({k: rec[k] for k in rec if rec[k] is not None and k in self.db.local.JOB_COLS})

        if status == "RUN":
            if prev_status != "RUN":
                self._on_run(rec)
            else:
                self._on_run_tick(rec)
        elif status == "DONE":
            self._on_done(rec)
            self._untrack(job_id)
        elif status == "EXIT":
            self._on_exit(rec)
            self._untrack(job_id)

    def _untrack(self, job_id):
        with self._monitored_lock:
            self._monitored.discard(job_id)

    # ── state transitions ─────────────────────────────────────────────────────
    def _on_run(self, rec):
        job_id = rec["job_id"]
        self.db.update_job_status(job_id, "RUN")
        # start discovery watcher (wrapper children)
        self._spawn(lambda: self.discovery.discover(rec), f"discover-{job_id}")
        # license monitor thread (null-safe, P11)
        if self.license_agent:
            self._spawn(lambda: self.license_agent.monitor_job(job_id), f"lic-{job_id}")

    def _on_run_tick(self, rec):
        # Early warning assessment each RUN tick.
        res = self.early_warning.assess(rec)
        snap = res.get("result") or {}
        if snap.get("risk_level") == "CRITICAL":
            self.db.update_job_status(rec["job_id"], "WARNED")
            self.alert.send("EARLY_WARNING_CRITICAL",
                            f"{rec.get('tool')} mem {snap.get('mem_util_pct')}% "
                            f"ETA {snap.get('eta_to_limit_min')}min", rec["job_id"])

    def _on_done(self, rec):
        job_id = rec["job_id"]
        # record actuals; compute actual_compute_sec (license wait excluded)
        compute = rec.get("runtime_sec")
        lic = None
        if self.license_agent:
            self.license_agent.monitor_job(job_id)
            usage = self.db._query_one(
                "SELECT * FROM job_license_usage WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,))
            if usage:
                compute = usage.get("actual_compute_sec") or compute
                lic = usage
        self.db.update_job(job_id, status="DONE", actual_compute_sec=compute,
                           mem_peak_mb=rec.get("mem_peak_mb"), end_time=rec.get("end_time"),
                           sync_status="pending")
        self.prediction.record_actuals(self.db.get_job(job_id))
        self.db.write_coordinator_event(job_id, "DONE", "Coordinator",
                                        notes=f"compute={compute}s")

    def _on_exit(self, rec):
        job_id = rec["job_id"]
        self.db.update_job(job_id, status="EXIT", mem_peak_mb=rec.get("mem_peak_mb"),
                           end_time=rec.get("end_time"), exit_code=rec.get("exit_code"),
                           term_signal=rec.get("term_signal"))
        job = self.db.get_job(job_id)

        # ANALYZING → advisory RCA.
        license_data = None
        if self.license_agent:
            license_data = self.db._query_one(
                "SELECT * FROM job_license_usage WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,))
        ares = self.analysis.analyze(job, license_data)
        analysis = ares["result"]
        self._decide_restart(job, analysis)

    def _decide_restart(self, job, analysis):
        job_id = job["job_id"]
        if not analysis.get("restart_advised"):
            self.db.update_job_status(job_id, "FAILED_FINAL")
            self.alert.send("JOB_FAILED_NO_RESTART",
                            analysis.get("root_cause", "no restart advised"), job_id)
            return
        # Coordinator owns the loop guard (P4) — advisory LLM never bypasses it.
        res = self.restart.restart(job, analysis)
        r = res.get("result", {})
        if r.get("restarted"):
            new_id = r["new_job_id"]
            self.track(new_id)
            self.db.write_coordinator_event(job_id, "RESTARTED", "Coordinator",
                                            notes=f"-> {new_id}")
        else:
            self.db.update_job_status(job_id, "FAILED_BUDGET")
            self.alert.send("RESTART_BUDGET_EXHAUSTED", r.get("reason", ""), job_id)

    # ── snapshot collectors (resource/disk/license every 30s) ─────────────────
    def _snapshot_loop(self):
        while not self._stop.wait(30):
            try:
                self._collect_resource_snapshot()
                self._collect_disk_snapshot()
                if self.license_agent:
                    self.license_agent.availability()
            except Exception:
                pass

    def _collect_resource_snapshot(self):
        jobs = self.db.get_jobs_by_status("PEND", "RUN")
        by_queue = {}
        for j in jobs:
            q = j.get("queue") or "default"
            d = by_queue.setdefault(q, {"running": 0, "pending": 0})
            if j["status"] == "RUN":
                d["running"] += 1
            else:
                d["pending"] += 1
        for q, d in by_queue.items():
            total = d["running"] + d["pending"]
            load = (d["running"] / total * 100) if total else 0.0
            self.db.write_resource_snapshot({
                "queue_name": q, "running_jobs": d["running"], "pending_jobs": d["pending"],
                "load_pct": round(load, 1), "top_users": [self.user]})

    def _collect_disk_snapshot(self):
        mount = str(self.cfg.data_path)
        try:
            usage = shutil.disk_usage(mount)
            used_gb = usage.used / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            util = used_gb / total_gb * 100 if total_gb else 0
        except OSError:
            return
        self.db.write_disk_snapshot({
            "mount_point": mount, "used_gb": round(used_gb, 1),
            "total_gb": round(total_gb, 1), "util_pct": round(util, 1), "top_consumers": []})
        if util >= self.cfg.getint("alerts", "disk_warn_threshold", 85):
            self.alert.send("DISK_THRESHOLD_85", f"{mount} at {util:.0f}%")
