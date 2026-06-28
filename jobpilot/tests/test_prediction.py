"""Prediction Agent: cold-start defaults + always-present confidence label."""


def test_cold_start_uses_tool_defaults(coordinator):
    job = {"command": "calibre -drc r.svrf", "queue": "verify"}
    pred = coordinator.prediction.predict_resources(job)
    # No history → confidence NONE, calibre default memory.
    assert pred["confidence"] == "NONE"
    assert pred["predicted_mem_mb"] == 96000
    assert "confidence" in pred and "predicted_total_p50_min" in pred


def test_license_unavailable_marks_field(coordinator):
    job = {"command": "dc_shell -f a.tcl", "queue": "normal"}
    pred = coordinator.prediction.predict_resources(job, license_agent=None)
    assert pred["includes_license_wait"] is False
    assert pred["license_confidence"] == "UNAVAILABLE"
