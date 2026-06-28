"""License Monitor Agent: optional, level detection, graceful degradation (P11)."""

from jobpilot.agents.license import LicenseMonitorAgent


def test_level_0_when_unconfigured(coordinator):
    # Default config has no license_servers → coordinator.license_agent is None.
    assert coordinator.license_agent is None
    assert coordinator.license_level == 0


def test_licenses_api_empty_at_level_0(coordinator):
    from jobpilot.dashboard.routes import DashboardAPI
    api = DashboardAPI(coordinator)
    assert api.licenses() == []


def test_level_detection_with_servers(tmp_config):
    tmp_config.set("licenses", "license_servers", "lic01:27000")
    agent = LicenseMonitorAgent_from(tmp_config)
    assert agent.detect_level() == 1  # servers but no history yet


def LicenseMonitorAgent_from(cfg):
    from jobpilot.agents.database import DatabaseAgent
    db = DatabaseAgent(cfg, "u", "h")
    return LicenseMonitorAgent(db, cfg)


def test_risk_classification():
    assert LicenseMonitorAgent._risk({"total": 20, "in_use": 0, "available": 20}) == "LOW"
    assert LicenseMonitorAgent._risk({"total": 20, "in_use": 18, "available": 2}) == "HIGH"
    assert LicenseMonitorAgent._risk({"total": 20, "in_use": 20, "available": 0}) == "BLOCKED"
