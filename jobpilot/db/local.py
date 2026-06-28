"""Local SQLite hot tier — the single source of live job state (P2).

Uses the stdlib ``sqlite3`` driver (always available, air-gap friendly).  NFS is
detected at init and WAL mode applied to mitigate corruption risk.  All access is
serialised behind a lock because the coordinator drives several daemon threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import warnings
from pathlib import Path

from ..config import filesystem_type

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class LocalDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.on_nfs = self._apply_pragmas()
        self.init_schema()

    # ── setup ─────────────────────────────────────────────────────────────────
    def _apply_pragmas(self) -> bool:
        fs = filesystem_type(self.db_path.parent)
        on_nfs = "nfs" in fs
        if on_nfs:
            warnings.warn(f"JobPilot DB on NFS ({fs}): applying WAL mode to reduce corruption risk")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        else:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        return on_nfs

    def init_schema(self):
        with self._lock:
            self._conn.executescript(SCHEMA_FILE.read_text())
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ── generic helpers ───────────────────────────────────────────────────────
    def _exec(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def _query_one(self, sql, params=()):
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ── jobs ──────────────────────────────────────────────────────────────────
    JOB_COLS = [
        "job_id", "job_name", "status", "queue", "user_name", "host",
        "submit_time", "start_time", "end_time", "runtime_sec", "actual_compute_sec",
        "cpu_used", "mem_used_mb", "mem_peak_mb", "mem_requested_mb", "disk_used_mb",
        "exit_code", "term_signal", "cwd", "command", "tool", "flow_stage",
        "has_spef", "corner_count", "restart_count", "parent_job_id", "root_job_id",
        "job_depth", "is_discovered", "sync_status", "raw_bjobs",
    ]

    def write_job(self, job: dict):
        cols = [c for c in self.JOB_COLS if c in job]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "job_id")
        sql = (
            f"INSERT INTO jobs ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(job_id) DO UPDATE SET {updates}"
        )
        self._exec(sql, tuple(job[c] for c in cols))

    def update_job(self, job_id: str, **fields):
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE jobs SET {sets} WHERE job_id=?", (*fields.values(), job_id))

    def update_job_status(self, job_id: str, status: str):
        self.update_job(job_id, status=status)

    def get_job(self, job_id: str):
        return self._query_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))

    def get_jobs_by_status(self, *statuses):
        marks = ",".join("?" for _ in statuses)
        return self._query(f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY submit_time DESC", statuses)

    def get_all_jobs(self, limit=500):
        return self._query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

    def get_root_jobs(self, limit=25, offset=0, status=None, queue=None):
        clauses = ["(parent_job_id IS NULL OR parent_job_id='')"]
        params = []
        if status:
            clauses.append("status=?"); params.append(status)
        if queue:
            clauses.append("queue=?"); params.append(queue)
        where = " AND ".join(clauses)
        total = self._query_one(f"SELECT COUNT(*) c FROM jobs WHERE {where}", params)["c"]
        params2 = params + [limit, offset]
        rows = self._query(
            f"SELECT * FROM jobs WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", params2
        )
        return rows, total

    def get_children(self, root_job_id: str):
        return self._query(
            "SELECT * FROM jobs WHERE root_job_id=? AND job_id!=? ORDER BY id",
            (root_job_id, root_job_id),
        )

    def get_similar_jobs(self, tool, queue, limit=5):
        return self._query(
            "SELECT * FROM jobs WHERE tool=? AND queue=? AND status IN ('DONE','EXIT') "
            "ORDER BY id DESC LIMIT ?",
            (tool, queue, limit),
        )

    def get_completed_for_training(self, months=6):
        return self._query(
            "SELECT * FROM jobs WHERE status IN ('DONE','EXIT') "
            "AND actual_compute_sec IS NOT NULL"
        )

    def count_similar(self, tool, flow_stage, queue):
        row = self._query_one(
            "SELECT COUNT(*) c FROM jobs WHERE tool=? AND flow_stage=? AND queue=? "
            "AND status IN ('DONE','EXIT')",
            (tool, flow_stage, queue),
        )
        return row["c"] if row else 0

    def lineage_restart_count(self, root_job_id: str):
        row = self._query_one(
            "SELECT COALESCE(MAX(restart_count),0) m FROM jobs WHERE root_job_id=?",
            (root_job_id,),
        )
        return row["m"] if row else 0

    # ── analysis ──────────────────────────────────────────────────────────────
    def write_analysis(self, rec: dict):
        rec = dict(rec)
        if isinstance(rec.get("evidence"), (list, dict)):
            rec["evidence"] = json.dumps(rec["evidence"])
        if isinstance(rec.get("restart_params"), (list, dict)):
            rec["restart_params"] = json.dumps(rec["restart_params"])
        cols = ["job_id", "root_cause", "error_category", "confidence", "evidence",
                "recommended_fix", "restart_advised", "restart_params",
                "license_contributed", "llm_model", "tokens_used"]
        cols = [c for c in cols if c in rec]
        ph = ",".join("?" for _ in cols)
        self._exec(f"INSERT INTO job_analysis ({','.join(cols)}) VALUES ({ph})",
                   tuple(rec[c] for c in cols))

    def get_recent_analyses(self, limit=8):
        rows = self._query(
            "SELECT a.*, j.tool, j.job_name FROM job_analysis a "
            "LEFT JOIN jobs j ON a.job_id=j.job_id ORDER BY a.id DESC LIMIT ?",
            (limit,),
        )
        return rows

    def get_analysis_for(self, job_id):
        return self._query_one(
            "SELECT * FROM job_analysis WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)
        )

    # ── snapshots / early warning ─────────────────────────────────────────────
    def write_snapshot(self, snap: dict):
        cols = ["job_id", "elapsed_sec", "mem_current_mb", "cpu_current",
                "mem_util_pct", "slope_mb_per_min", "eta_to_limit_min", "risk_level"]
        cols = [c for c in cols if c in snap]
        ph = ",".join("?" for _ in cols)
        self._exec(f"INSERT INTO job_snapshots ({','.join(cols)}) VALUES ({ph})",
                   tuple(snap[c] for c in cols))

    def get_mem_snapshots(self, job_id):
        return self._query(
            "SELECT * FROM job_snapshots WHERE job_id=? ORDER BY elapsed_sec", (job_id,)
        )

    def get_early_warnings(self):
        return self._query(
            "SELECT s.*, j.tool, j.mem_requested_mb FROM job_snapshots s "
            "JOIN jobs j ON s.job_id=j.job_id "
            "WHERE s.id IN (SELECT MAX(id) FROM job_snapshots GROUP BY job_id) "
            "AND j.status='RUN' AND s.risk_level IN ('WARNING','CRITICAL') "
            "ORDER BY CASE s.risk_level WHEN 'CRITICAL' THEN 0 ELSE 1 END"
        )

    # ── resource / disk snapshots ─────────────────────────────────────────────
    def write_resource_snapshot(self, rec):
        self._exec(
            "INSERT INTO resource_snapshots (queue_name,running_jobs,pending_jobs,load_pct,top_users) "
            "VALUES (?,?,?,?,?)",
            (rec["queue_name"], rec["running_jobs"], rec["pending_jobs"],
             rec["load_pct"], json.dumps(rec.get("top_users", []))),
        )

    def write_disk_snapshot(self, rec):
        self._exec(
            "INSERT INTO disk_snapshots (mount_point,used_gb,total_gb,util_pct,top_consumers) "
            "VALUES (?,?,?,?,?)",
            (rec["mount_point"], rec["used_gb"], rec["total_gb"],
             rec["util_pct"], json.dumps(rec.get("top_consumers", []))),
        )

    def latest_queues(self, limit=5):
        return self._query(
            "SELECT queue_name name, running_jobs running, pending_jobs pending, load_pct "
            "FROM resource_snapshots WHERE snapshot_ts=(SELECT MAX(snapshot_ts) FROM resource_snapshots) "
            "ORDER BY load_pct DESC LIMIT ?",
            (limit,),
        )

    def latest_disks(self, limit=5):
        return self._query(
            "SELECT mount_point mount, used_gb, total_gb, util_pct "
            "FROM disk_snapshots WHERE snapshot_ts=(SELECT MAX(snapshot_ts) FROM disk_snapshots) "
            "ORDER BY util_pct DESC LIMIT ?",
            (limit,),
        )

    # ── license ───────────────────────────────────────────────────────────────
    def write_license_usage(self, rec: dict):
        cols = ["job_id", "license_feature", "license_server", "checkout_time",
                "release_time", "license_wait_sec", "actual_compute_sec", "detection_method"]
        cols = [c for c in cols if c in rec]
        ph = ",".join("?" for _ in cols)
        self._exec(f"INSERT INTO job_license_usage ({','.join(cols)}) VALUES ({ph})",
                   tuple(rec[c] for c in cols))

    def write_license_snapshot(self, rec: dict):
        self._exec(
            "INSERT INTO license_snapshots (feature,server,total,in_use,available,active_users) "
            "VALUES (?,?,?,?,?,?)",
            (rec["feature"], rec.get("server"), rec["total"], rec["in_use"],
             rec["available"], json.dumps(rec.get("active_users", []))),
        )

    def latest_license_snapshots(self, limit=5):
        return self._query(
            "SELECT feature,total,in_use,available FROM license_snapshots "
            "WHERE snapshot_ts=(SELECT MAX(snapshot_ts) FROM license_snapshots) "
            "ORDER BY (CAST(in_use AS REAL)/NULLIF(total,0)) DESC LIMIT ?",
            (limit,),
        )

    def get_license_wait_stats(self, feature, tool, hour, dow):
        return self._query_one(
            "SELECT * FROM license_wait_stats WHERE feature=? AND tool=? "
            "AND hour_of_day=? AND day_of_week=?",
            (feature, tool, hour, dow),
        )

    def upsert_license_wait_stat(self, rec: dict):
        self._exec(
            "INSERT INTO license_wait_stats "
            "(feature,tool,hour_of_day,day_of_week,sample_count,avg_wait_sec,"
            " p50_wait_sec,p90_wait_sec,max_wait_sec) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(feature,tool,hour_of_day,day_of_week) DO UPDATE SET "
            "sample_count=excluded.sample_count, avg_wait_sec=excluded.avg_wait_sec, "
            "p50_wait_sec=excluded.p50_wait_sec, p90_wait_sec=excluded.p90_wait_sec, "
            "max_wait_sec=excluded.max_wait_sec, computed_at=datetime('now')",
            (rec["feature"], rec["tool"], rec["hour_of_day"], rec["day_of_week"],
             rec["sample_count"], rec["avg_wait_sec"], rec["p50_wait_sec"],
             rec["p90_wait_sec"], rec["max_wait_sec"]),
        )

    def low_contention_hour(self, feature, tool):
        row = self._query_one(
            "SELECT hour_of_day FROM license_wait_stats WHERE feature=? AND tool=? "
            "ORDER BY avg_wait_sec ASC LIMIT 1",
            (feature, tool),
        )
        return row["hour_of_day"] if row else None

    def has_license_history(self):
        row = self._query_one("SELECT COUNT(*) c FROM job_license_usage")
        return bool(row and row["c"] > 0)

    def license_data_age_weeks(self):
        row = self._query_one(
            "SELECT (julianday('now') - julianday(MIN(recorded_at)))/7.0 w "
            "FROM job_license_usage"
        )
        return int(row["w"]) if row and row["w"] else 0

    # ── events ────────────────────────────────────────────────────────────────
    def write_agent_event(self, rec: dict):
        self._exec(
            "INSERT INTO agent_events (agent_name,status,action,job_id,latency_ms,error_msg) "
            "VALUES (?,?,?,?,?,?)",
            (rec.get("agent_name"), rec.get("status"), rec.get("action"),
             rec.get("job_id"), rec.get("latency_ms", 0), rec.get("error_msg")),
        )

    def write_coordinator_event(self, job_id, event_type, agent, payload=None, notes=None):
        self._exec(
            "INSERT INTO coordinator_events (job_id,event_type,agent,payload,notes) VALUES (?,?,?,?,?)",
            (job_id, event_type, agent,
             json.dumps(payload) if isinstance(payload, (dict, list)) else payload, notes),
        )

    def get_agent_events(self, limit=50):
        return self._query("SELECT * FROM agent_events ORDER BY id DESC LIMIT ?", (limit,))

    def get_coordinator_events(self, limit=50):
        return self._query("SELECT * FROM coordinator_events ORDER BY id DESC LIMIT ?", (limit,))

    def agent_summary(self):
        return self._query(
            "SELECT agent_name name, "
            "       COUNT(*) handled, "
            "       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) errors, "
            "       CAST(AVG(latency_ms) AS INTEGER) avg_latency_ms, "
            "       MAX(action) last_action "
            "FROM agent_events GROUP BY agent_name"
        )

    # ── predictions ───────────────────────────────────────────────────────────
    def write_prediction(self, rec: dict):
        cols = ["job_id", "predicted_compute_sec", "predicted_license_wait",
                "predicted_total_sec", "predicted_mem_mb", "suggested_queue",
                "prediction_confidence", "license_confidence"]
        cols = [c for c in cols if c in rec]
        ph = ",".join("?" for _ in cols)
        self._exec(f"INSERT INTO prediction_log ({','.join(cols)}) VALUES ({ph})",
                   tuple(rec[c] for c in cols))

    def update_prediction_actuals(self, job_id, **fields):
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self._exec(
            f"UPDATE prediction_log SET {sets} WHERE id=(SELECT MAX(id) FROM prediction_log WHERE job_id=?)",
            (*fields.values(), job_id),
        )

    def prediction_accuracy_rows(self, limit=200):
        return self._query(
            "SELECT predicted_mem_mb, actual_mem_mb, predicted_compute_sec, actual_compute_sec "
            "FROM prediction_log WHERE actual_mem_mb IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    # ── retention (P8: raw rotated, models kept forever) ──────────────────────
    def purge_old_raw(self, retention_days=7):
        cur = self._exec(
            "DELETE FROM jobs WHERE status IN ('DONE','EXIT','FAILED_FINAL','ARCHIVED') "
            "AND created_at < datetime('now', ?) AND sync_status='synced'",
            (f"-{retention_days} days",),
        )
        return cur.rowcount

    # ── sync support ──────────────────────────────────────────────────────────
    def get_unsynced(self, batch=100):
        return self._query(
            "SELECT * FROM jobs WHERE sync_status='pending' AND status IN ('DONE','EXIT','FAILED_FINAL') "
            "LIMIT ?",
            (batch,),
        )

    def mark_synced(self, job_ids):
        for jid in job_ids:
            self.update_job(jid, sync_status="synced")
