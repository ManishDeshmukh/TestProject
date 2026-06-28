"""Shared, immutable knowledge used across every JobPilot agent.

Centralising these dictionaries enforces design principle P6 (predict from
``bjobs -l`` signals, never the job name) and keeps tool/license/intent
definitions in exactly one place so the agents stay consistent.
"""

# ── Tool detection: command-path token → (tool, flow_stage) ──────────────────
# Detected from the *command*, never the job name (P6).
TOOL_PATTERNS = {
    "dc_shell":    ("synopsys_dc",     "synthesis"),
    "icc2_shell":  ("synopsys_icc2",   "pnr"),
    "pt_shell":    ("synopsys_pt",     "sta"),
    "StarXtract":  ("synopsys_star",   "extraction"),
    "fm_shell":    ("synopsys_fm",     "formal"),
    "calibre":     ("mentor_calibre",  "drc_lvs"),
    "genus":       ("cadence_genus",   "synthesis"),
    "innovus":     ("cadence_innovus", "pnr"),
    "tempus":      ("cadence_tempus",  "sta"),
    "lec":         ("cadence_lec",     "formal"),
    "vcs":         ("synopsys_vcs",    "simulation"),
    "questa":      ("mentor_questa",   "simulation"),
}

# ── FlexLM feature names per tool token ──────────────────────────────────────
LICENSE_MAP = {
    "dc_shell":   ["SYNOPSYS_DC", "DesignCompiler"],
    "icc2_shell": ["SYNOPSYS_ICC2", "IC_Compiler_2"],
    "pt_shell":   ["SYNOPSYS_PTPX", "PrimeTime"],
    "StarXtract": ["SYNOPSYS_STARRC", "StarRC"],
    "fm_shell":   ["SYNOPSYS_FMVERIFY", "Formality"],
    "vcs":        ["VCSi", "VCS_MX"],
    "genus":      ["GENUS_SYNTHESIS"],
    "innovus":    ["INNOVUS", "NXT_INNOVUS"],
    "tempus":     ["TEMPUS_STA"],
    "lec":        ["LEC_ULTRA", "CONFRML"],
    "calibre":    ["CAL_DRC_FLAT", "CAL_LVS_FLAT", "CAL_PEX_FLAT"],
    "questa":     ["questa_sim", "ModelSim_SE"],
}

# ── License checkout log patterns (regex), tried per detected tool family ─────
CHECKOUT_PATTERNS = {
    "synopsys": r"License acquired for (?P<feat>[\w\.]+) at (?P<time>[\d:\-/ ]+)",
    "calibre":  r"Checking out \d+ license\(s\) of (?P<feat>[\w\.]+)",
    "cadence":  r"Checking out license (?P<feat>[\w\.]+)",
    "vcs":      r"License: (?P<feat>[\w\.]+) checked out",
}

# ── Cold-start defaults when < min_samples similar jobs are known ─────────────
TOOL_DEFAULTS = {
    "synopsys_dc":     {"p90_mem_mb": 32000, "p90_rt_min": 120},
    "synopsys_icc2":   {"p90_mem_mb": 64000, "p90_rt_min": 480},
    "synopsys_pt":     {"p90_mem_mb": 48000, "p90_rt_min": 240},
    "mentor_calibre":  {"p90_mem_mb": 96000, "p90_rt_min": 360},
    "cadence_genus":   {"p90_mem_mb": 32000, "p90_rt_min": 120},
    "cadence_innovus": {"p90_mem_mb": 64000, "p90_rt_min": 480},
    "synopsys_vcs":    {"p90_mem_mb": 16000, "p90_rt_min": 60},
}
DEFAULT_RESOURCES = {"p90_mem_mb": 16000, "p90_rt_min": 60}

# ── Failure classification: term_signal / exit_code → (category, restart, …) ──
# scale keys: 'mem' multiplies memory_mb, 'rt' multiplies runtime_sec.
FAILURE_RULES = {
    "TERM_MEMLIMIT": {"category": "RESOURCE_LIMIT", "restart": True,  "scale": {"mem": 1.5}},
    "TERM_RUNLIMIT": {"category": "RESOURCE_LIMIT", "restart": True,  "scale": {"rt": 2.0}},
    "EXIT_137":      {"category": "RESOURCE_LIMIT", "restart": True,  "scale": {"mem": 1.5}},
    "EXIT_127":      {"category": "ENVIRONMENT",    "restart": False, "scale": {}},
    "EXIT_134":      {"category": "TOOL_CRASH",     "restart": "first_only", "scale": {}},
    "NO_START":      {"category": "CLUSTER_ISSUE",  "restart": True,  "scale": {}},
    "LICENSE_WAIT":  {"category": "LICENSE_WAIT",   "restart": True,  "scale": {"rt": 2.0}},
}

ERROR_CATEGORIES = [
    "RESOURCE_LIMIT", "ENVIRONMENT", "TOOL_CRASH",
    "CONSTRAINT_ERROR", "CLUSTER_ISSUE", "LICENSE_WAIT", "USER_ERROR",
]

# ── Chatbot intents (22): phrase triggers for rule-based classification ───────
INTENTS = {
    # Status
    "job_status":      ["status of job", "what is job", "check job"],
    "all_status":      ["all jobs", "my jobs", "job summary"],
    "running_jobs":    ["which jobs running", "running jobs"],
    "failed_jobs":     ["failed jobs", "exit jobs", "jobs that died"],
    "pending_jobs":    ["pending jobs", "stuck in queue"],
    # Analysis
    "why_failed":      ["why did", "why failed", "root cause", "rca for"],
    "last_failure":    ["last failed", "most recent failure"],
    "common_failures": ["common failures", "what keeps failing"],
    # Predictions
    "predict_tat":     ["how long will", "when will finish", "tat for"],
    "predict_memory":  ["how much memory", "mem requirement"],
    "queue_load":      ["queue load", "best queue", "queue status"],
    "disk_usage":      ["disk usage", "disk space"],
    # License
    "license_status":  ["license status", "lmstat", "licenses available",
                        "how many licenses", "license free"],
    "license_wait":    ["license wait", "how long for license",
                        "license contention", "best time to submit"],
    # History
    "job_history":     ["history", "past jobs", "jobs last week"],
    "job_details":     ["details of job", "tell me about job"],
    # Actions (confirmation required)
    "restart_job":     ["restart job", "resubmit job", "retry job"],
    "cancel_job":      ["cancel job", "kill job", "bkill job"],
    "cancel_all_pend": ["cancel all pending", "kill all pending"],
    # Navigation
    "help":            ["help", "what can you do", "commands"],
    "show_dashboard":  ["show dashboard", "open tab"],
    "unknown":         [],
}

CONFIRM_INTENTS = {"restart_job", "cancel_job", "cancel_all_pend"}
BLOCKED_INTENTS = {"submit_job"}  # new submissions never allowed from chat

# ── Job lifecycle states (P-coordinator state machine) ───────────────────────
STATES = [
    "NEW", "SUBMITTED", "PENDING", "RUNNING", "WARNED",
    "DONE", "EXIT", "ANALYZING", "RESTARTED", "FAILED_FINAL",
    "FAILED_BUDGET", "ARCHIVED",
]

# Risk thresholds (mirrors [early_warning] config defaults)
RISK_MEM_WARN = 0.65
RISK_MEM_CRIT = 0.85
RISK_ETA_CRIT_MIN = 30

# License contention thresholds
LIC_LOW = 0.60
LIC_HIGH = 0.85


def detect_tool(command: str):
    """Return ``(tool, flow_stage, token)`` detected from a command string.

    Detection is by command-path token (P6) — the job name is never consulted.
    Returns ``(None, None, None)`` if no known tool token is present.
    """
    if not command:
        return (None, None, None)
    for token, (tool, stage) in TOOL_PATTERNS.items():
        if token in command:
            return (tool, stage, token)
    return (None, None, None)


def license_features_for(command: str):
    """Return the list of FlexLM feature names a command is expected to need."""
    _, _, token = detect_tool(command)
    return LICENSE_MAP.get(token, []) if token else []
