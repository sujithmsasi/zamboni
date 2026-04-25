-- =============================================================================
-- HK Config
-- Per-table housekeeping policy. One row per table in stream_registry.
-- Database: zamboni_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS glue_catalog.zamboni_catalog.hk_config (

    -- ── Identity (FK to stream_registry) ──────────────────────────────────────
    table_fqn                       STRING  COMMENT 'Matches stream_registry.table_fqn',
    policy_template                 STRING  COMMENT 'STAGING_DEFAULT | DATALAKE_DEFAULT | BASE_SCD2 | MASTER_DEFAULT | CRITICAL_HIGH_VOL | NON_PROD_DEFAULT',

    -- ── Compaction ────────────────────────────────────────────────────────────
    compaction_strategy             STRING  COMMENT 'binpack | sort | zorder',
    compaction_target_file_size_mb  INT     COMMENT 'Target file size after compaction (MB)',
    compaction_engine               STRING  COMMENT 'athena | glue',

    -- ── Snapshot Retention ────────────────────────────────────────────────────
    snapshot_retention_days         INT     COMMENT 'Expire snapshots older than N days',
    snapshot_min_to_keep            INT     COMMENT 'Hard floor — always keep at least N snapshots. Never below 30.',

    -- ── Orphan File Cleanup ───────────────────────────────────────────────────
    orphan_file_retention_days      INT     COMMENT 'Delete orphan files older than N days. Hard minimum: 2.',

    -- ── Execution Schedule ────────────────────────────────────────────────────
    run_frequency                   STRING  COMMENT 'daily | weekly | every_trigger',
    window_config                   STRING  COMMENT 'JSON: {"type":"post_batch"|"scheduled","timezone":"America/Los_Angeles","days":[...],"start_time":"HH:MM","duration_hours":N,"delay_minutes":N}',

    -- ── Partition Filtering ───────────────────────────────────────────────────
    partition_column                STRING  COMMENT 'Primary partition column e.g. partition_date',
    partition_filter_days           INT     COMMENT 'Process only partitions from last N days. Null = all.',

    -- ── Sort / Z-Order ────────────────────────────────────────────────────────
    sort_columns                    ARRAY<STRING> COMMENT 'Columns for sort or zorder compaction',
    glue_job_name                   STRING  COMMENT 'Glue job name for sort/zorder. Null if using Athena.',

    -- ── Audit ─────────────────────────────────────────────────────────────────
    template_applied_at             TIMESTAMP COMMENT 'When template was last applied',
    manually_overridden             BOOLEAN   COMMENT 'True if any field was changed after template application',
    override_notes                  STRING    COMMENT 'Why this table deviates from its template'

)
LOCATION 's3://your-zamboni-metadata-bucket/hk_config/'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'PARQUET',
    'write_compression' = 'SNAPPY'
);
