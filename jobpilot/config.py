"""Configuration + path resolution (design principle P12).

``~/.jobpilot/config.ini`` holds *config only* (tiny, safe on NFS home).  All
runtime data — SQLite DB, logs, models — lives under a configurable ``data_path``
on a fast scratch/project disk.  Resolution priority::

    config.ini [paths] data_path  >  $JOBPILOT_DATA  >  ~/.jobpilot  (fallback)
"""

from __future__ import annotations

import configparser
import getpass
import os
import shutil
import subprocess
from pathlib import Path

USER = getpass.getuser()
HOME_DIR = Path.home() / ".jobpilot"
CONFIG_PATH = HOME_DIR / "config.ini"
SESSION_PATH = HOME_DIR / ".session"

# Default config used to seed config.ini on first ``jp init``.
DEFAULTS = {
    "paths": {
        "data_path": f"/scratch/{USER}/.jobpilot",
        "tmp_path": f"/tmp/jobpilot_{USER}",
        "db_path": "",
        "log_path": "",
        "models_path": "",
    },
    "coordinator": {
        "poll_interval_run": "30",
        "poll_interval_pend": "120",
        "dashboard_port": "8765",
        "dashboard_host": "localhost",
        "auto_open_browser": "true",
        "browser_cmd": "firefox",
    },
    "lsf": {"bsub_extra_flags": "", "bjobs_timeout": "30", "max_parallel_polls": "20"},
    "restart": {
        "max_restarts_per_job": "3", "cooldown_seconds": "300",
        "global_hourly_limit": "50", "mem_scale_factor": "1.5", "rt_scale_factor": "2.0",
    },
    "early_warning": {
        "poll_at_minutes": "2,5,10", "mem_warn_threshold": "0.65",
        "mem_crit_threshold": "0.85", "eta_crit_minutes": "30",
    },
    "llm": {
        "model_analysis": "qwen2.5-coder:32b", "model_chat": "qwen3:8b",
        "model_log_large": "minimax-text-01", "model_fallback": "qwen2.5-coder:7b",
        "temperature_analysis": "0.1", "temperature_chat": "0.4",
        "ollama_url": "http://localhost:11434", "timeout_analysis_sec": "60",
        "timeout_chat_sec": "30", "log_size_threshold_mb": "50",
    },
    "database": {"retention_days": "7", "sync_interval_sec": "300", "sync_batch_size": "100"},
    "central": {
        "enabled": "false", "central_url": "http://central-server:8766",
        "central_models_path": "/nfs/jobpilot/models/", "sync_timeout_sec": "30",
    },
    "prediction": {
        "min_samples_for_ml": "10", "confidence_high": "1000",
        "confidence_medium": "200", "confidence_low": "10", "mem_safety_buffer": "1.2",
    },
    "alerts": {"terminal_output": "true", "disk_warn_threshold": "85", "queue_warn_threshold": "90"},
    "licenses": {
        "license_servers": "", "lmstat_path": "", "checker_script": "",
        "always_monitor": "", "poll_interval_sec": "60", "alert_threshold_pct": "80",
    },
}


class Config:
    """Typed accessor over ``config.ini`` with resolved, created data paths."""

    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config_path = Path(config_path)
        self._cp = configparser.ConfigParser()
        # Seed defaults so .get never explodes even with a partial file.
        self._cp.read_dict(DEFAULTS)
        if self.config_path.exists():
            self._cp.read(self.config_path)
        self.data_path = self._resolve_data_path()

    # ── raw getters ──────────────────────────────────────────────────────────
    def get(self, section, key, fallback=None):
        return self._cp.get(section, key, fallback=fallback)

    def getint(self, section, key, fallback=0):
        try:
            return self._cp.getint(section, key)
        except (ValueError, configparser.Error):
            return fallback

    def getfloat(self, section, key, fallback=0.0):
        try:
            return self._cp.getfloat(section, key)
        except (ValueError, configparser.Error):
            return fallback

    def getbool(self, section, key, fallback=False):
        try:
            return self._cp.getboolean(section, key)
        except (ValueError, configparser.Error):
            return fallback

    # ── path resolution (P12) ─────────────────────────────────────────────────
    def _expand(self, raw: str) -> str:
        return os.path.expanduser(os.path.expandvars(raw or ""))

    def _resolve_data_path(self) -> Path:
        cfg = self._expand(self.get("paths", "data_path", fallback=""))
        env = os.environ.get("JOBPILOT_DATA")
        if cfg and cfg not in ("", f"/scratch/{USER}/.jobpilot"):
            chosen = cfg                      # explicit config wins
        elif env:
            chosen = env                      # env override
        elif cfg:
            chosen = cfg                      # default config value
        else:
            chosen = str(HOME_DIR)            # last-resort fallback
        # If the configured scratch path is not writable (e.g. dev box without
        # /scratch), fall back to home so the system still runs.
        path = Path(self._expand(chosen))
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            path = HOME_DIR
            path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        raw = self._expand(self.get("paths", "db_path", fallback=""))
        return Path(raw) if raw else self.data_path / "jobs.db"

    @property
    def log_path(self) -> Path:
        raw = self._expand(self.get("paths", "log_path", fallback=""))
        p = Path(raw) if raw else self.data_path / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def models_path(self) -> Path:
        raw = self._expand(self.get("paths", "models_path", fallback=""))
        p = Path(raw) if raw else self.data_path / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def tmp_path(self) -> Path:
        raw = self._expand(self.get("paths", "tmp_path", fallback=f"/tmp/jobpilot_{USER}"))
        p = Path(raw)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            p = Path(f"/tmp/jobpilot_{USER}")
            p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def snapshots_path(self) -> Path:
        p = self.data_path / "snapshots"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ── license capability hint (Level 0 if no servers configured) ────────────
    @property
    def license_servers(self):
        raw = self.get("licenses", "license_servers", fallback="").strip()
        return [s.strip() for s in raw.split(",") if s.strip()]

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as fh:
            self._cp.write(fh)

    def set(self, section, key, value):
        if not self._cp.has_section(section):
            self._cp.add_section(section)
        self._cp.set(section, key, str(value))


# ── helpers used by `jp init` ─────────────────────────────────────────────────
def detect_scratch_candidates():
    """Return a list of ``(path, free_gb, recommended)`` candidate data dirs."""
    candidates, seen = [], set()
    probes = [
        f"/scratch/{USER}", f"/proj/eda/{USER}", f"/proj/{USER}",
        f"/tmp/{USER}", str(Path.home()),
    ]
    for base in probes:
        parent = Path(base).parent
        if not parent.exists():
            continue
        if base in seen:
            continue
        seen.add(base)
        try:
            usage = shutil.disk_usage(parent)
            free_gb = usage.free / (1024 ** 3)
        except OSError:
            continue
        recommended = ("/scratch" in base or "/proj" in base) and free_gb > 50
        candidates.append((base, round(free_gb, 1), recommended))
    return candidates


def filesystem_type(path: Path) -> str:
    """Best-effort filesystem type detection (for NFS warning / WAL mode)."""
    try:
        out = subprocess.run(
            ["df", "-T", str(path)], capture_output=True, text=True, timeout=5
        )
        lines = out.stdout.splitlines()
        if len(lines) >= 2:
            return lines[1].split()[1].lower()
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return "unknown"
