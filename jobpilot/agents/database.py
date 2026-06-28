"""Database Agent — single gateway for all DB reads and writes (P9).

Wraps :class:`LocalDB` and owns the background sync thread that pushes completed
jobs to central PostgreSQL.  Other agents receive *this* object as ``self.db`` so
all shared state flows through one place.  The sync thread never blocks the
coordinator and silently retries (P2).
"""

from __future__ import annotations

import threading
import time

from ..db.local import LocalDB
from ..db.central import CentralDB
from .base import BaseAgent, ok, err


class DatabaseAgent(BaseAgent):
    def __init__(self, config, user, host):
        self.cfg = config
        self.name = "DatabaseAgent"
        self.local = LocalDB(config.db_path)
        self.central = CentralDB(config)
        self.user = user
        self.host = host
        self._stop = threading.Event()
        self._sync_thread = None
        # NOTE: BaseAgent.__init__ skipped on purpose — this agent *is* the gateway.

    # The Database Agent IS the gateway, so it proxies LocalDB directly.
    def __getattr__(self, item):
        # Delegate unknown attributes (write_job, get_jobs_by_status, ...) to LocalDB.
        return getattr(self.local, item)

    def execute(self, action: str, payload: dict) -> dict:
        try:
            method = getattr(self.local, action)
        except AttributeError:
            return err(f"unknown db action: {action}")
        try:
            return ok(method(**payload) if payload else method())
        except Exception as e:  # noqa: BLE001
            return err(e)

    # ── background sync (every sync_interval_sec) ─────────────────────────────
    def start_sync(self):
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

    def stop(self):
        self._stop.set()
        self.local.close()

    def _sync_loop(self):
        interval = self.cfg.getint("database", "sync_interval_sec", 300)
        batch = self.cfg.getint("database", "sync_batch_size", 100)
        while not self._stop.wait(interval):
            try:
                self._sync_once(batch)
            except Exception:
                pass  # never blocks; silent retry next tick

    def _sync_once(self, batch=100):
        if not self.central.available:
            return 0
        jobs = self.local.get_unsynced(batch)
        if not jobs:
            return 0
        synced = self.central.sync_jobs(jobs, self.user, self.host)
        if synced:
            self.local.mark_synced(synced)
        return len(synced)
