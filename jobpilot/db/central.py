"""Central PostgreSQL warm tier sync — fully optional (P2).

Completed jobs are pushed asynchronously.  A central outage has *zero* impact on
the user's jobs or dashboard: every method degrades to a silent no-op when
psycopg2 is missing or ``[central] enabled = false``.
"""

from __future__ import annotations

try:
    import psycopg2  # type: ignore
    _HAVE_PG = True
except ImportError:
    _HAVE_PG = False


class CentralDB:
    def __init__(self, config):
        self.cfg = config
        self.enabled = config.getbool("central", "enabled", False) and _HAVE_PG
        self.url = config.get("central", "central_url", "")
        self._conn = None

    @property
    def available(self) -> bool:
        return self.enabled

    def _connect(self):
        if not self.enabled:
            return None
        if self._conn is not None:
            return self._conn
        try:
            self._conn = psycopg2.connect(self.url, connect_timeout=5)
            return self._conn
        except Exception:
            self._conn = None
            return None

    def sync_jobs(self, jobs, user, host):
        """Push a batch of completed jobs. Returns synced job_ids (empty if down)."""
        conn = self._connect()
        if conn is None:
            return []
        synced = []
        try:
            cur = conn.cursor()
            for j in jobs:
                cur.execute(
                    "INSERT INTO jobs (job_id, tool, flow_stage, queue, status, "
                    "actual_compute_sec, runtime_sec, mem_peak_mb, has_spef, corner_count, "
                    "source_user, source_host) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (job_id) DO NOTHING",
                    (j["job_id"], j.get("tool"), j.get("flow_stage"), j.get("queue"),
                     j.get("status"), j.get("actual_compute_sec"), j.get("runtime_sec"),
                     j.get("mem_peak_mb"), j.get("has_spef"), j.get("corner_count"),
                     user, host),
                )
                synced.append(j["job_id"])
            conn.commit()
        except Exception:
            self._conn = None  # force reconnect next time; never raise
            return synced
        return synced
