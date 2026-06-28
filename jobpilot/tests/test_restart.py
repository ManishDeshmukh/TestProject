"""Restart Agent loop guard (P4): all three checks must pass."""


def test_lineage_budget_blocks_after_max(coordinator):
    db = coordinator.db
    # Seed a lineage already at the restart cap.
    db.write_job({"job_id": "10", "status": "EXIT", "root_job_id": "10", "restart_count": 3,
                  "command": "dc_shell -f a.tcl", "queue": "normal"})
    job = db.get_job("10")
    allowed, reason = coordinator.restart._loop_guard(job)
    assert allowed is False
    assert "budget" in reason


def test_cooldown_blocks_second_restart(coordinator):
    db = coordinator.db
    db.write_job({"job_id": "20", "status": "EXIT", "root_job_id": "20", "restart_count": 0,
                  "command": "dc_shell -f a.tcl", "queue": "normal",
                  "term_signal": "TERM_MEMLIMIT", "mem_requested_mb": 16000})
    job = db.get_job("20")
    res = coordinator.restart.restart(job, {"error_category": "RESOURCE_LIMIT",
                                            "restart_params": {"memory_mb": 24000}})
    assert res["result"]["restarted"] is True
    # Immediately retrying the same lineage must hit the cooldown.
    allowed, reason = coordinator.restart._loop_guard(job)
    assert allowed is False and "cooldown" in reason


def test_restart_logs_bsub_before_execution(coordinator):
    db = coordinator.db
    db.write_job({"job_id": "30", "status": "EXIT", "root_job_id": "30", "restart_count": 0,
                  "command": "icc2_shell -f p.tcl", "queue": "long", "mem_requested_mb": 32000})
    job = db.get_job("30")
    coordinator.restart.restart(job, {"error_category": "RESOURCE_LIMIT", "restart_params": {}})
    events = db.get_coordinator_events(20)
    assert any(e["event_type"] == "BSUB_RESTART" for e in events)
