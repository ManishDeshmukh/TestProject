"""Feature extraction from the *command* and ``bjobs -l`` signals (P6).

The job name is never a feature.  Features are derived from the command path
(tool/flow_stage), command arguments (.spef, corner count), the queue, submit
time, and the filesystem tier of the working directory.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..constants import detect_tool

FEATURE_ORDER = [
    "tool_id", "flow_id", "has_spef", "corner_count", "queue_id",
    "submit_hour", "submit_dow", "fs_tier",
]

# Stable categorical encodings so a persisted model keeps meaning across runs.
_TOOL_IDS = {
    None: 0, "synopsys_dc": 1, "synopsys_icc2": 2, "synopsys_pt": 3,
    "synopsys_star": 4, "synopsys_fm": 5, "mentor_calibre": 6, "cadence_genus": 7,
    "cadence_innovus": 8, "cadence_tempus": 9, "cadence_lec": 10, "synopsys_vcs": 11,
    "mentor_questa": 12,
}
_FLOW_IDS = {
    None: 0, "synthesis": 1, "pnr": 2, "sta": 3, "extraction": 4,
    "formal": 5, "drc_lvs": 6, "simulation": 7,
}


def fs_tier(cwd: str) -> int:
    """0 = scratch/local (fast), 1 = nfs/home (slow)."""
    if not cwd:
        return 0
    return 1 if ("/nfs" in cwd or cwd.startswith("/home") or "/proj" in cwd) else 0


def extract(job: dict) -> dict:
    """Return the named feature dict from a job record."""
    command = job.get("command", "") or ""
    tool = job.get("tool")
    stage = job.get("flow_stage")
    if not tool:
        tool, stage, _ = detect_tool(command)
    has_spef = 1 if ".spef" in command else 0
    corner_count = len(re.findall(r"corner", command, re.IGNORECASE))
    submit = job.get("submit_time")
    try:
        dt = datetime.fromisoformat(submit) if submit else datetime.now()
    except ValueError:
        dt = datetime.now()
    return {
        "tool": tool, "flow_stage": stage, "has_spef": has_spef,
        "corner_count": corner_count, "queue": job.get("queue", ""),
        "submit_hour": dt.hour, "submit_dow": dt.weekday(),
        "fs_tier": fs_tier(job.get("cwd", "")),
    }


def vectorize(feat: dict, queue_index: dict | None = None) -> list:
    """Turn a feature dict into a numeric vector in FEATURE_ORDER."""
    queue_index = queue_index or {}
    return [
        _TOOL_IDS.get(feat.get("tool"), 0),
        _FLOW_IDS.get(feat.get("flow_stage"), 0),
        feat.get("has_spef", 0),
        feat.get("corner_count", 0),
        queue_index.get(feat.get("queue", ""), 0),
        feat.get("submit_hour", 0),
        feat.get("submit_dow", 0),
        feat.get("fs_tier", 0),
    ]
