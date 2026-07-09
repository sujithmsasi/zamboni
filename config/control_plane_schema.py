"""
Zamboni -- Control-plane schema (shared source of truth)

SQLite DDL + migrations for the 5 tables that are SQLite-primary in
production: stream_registry, hk_config, domain_registry, nonprod_registry,
controlm_jobs. Both the demo/local fixture (scripts/seed_local_db.py) and
the real production control-plane DB (scripts/init_control_plane_db.py)
import this so the two schemas can never drift into independently
hand-maintained copies -- the drift that happened between sql/*.sql and
scripts/seed_local_db.py is exactly what this module exists to prevent
happening again between the demo DB and the control-plane DB.

stream_registry's DDL here deliberately EXCLUDES the engine-owned columns
that stay Athena-direct via their own dedicated read/write functions,
independent of the general row-fetch path:
    aws_opt_compaction / aws_opt_retention / aws_opt_orphan /
        aws_opt_checked_at   -- engine/core/conflict_detector.py (Gate 0 cache)
    last_execution_id        -- engine/core/idempotency.py
    metadata_location        -- engine/core/recovery.py (local-sim only)
    properties_synced        -- engine/core/property_sync.py

scripts/seed_local_db.py adds all of the above back on top of this shared
base for its own table dict, since local mode simulates the entire Athena
side (including engine-owned state) through one file. The production
control-plane DB (scripts/init_control_plane_db.py) uses this module's
DDL/migrations as-is, with no additions -- those columns simply never
exist in the control-plane mirror; every reader of them
(conflict_detector.get_cached(), idempotency.check_already_executed(),
recovery._current_metadata_location()) already queries them directly
against real Athena, never via a pre-fetched row from this table.
"""
from __future__ import annotations

CONTROL_PLANE_TABLES: dict[str, str] = {

"domain_registry": """
CREATE TABLE IF NOT EXISTS domain_registry (
    domain_name             TEXT PRIMARY KEY,
    environment             TEXT,
    owner_email             TEXT,
    hot_retention_days      INTEGER DEFAULT 30,
    stale_threshold_days    INTEGER DEFAULT 60,
    is_active               INTEGER DEFAULT 1,
    archive_enabled         INTEGER DEFAULT 0,
    digest_enabled          INTEGER DEFAULT 0,
    digest_email            TEXT,
    ci_number               TEXT,
    notes                   TEXT,
    created_at              TEXT,
    updated_at              TEXT,
    registered_at           TEXT DEFAULT (datetime('now')),
    display_name            TEXT,
    owner_name              TEXT,
    team_name               TEXT,
    description             TEXT,
    archive_duration_days   INTEGER DEFAULT 365,
    auto_delete_after_days  INTEGER DEFAULT 120,
    registered_by           TEXT DEFAULT ''
)""",

# aws_opt_*, last_execution_id, metadata_location, properties_synced
# deliberately excluded -- see module docstring.
"stream_registry": """
CREATE TABLE IF NOT EXISTS stream_registry (
    table_fqn               TEXT PRIMARY KEY,
    domain                  TEXT,
    layer                   TEXT,
    tier                    TEXT,
    table_format            TEXT DEFAULT 'iceberg',
    environment             TEXT DEFAULT 'prod',
    owner_email             TEXT,
    ci_number               TEXT,
    hk_enabled              INTEGER DEFAULT 0,
    dry_run_until           TEXT,
    force_run               INTEGER DEFAULT 0,
    dependent_job_name      TEXT,
    dependent_job_type      TEXT DEFAULT 'glue',
    controlm_pipeline_job   TEXT,
    controlm_hk_job         TEXT,
    dependent_on_controlm_job TEXT,
    archive_enabled         INTEGER DEFAULT 0,
    archive_retention_days  INTEGER,
    archive_bucket          TEXT,
    lifecycle_enabled       INTEGER DEFAULT 0,
    processing_cadence      TEXT,
    registered_by           TEXT,
    registered_at           TEXT,
    updated_at              TEXT,
    database_name           TEXT,
    owner_name              TEXT DEFAULT '',
    notes                   TEXT DEFAULT ''
)""",

"controlm_jobs": """
CREATE TABLE IF NOT EXISTS controlm_jobs (
    job_name              TEXT PRIMARY KEY,
    job_type              TEXT DEFAULT 'controlm',
    description           TEXT DEFAULT '',
    domain                TEXT DEFAULT '',
    environment           TEXT DEFAULT 'prod',
    expected_start_time   TEXT DEFAULT '',
    expected_duration_min INTEGER DEFAULT 0,
    job_frequency         TEXT DEFAULT '',
    active                INTEGER DEFAULT 1,
    registered_by         TEXT DEFAULT 'system',
    created_at            TEXT,
    updated_at            TEXT
)""",

"hk_config": """
CREATE TABLE IF NOT EXISTS hk_config (
    table_fqn                       TEXT PRIMARY KEY,
    policy_template                 TEXT,
    compaction_strategy             TEXT DEFAULT 'binpack',
    compaction_target_file_size_mb  INTEGER DEFAULT 128,
    compaction_engine               TEXT DEFAULT 'athena',
    sort_order_cols                 TEXT,
    snapshot_retention_days         INTEGER DEFAULT 7,
    snapshot_min_to_keep            INTEGER DEFAULT 30,
    orphan_file_retention_days      INTEGER DEFAULT 3,
    orphan_cleanup_cadence_days     INTEGER DEFAULT 7,
    run_frequency                   TEXT DEFAULT 'daily',
    partition_column                TEXT,
    partition_filter_days           INTEGER,
    window_config                   TEXT,
    manually_overridden             INTEGER DEFAULT 0,
    override_notes                  TEXT,
    created_at                      TEXT,
    updated_at                      TEXT,
    partition_type                  TEXT DEFAULT 'date'
)""",

"nonprod_registry": """
CREATE TABLE IF NOT EXISTS nonprod_registry (
    table_fqn               TEXT PRIMARY KEY,
    domain                  TEXT,
    environment             TEXT,
    table_format            TEXT DEFAULT 'iceberg',
    lifecycle_state         TEXT DEFAULT 'ACTIVE',
    last_query_at           TEXT,
    last_write_at           TEXT,
    days_since_activity     INTEGER DEFAULT 0,
    stale_threshold_days    INTEGER DEFAULT 60,
    is_backup               INTEGER DEFAULT 0,
    is_backup_pattern       INTEGER DEFAULT 0,
    pattern_matched         TEXT DEFAULT '',
    owner_email              TEXT,
    owner_exempted          INTEGER DEFAULT 0,
    exemption_reason        TEXT,
    state_changed_at        TEXT,
    greenzone_expires_at    TEXT,
    pending_drop_expires_at TEXT,
    first_seen_at           TEXT,
    scan_count              INTEGER DEFAULT 0,
    created_at              TEXT,
    database_name           TEXT DEFAULT '',
    dropped_at              TEXT,
    bytes_reclaimed         INTEGER DEFAULT 0,
    s3_cleaned              INTEGER DEFAULT 0,
    catalog_dropped         INTEGER DEFAULT 0,
    previous_state          TEXT
)""",

}

# Same (table, column, sqlite_type) shape as scripts/seed_local_db.py's own
# _migrations list -- ALTER TABLE ADD COLUMN, skipped silently if the column
# already exists. Only entries for the 5 control-plane tables, and only
# UI-owned columns (the stream_registry aws_opt_*/metadata_location entries
# are deliberately omitted -- see module docstring).
CONTROL_PLANE_MIGRATIONS: list[tuple[str, str, str]] = [
    ("stream_registry",  "owner_name",                       "TEXT DEFAULT ''"),
    ("stream_registry",  "notes",                             "TEXT DEFAULT ''"),
    ("stream_registry",  "partition_type",                   "TEXT DEFAULT 'date'"),
    ("stream_registry",  "controlm_job_start_time",          "TEXT DEFAULT '02:00'"),
    ("stream_registry",  "controlm_expected_duration_min",   "INTEGER DEFAULT 0"),
    ("hk_config",        "gate1_enabled",                    "INTEGER DEFAULT 0"),
    ("hk_config",        "gate2_enabled",                    "INTEGER DEFAULT 1"),
    ("hk_config",        "gate3_enabled",                    "INTEGER DEFAULT 1"),
    ("hk_config",        "partition_type",     "TEXT DEFAULT 'date'"),
    ("domain_registry",  "display_name",       "TEXT"),
    ("domain_registry",  "registered_at",      "TEXT DEFAULT (datetime('now'))"),
    ("domain_registry",  "owner_name",         "TEXT DEFAULT ''"),
    ("domain_registry",  "team_name",          "TEXT DEFAULT ''"),
    ("domain_registry",  "description",        "TEXT DEFAULT ''"),
    ("domain_registry",  "archive_duration_days",  "INTEGER DEFAULT 365"),
    ("domain_registry",  "auto_delete_after_days", "INTEGER DEFAULT 120"),
    ("domain_registry",  "registered_by",      "TEXT DEFAULT ''"),
    ("hk_config",        "gate0_override_until",  "TEXT"),
    ("hk_config",        "gate0_override_reason", "TEXT"),
    ("hk_config",        "gate0_override_by",     "TEXT"),
    ("controlm_jobs",    "job_frequency",             "TEXT DEFAULT ''"),
]
