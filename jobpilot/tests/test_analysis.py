"""Analysis Agent rule engine (air-gap fallback) classifies failures correctly."""

from jobpilot.agents.analysis import AnalysisAgent


def _analyze(coordinator, job):
    # The coordinator always persists the job before RCA; mirror that here so the
    # job_analysis foreign key is satisfied.
    coordinator.db.write_job({k: job[k] for k in job if k in coordinator.db.local.JOB_COLS})
    return coordinator.analysis.analyze(job)["result"]


def test_memlimit_is_resource_limit_with_restart(coordinator):
    job = {"job_id": "1", "term_signal": "TERM_MEMLIMIT", "mem_requested_mb": 16000,
           "command": "dc_shell -f a.tcl", "start_time": "x", "restart_count": 0}
    res = _analyze(coordinator, job)
    assert res["error_category"] == "RESOURCE_LIMIT"
    assert res["restart_advised"] is True
    assert res["restart_params"]["memory_mb"] == 24000  # 16000 * 1.5


def test_cmd_not_found_is_environment_no_restart(coordinator):
    job = {"job_id": "2", "exit_code": 127, "command": "calibre -drc", "start_time": "x"}
    res = _analyze(coordinator, job)
    assert res["error_category"] == "ENVIRONMENT"
    assert res["restart_advised"] is False


def test_no_start_is_cluster_issue(coordinator):
    job = {"job_id": "3", "command": "icc2_shell -f p.tcl", "host": "node7"}
    res = _analyze(coordinator, job)
    assert res["error_category"] == "CLUSTER_ISSUE"
    assert "node7" in (res["restart_params"]["extra_flags"] or "")
