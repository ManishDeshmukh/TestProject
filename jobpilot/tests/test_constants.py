"""Tool detection + license mapping derive from the command, never the job name (P6)."""

from jobpilot.constants import detect_tool, license_features_for, TOOL_DEFAULTS


def test_detect_tool_from_command():
    assert detect_tool("calibre -drc rules.svrf")[:2] == ("mentor_calibre", "drc_lvs")
    assert detect_tool("icc2_shell -f place.tcl")[:2] == ("synopsys_icc2", "pnr")
    assert detect_tool("dc_shell -f run.tcl")[0] == "synopsys_dc"


def test_job_name_is_not_a_feature():
    # Detection runs on the command path; a job name with no tool token yields None.
    assert detect_tool("block_top_run5")[0] is None
    # The command, not the name, drives detection.
    assert detect_tool("vcs -full64 tb.sv")[0] == "synopsys_vcs"


def test_license_features_for_tool():
    feats = license_features_for("calibre -drc r.svrf")
    assert "CAL_LVS_FLAT" in feats and "CAL_DRC_FLAT" in feats


def test_tool_defaults_present():
    assert TOOL_DEFAULTS["mentor_calibre"]["p90_mem_mb"] == 96000
