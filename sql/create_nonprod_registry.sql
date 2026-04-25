-- =============================================================================
-- Non-Prod Registry
-- Lifecycle Engine tracking table for preprod / dev / test environments.
-- Database: zamboni_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS glue_catalog.zamboni_catalog.nonprod_registry (

    -- ── Identity ──────────────────────────────────────────────────────────────
    table_fqn           STRING    COMMENT 'Fully qualified: glue_catalog.database.table',
    glue_database       STRING    COMMENT 'Glue database name',
    table_name          STRING    COMMENT 'Table name',
    environment         STRING    COMMENT 'preprod | dev | test',
    domain              STRING    COMMENT 'Inferred from database name or manually set',
    table_format        STRING    COMMENT 'iceberg | hudi | external | hive | unknown',

    -- ── Lifecycle State Machine ───────────────────────────────────────────────
    lifecycle_state     STRING    COMMENT 'ACTIVE | STALE_CANDIDATE | GREENZONE | PENDING_DROP | DROPPED',
    previous_state      STRING    COMMENT 'State before last transition',
    state_changed_at    TIMESTAMP COMMENT 'When state last changed',

    -- ── Activity Signals ──────────────────────────────────────────────────────
    last_query_at       TIMESTAMP COMMENT 'Last Athena StartQueryExecution from CloudTrail',
    last_write_at       TIMESTAMP COMMENT 'Last Glue/S3 write event',
    created_at          TIMESTAMP COMMENT 'Table CreateTime from Glue catalog',
    days_since_activity INT       COMMENT 'Days since max(last_query_at, last_write_at)',

    -- ── GREENZONE Workflow ────────────────────────────────────────────────────
    greenzone_notified_at   TIMESTAMP COMMENT 'When GREENZONE email was sent',
    greenzone_expires_at    TIMESTAMP COMMENT 'Window end — moves to PENDING_DROP if no response',
    owner_exempted          BOOLEAN   COMMENT 'Owner explicitly requested exemption',
    owner_response_at       TIMESTAMP COMMENT 'When owner responded',
    owner_response_note     STRING    COMMENT 'Owner justification for exemption',

    -- ── PENDING_DROP ──────────────────────────────────────────────────────────
    pending_drop_notified_at TIMESTAMP COMMENT '48-hour final notice timestamp',
    pending_drop_expires_at  TIMESTAMP COMMENT 'When auto-delete executes if no action',

    -- ── Drop Outcome ──────────────────────────────────────────────────────────
    dropped_at          TIMESTAMP COMMENT 'When table was deleted',
    s3_cleaned          BOOLEAN   COMMENT 'S3 data/ and metadata/ prefixes swept',
    catalog_dropped     BOOLEAN   COMMENT 'Glue DROP TABLE succeeded',
    bytes_reclaimed     BIGINT    COMMENT 'Estimated bytes freed from S3',

    -- ── Backup Pattern Detection ──────────────────────────────────────────────
    is_backup_pattern   BOOLEAN   COMMENT 'Name matches _bkp, _backup, _bak, _copy, _temp, _old or timestamp suffix',
    pattern_matched     STRING    COMMENT 'Which pattern matched',

    -- ── Audit ─────────────────────────────────────────────────────────────────
    first_seen_at       TIMESTAMP COMMENT 'First discovered by ZAMBONI-NONPROD-SCAN',
    last_scanned_at     TIMESTAMP COMMENT 'Most recent scan',
    scan_count          INT       COMMENT 'Number of times scanned'

)
LOCATION 's3://your-zamboni-metadata-bucket/nonprod_registry/'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'PARQUET',
    'write_compression' = 'SNAPPY'
);
