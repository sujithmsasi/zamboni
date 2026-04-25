-- =============================================================================
-- Execution Log
-- Unified audit log for all three engines.
-- Partitioned by execution_date for query performance.
-- Database: zamboni_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS glue_catalog.zamboni_catalog.execution_log (

    -- ── Run Identity ──────────────────────────────────────────────────────────
    execution_id        STRING    COMMENT 'UUID — unique per operation',
    run_id              STRING    COMMENT 'UUID — groups all operations in one engine invocation',
    engine              STRING    COMMENT 'hk | archival | lifecycle',
    operation           STRING    COMMENT 'compaction | vacuum | orphan_cleanup | archival | stale_scan | lifecycle_transition | catalog_cleanup',

    -- ── Table Context ─────────────────────────────────────────────────────────
    table_fqn           STRING    COMMENT 'Fully qualified table name',
    stream_id           STRING    COMMENT 'Stream ID from registry',
    domain              STRING    COMMENT 'Business domain',
    layer               STRING    COMMENT 'staging | datalake | base | master',
    tier                STRING    COMMENT 'critical | standard | low',
    environment         STRING    COMMENT 'prod | preprod | dev | test',

    -- ── Outcome ───────────────────────────────────────────────────────────────
    status              STRING    COMMENT 'SUCCESS | FAILURE | SKIPPED | DRY_RUN',
    skip_reason         STRING    COMMENT 'SKIP_UPSTREAM_PENDING | SKIP_OUTSIDE_WINDOW | SKIP_HEALTHY | SKIP_DISABLED | null',
    error_message       STRING    COMMENT 'Error detail on FAILURE. Null on success.',
    dry_run             BOOLEAN   COMMENT 'True if this was a dry-run evaluation — no writes',

    -- ── Timing ────────────────────────────────────────────────────────────────
    started_at          TIMESTAMP COMMENT 'Operation start',
    completed_at        TIMESTAMP COMMENT 'Operation end',
    duration_seconds    INT       COMMENT 'Wall-clock duration in seconds',

    -- ── HK Engine Metrics ─────────────────────────────────────────────────────
    snapshots_before    INT       COMMENT 'Snapshot count before vacuum',
    snapshots_after     INT       COMMENT 'Snapshot count after vacuum',
    snapshots_expired   INT       COMMENT 'Snapshots removed',
    orphan_files_deleted INT      COMMENT 'Orphan files removed',
    files_compacted     INT       COMMENT 'Files rewritten by compaction',
    bytes_rewritten     BIGINT    COMMENT 'Bytes rewritten by compaction',

    -- ── Archival Engine Metrics ───────────────────────────────────────────────
    partition_date      DATE      COMMENT 'Partition archived',
    rows_archived       BIGINT    COMMENT 'Row count exported to archive',
    bytes_archived      BIGINT    COMMENT 'Bytes written to archive S3',
    archive_s3_path     STRING    COMMENT 'Destination S3 prefix',
    pre_validation      STRING    COMMENT 'PASS | FAIL — pre-archive check',
    post_validation     STRING    COMMENT 'PASS | FAIL — post-archive reconciliation',

    -- ── Cost Tracking ─────────────────────────────────────────────────────────
    athena_query_id     STRING    COMMENT 'Athena QueryExecutionId for cost lookup',
    bytes_scanned       BIGINT    COMMENT 'Bytes scanned by Athena',

    -- ── Partition ─────────────────────────────────────────────────────────────
    execution_date      DATE      COMMENT 'Partition column — date of this execution'

)
PARTITIONED BY (execution_date)
LOCATION 's3://your-zamboni-metadata-bucket/execution_log/'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'PARQUET',
    'write_compression' = 'SNAPPY'
);
