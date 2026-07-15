-- =============================================================================
-- Control Plane schema catch-up (SQLite-primary migration).
--
-- These columns already exist in the real runtime schema (scripts/
-- seed_local_db.py's local-mode DDL and every api/services/*.py column
-- list already reads/writes them) but were never given a committed Athena
-- ALTER TABLE statement -- drift between sql/*.sql and the actual code.
-- Needed now so scripts/control_plane_sync.py's periodic push has a real
-- Athena column to land in for every SQLite-primary column.
--
-- Idempotency note: Athena's ALTER TABLE ... ADD COLUMNS fails if a column
-- already exists. Run each statement once per environment, or guard it
-- with your deployment tool's "does this column exist" check first.
-- =============================================================================

-- stream_registry: Control-M fields the code actually uses today.
-- Supersedes the older single controlm_job_name column (sql/
-- create_stream_registry.sql) -- that column is left in place (Athena
-- ALTER TABLE DROP COLUMN is not safely portable across Iceberg/Athena
-- versions) but is no longer written by any code path; use
-- controlm_pipeline_job/controlm_hk_job instead.
ALTER TABLE zamboni_catalog.stream_registry ADD COLUMNS (
    controlm_pipeline_job           STRING,
    controlm_hk_job                 STRING,
    controlm_job_start_time         STRING,
    controlm_expected_duration_min  INT,
    owner_name                      STRING,
    database_name                   STRING
);

-- hk_config: gate1/2/3 enable flags (Gate 1 upstream-job check, Gate 2 safe
-- window, Gate 3 circuit breaker) and the orphan-cleanup cadence, plus
-- sort_order_cols -- a plain comma-separated STRING (see engine/core/
-- config.py::_to_sql_array()), superseding the old sort_columns
-- ARRAY<STRING> column (sql/create_hk_config.sql), which no code writes.
ALTER TABLE zamboni_catalog.hk_config ADD COLUMNS (
    gate1_enabled                INT,
    gate2_enabled                INT,
    gate3_enabled                INT,
    orphan_cleanup_cadence_days  INT,
    sort_order_cols              STRING
);

-- nonprod_registry: fields the code actually uses beyond the original DDL
-- (sql/create_nonprod_registry.sql).
ALTER TABLE zamboni_catalog.nonprod_registry ADD COLUMNS (
    stale_threshold_days  INT,
    is_backup             BOOLEAN,
    owner_email           STRING,
    database_name         STRING
);
