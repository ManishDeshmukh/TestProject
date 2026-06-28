-- JobPilot local SQLite schema (hot tier — live job state, P2).
-- All license tables are created here at schema-init, never at agent-init.

CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT UNIQUE NOT NULL,
    job_name         TEXT,
    status           TEXT,        -- PEND|RUN|DONE|EXIT|RESTARTED|FAILED_FINAL
    queue            TEXT,
    user_name        TEXT,
    host             TEXT,
    submit_time      TEXT,
    start_time       TEXT,
    end_time         TEXT,
    runtime_sec      INTEGER,     -- TOTAL: queue+license+compute
    actual_compute_sec INTEGER,   -- TRUE compute: end - license_checkout
    cpu_used         REAL,
    mem_used_mb      INTEGER,
    mem_peak_mb      INTEGER,
    mem_requested_mb INTEGER,
    disk_used_mb     INTEGER,
    exit_code        INTEGER,
    term_signal      TEXT,
    cwd              TEXT,
    command          TEXT,
    tool             TEXT,
    flow_stage       TEXT,
    has_spef         INTEGER,
    corner_count     INTEGER,
    restart_count    INTEGER DEFAULT 0,
    parent_job_id    TEXT,
    root_job_id      TEXT,
    job_depth        INTEGER DEFAULT 0,
    is_discovered    INTEGER DEFAULT 0,
    sync_status      TEXT DEFAULT 'pending',
    raw_bjobs        TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_analysis (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT REFERENCES jobs(job_id),
    analysis_ts      TEXT DEFAULT (datetime('now')),
    root_cause       TEXT,
    error_category   TEXT,
    confidence       TEXT,
    evidence         TEXT,
    recommended_fix  TEXT,
    restart_advised  INTEGER,
    restart_params   TEXT,
    license_contributed INTEGER DEFAULT 0,
    llm_model        TEXT,
    tokens_used      INTEGER
);

CREATE TABLE IF NOT EXISTS job_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT REFERENCES jobs(job_id),
    snapshot_ts      TEXT DEFAULT (datetime('now')),
    elapsed_sec      INTEGER,
    mem_current_mb   INTEGER,
    cpu_current      REAL,
    mem_util_pct     REAL,
    slope_mb_per_min REAL,
    eta_to_limit_min REAL,
    risk_level       TEXT
);

CREATE TABLE IF NOT EXISTS job_license_usage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT REFERENCES jobs(job_id),
    license_feature     TEXT,
    license_server      TEXT,
    checkout_time       TEXT,
    release_time        TEXT,
    license_wait_sec    INTEGER,
    actual_compute_sec  INTEGER,
    detection_method    TEXT,
    recorded_at         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS license_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_ts     TEXT DEFAULT (datetime('now')),
    feature         TEXT,
    server          TEXT,
    total           INTEGER,
    in_use          INTEGER,
    available       INTEGER,
    active_users    TEXT
);

CREATE TABLE IF NOT EXISTS license_wait_stats (
    feature         TEXT,
    tool            TEXT,
    hour_of_day     INTEGER,
    day_of_week     INTEGER,
    sample_count    INTEGER,
    avg_wait_sec    REAL,
    p50_wait_sec    REAL,
    p90_wait_sec    REAL,
    max_wait_sec    REAL,
    computed_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (feature, tool, hour_of_day, day_of_week)
);

CREATE TABLE IF NOT EXISTS resource_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_ts     TEXT DEFAULT (datetime('now')),
    queue_name      TEXT,
    running_jobs    INTEGER,
    pending_jobs    INTEGER,
    load_pct        REAL,
    top_users       TEXT
);

CREATE TABLE IF NOT EXISTS disk_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_ts     TEXT DEFAULT (datetime('now')),
    mount_point     TEXT,
    used_gb         REAL,
    total_gb        REAL,
    util_pct        REAL,
    top_consumers   TEXT
);

CREATE TABLE IF NOT EXISTS coordinator_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts        TEXT DEFAULT (datetime('now')),
    job_id          TEXT,
    event_type      TEXT,
    agent           TEXT,
    payload         TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS prediction_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                  TEXT REFERENCES jobs(job_id),
    predicted_compute_sec   INTEGER,
    actual_compute_sec      INTEGER,
    predicted_license_wait  INTEGER,
    actual_license_wait     INTEGER,
    predicted_total_sec     INTEGER,
    actual_total_sec        INTEGER,
    predicted_mem_mb        INTEGER,
    actual_mem_mb           INTEGER,
    suggested_queue         TEXT,
    actual_queue            TEXT,
    prediction_confidence   TEXT,
    license_confidence      TEXT,
    prediction_ts           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts        TEXT DEFAULT (datetime('now')),
    agent_name      TEXT,
    status          TEXT,
    action          TEXT,
    job_id          TEXT,
    latency_ms      INTEGER,
    error_msg       TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status    ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_root      ON jobs(root_job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_queue     ON jobs(queue);
CREATE INDEX IF NOT EXISTS idx_jobs_submit    ON jobs(submit_time);
CREATE INDEX IF NOT EXISTS idx_jobs_sync      ON jobs(sync_status, status);
CREATE INDEX IF NOT EXISTS idx_snapshots_job  ON job_snapshots(job_id);
CREATE INDEX IF NOT EXISTS idx_lic_job        ON job_license_usage(job_id);
CREATE INDEX IF NOT EXISTS idx_lic_feat       ON job_license_usage(license_feature);
CREATE INDEX IF NOT EXISTS idx_lic_snap       ON license_snapshots(snapshot_ts, feature);
CREATE INDEX IF NOT EXISTS idx_rsrc_ts        ON resource_snapshots(snapshot_ts);
CREATE INDEX IF NOT EXISTS idx_disk_ts        ON disk_snapshots(snapshot_ts);
CREATE INDEX IF NOT EXISTS idx_agent_ts       ON agent_events(event_ts);
