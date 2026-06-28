"""Database Agent gateway: schema init, job upsert, license tables exist."""

from jobpilot.db.local import LocalDB


def test_schema_creates_license_tables(tmp_path):
    db = LocalDB(tmp_path / "jobs.db")
    tables = {r["name"] for r in db._query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for required in ("jobs", "job_license_usage", "license_snapshots",
                     "license_wait_stats", "prediction_log", "agent_events"):
        assert required in tables
    db.close()


def test_jobs_has_actual_compute_sec(tmp_path):
    db = LocalDB(tmp_path / "jobs.db")
    cols = {r["name"] for r in db._query("PRAGMA table_info(jobs)")}
    assert "actual_compute_sec" in cols
    db.close()


def test_write_and_update_job(tmp_path):
    db = LocalDB(tmp_path / "jobs.db")
    db.write_job({"job_id": "100", "status": "PEND", "queue": "normal", "tool": "synopsys_dc"})
    assert db.get_job("100")["status"] == "PEND"
    db.update_job_status("100", "RUN")
    assert db.get_job("100")["status"] == "RUN"
    db.close()


def test_root_jobs_and_children(tmp_path):
    db = LocalDB(tmp_path / "jobs.db")
    db.write_job({"job_id": "1", "status": "RUN", "root_job_id": "1"})
    db.write_job({"job_id": "2", "status": "RUN", "root_job_id": "1", "parent_job_id": "1"})
    roots, total = db.get_root_jobs()
    assert total == 1 and roots[0]["job_id"] == "1"
    children = db.get_children("1")
    assert len(children) == 1 and children[0]["job_id"] == "2"
    db.close()
