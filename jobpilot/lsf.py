"""LSF interface wrapper used by the Monitor / Discovery / Restart agents.

Real mode shells out to ``bsub`` / ``bjobs`` / ``bhist`` / ``bkill``.  When LSF
is not on PATH (developer box, CI, demo) it transparently falls back to an
in-process **simulator** that advances synthetic jobs through PEND→RUN→DONE/EXIT
so the whole closed loop — monitoring, early warning, analysis, restart,
dashboard — can be exercised end-to-end without a cluster.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta

from .constants import detect_tool

LSF_AVAILABLE = shutil.which("bsub") is not None and shutil.which("bjobs") is not None


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
# Simulator — only used when real LSF is unavailable.
# ─────────────────────────────────────────────────────────────────────────────
class _SimulatedCluster:
    """A tiny deterministic-ish LSF stand-in driven by wall-clock elapsed time.

    Timings are compressed (seconds, not minutes) so a demo job completes
    quickly while still passing through every lifecycle state.
    """

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()
        self._counter = random.randint(10000, 90000)

    def submit(self, name, queue, command, mem_mb, runtime_sec, user, host):
        with self._lock:
            self._counter += 1
            jid = str(self._counter)
            tool, stage, _ = detect_tool(command)
            # ~20% of sim jobs are destined to fail, to exercise the RCA loop.
            fate = random.choices(["DONE", "EXIT"], weights=[0.8, 0.2])[0]
            pend = random.uniform(3, 8)
            run = random.uniform(8, 20)
            self._jobs[jid] = {
                "job_id": jid, "job_name": name, "queue": queue, "command": command,
                "user_name": user, "host": host, "tool": tool, "flow_stage": stage,
                "mem_requested_mb": mem_mb, "runtime_limit_sec": runtime_sec,
                "submit_epoch": time.time(), "pend_sec": pend, "run_sec": run,
                "fate": fate, "submit_time": _now_iso(),
            }
            return jid

    def _derive(self, j):
        """Compute current status + metrics from elapsed wall time."""
        elapsed = time.time() - j["submit_epoch"]
        mem_req = j["mem_requested_mb"] or 16000
        if elapsed < j["pend_sec"]:
            return {"status": "PEND", "elapsed": 0}
        run_elapsed = elapsed - j["pend_sec"]
        start_time = datetime.fromtimestamp(j["submit_epoch"] + j["pend_sec"]).isoformat(timespec="seconds")
        if run_elapsed < j["run_sec"]:
            frac = run_elapsed / j["run_sec"]
            mem = int(mem_req * (0.5 + 0.5 * frac) * (1.05 if j["fate"] == "EXIT" else 0.8))
            return {"status": "RUN", "elapsed": int(run_elapsed), "start_time": start_time,
                    "mem_used_mb": mem, "cpu_used": round(frac * 100, 1)}
        # completed
        end_time = datetime.fromtimestamp(
            j["submit_epoch"] + j["pend_sec"] + j["run_sec"]
        ).isoformat(timespec="seconds")
        if j["fate"] == "EXIT":
            peak = int(mem_req * 1.05)
            return {"status": "EXIT", "elapsed": int(j["run_sec"]), "start_time": start_time,
                    "end_time": end_time, "mem_used_mb": peak, "mem_peak_mb": peak,
                    "exit_code": 137, "term_signal": "TERM_MEMLIMIT", "cpu_used": 100.0}
        peak = int(mem_req * 0.8)
        return {"status": "DONE", "elapsed": int(j["run_sec"]), "start_time": start_time,
                "end_time": end_time, "mem_used_mb": peak, "mem_peak_mb": peak,
                "exit_code": 0, "term_signal": None, "cpu_used": 100.0}

    def poll(self, jid):
        with self._lock:
            j = self._jobs.get(jid)
            if not j:
                return None
            d = self._derive(j)
            run_sec = d.get("elapsed", 0)
            return {
                "job_id": jid, "job_name": j["job_name"], "status": d["status"],
                "queue": j["queue"], "user_name": j["user_name"], "host": j["host"],
                "command": j["command"], "tool": j["tool"], "flow_stage": j["flow_stage"],
                "mem_requested_mb": j["mem_requested_mb"], "submit_time": j["submit_time"],
                "start_time": d.get("start_time"), "end_time": d.get("end_time"),
                "runtime_sec": run_sec, "mem_used_mb": d.get("mem_used_mb"),
                "mem_peak_mb": d.get("mem_peak_mb"), "cpu_used": d.get("cpu_used"),
                "exit_code": d.get("exit_code"), "term_signal": d.get("term_signal"),
                "cwd": os.getcwd(), "raw_bjobs": f"[simulated] {j['command']}",
            }

    def all_ids(self):
        with self._lock:
            return list(self._jobs.keys())

    def kill(self, jid):
        with self._lock:
            j = self._jobs.get(jid)
            if j and j["fate"] != "EXIT":
                # force-complete as cancelled
                j["run_sec"] = 0.01
                j["fate"] = "EXIT"
            return jid in self._jobs


_SIM = _SimulatedCluster()


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────
class LSF:
    def __init__(self, config=None, force_sim=False):
        self.cfg = config
        self.simulated = force_sim or not LSF_AVAILABLE
        self.timeout = config.getint("lsf", "bjobs_timeout", 30) if config else 30

    # ── submit ────────────────────────────────────────────────────────────────
    def submit(self, name, queue, command, mem_mb=None, runtime_sec=None,
               extra_flags="", user=None, host="-"):
        user = user or os.environ.get("USER", "user")
        if self.simulated:
            return _SIM.submit(name, queue, command, mem_mb or 16000,
                               runtime_sec or 3600, user, host)
        cmd = ["bsub", "-J", name, "-q", queue]
        if mem_mb:
            cmd += ["-M", str(mem_mb)]
        if runtime_sec:
            cmd += ["-W", str(int(runtime_sec / 60))]
        if extra_flags:
            cmd += extra_flags.split()
        cmd += command.split() if isinstance(command, str) else command
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        return self._parse_job_id(out.stdout)

    @staticmethod
    def _parse_job_id(stdout):
        # bsub prints: "Job <12345> is submitted to queue <normal>."
        import re
        m = re.search(r"Job <(\d+)>", stdout or "")
        return m.group(1) if m else None

    # ── poll ──────────────────────────────────────────────────────────────────
    def poll(self, job_id):
        if self.simulated:
            return _SIM.poll(job_id)
        return self._bjobs_detail(job_id)

    def list_ids(self, user=None):
        if self.simulated:
            return _SIM.all_ids()
        try:
            out = subprocess.run(
                ["bjobs", "-noheader", "-o", "jobid"], capture_output=True, text=True,
                timeout=self.timeout,
            )
            return [l.strip() for l in out.stdout.splitlines() if l.strip()]
        except (OSError, subprocess.SubprocessError):
            return []

    def _bjobs_detail(self, job_id):
        """Parse ``bjobs -l`` into the Monitor agent output contract."""
        try:
            out = subprocess.run(
                ["bjobs", "-l", str(job_id)], capture_output=True, text=True, timeout=self.timeout
            )
            raw = out.stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if not raw.strip():
            return None
        rec = parse_bjobs_l(raw)
        rec["job_id"] = str(job_id)
        rec["raw_bjobs"] = raw
        return rec

    # ── discovery helpers ─────────────────────────────────────────────────────
    def jobs_in_window(self, user, start_iso, window_sec=60):
        """Return job_ids submitted within [start, start+window] — wrapper discovery."""
        if self.simulated:
            ids = []
            start = datetime.fromisoformat(start_iso)
            for jid in _SIM.all_ids():
                p = _SIM.poll(jid)
                if not p or not p.get("submit_time"):
                    continue
                st = datetime.fromisoformat(p["submit_time"])
                if start <= st <= start + timedelta(seconds=window_sec):
                    ids.append(jid)
            return ids
        try:
            out = subprocess.run(
                ["bjobs", "-u", user, "-noheader", "-o", "jobid submit_time"],
                capture_output=True, text=True, timeout=self.timeout,
            )
            return [l.split()[0] for l in out.stdout.splitlines() if l.strip()]
        except (OSError, subprocess.SubprocessError):
            return []

    # ── kill (chatbot cancel actions / restart cleanup) ───────────────────────
    def kill(self, job_id):
        if self.simulated:
            return _SIM.kill(job_id)
        try:
            subprocess.run(["bkill", str(job_id)], capture_output=True, text=True, timeout=self.timeout)
            return True
        except (OSError, subprocess.SubprocessError):
            return False


def parse_bjobs_l(raw: str) -> dict:
    """Extract structured features from raw ``bjobs -l`` text (best-effort regex)."""
    import re
    rec = {}
    patterns = {
        "status": r"Status <(\w+)>",
        "queue": r"Queue <(\w+)>",
        "user_name": r"User <(\w+)>",
        "command": r"Command <(.+?)>",
        "cwd": r"CWD <(.+?)>",
    }
    for key, pat in patterns.items():
        m = re.search(pat, raw)
        if m:
            rec[key] = m.group(1)
    mem = re.search(r"MAX MEM:\s*([\d.]+)\s*([MG])bytes", raw)
    if mem:
        val = float(mem.group(1))
        rec["mem_peak_mb"] = int(val * 1024) if mem.group(2) == "G" else int(val)
    exitm = re.search(r"Exited with exit code (\d+)", raw)
    if exitm:
        rec["exit_code"] = int(exitm.group(1))
    term = re.search(r"(TERM_\w+)", raw)
    if term:
        rec["term_signal"] = term.group(1)
    if rec.get("command"):
        tool, stage, _ = detect_tool(rec["command"])
        rec["tool"], rec["flow_stage"] = tool, stage
    return rec
