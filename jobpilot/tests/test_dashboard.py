"""Dashboard API: summary counts, job trees, and the stdlib dispatcher."""

from jobpilot.dashboard.routes import DashboardAPI


def test_summary_and_jobs(coordinator):
    co = coordinator
    co.submit_job("dc0", "normal", "dc_shell -f a.tcl")
    api = DashboardAPI(co)
    s = api.summary()
    assert s["total"] == 1 and s["pending"] == 1
    assert s["license_level"] == 0
    jb = api.jobs()
    assert jb["total"] == 1
    assert jb["jobs"][0]["tool"] == "synopsys_dc"


def test_agents_endpoint_lists_eleven_nodes(coordinator):
    api = DashboardAPI(coordinator)
    names = {a["name"] for a in api.agents()}
    assert "Coordinator" in names and "ChatbotAgent" in names
    assert "LicenseMonitorAgent" in names
    assert len(names) == 11  # coordinator + 10 agents


def test_dispatch_routes(coordinator):
    api = DashboardAPI(coordinator)
    assert api.dispatch("/api/summary", {})["total"] == 0
    assert api.dispatch("/api/licenses", {}) == []
    assert api.dispatch("/api/unknown", {}) is None
