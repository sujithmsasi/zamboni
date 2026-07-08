-- =============================================================================
-- Stream Registry
-- Core governance table. Every Iceberg table managed by Zamboni is registered here.
-- Database: zamboni_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS glue_catalog.zamboni_catalog.stream_registry (

    -- ── Identity ──────────────────────────────────────────────────────────────
    table_fqn               STRING  COMMENT 'Fully qualified: glue_catalog.database.table',
    domain                  STRING  COMMENT 'FK to domain_registry.domain_name',
    layer                   STRING  COMMENT 'staging | datalake | base | master',
    tier                    STRING  COMMENT 'critical | standard | low',
    table_format            STRING  COMMENT 'iceberg | hudi | delta | external | hive. HK Engine processes iceberg only.',
    environment             STRING  COMMENT 'prod | preprod | dev | test',

    -- ── Ownership ─────────────────────────────────────────────────────────────
    owner_email             STRING  COMMENT 'Owning team email or distribution list',
    ci_number               STRING  COMMENT 'ITSM Configuration Item number',

    -- ── HK Engine Flags ───────────────────────────────────────────────────────
    hk_enabled              BOOLEAN COMMENT 'Master switch — false = excluded from all HK runs',
    dry_run_until           DATE    COMMENT 'HK evaluates but does not write until this date passes',
    force_run               BOOLEAN COMMENT 'Override safe window check. Use carefully.',

    -- ── Gate 1 — Upstream Dependency ─────────────────────────────────────────
    dependent_job_name      STRING  COMMENT 'Upstream Glue job name for batch completion check',
    dependent_job_type      STRING  COMMENT 'glue | none',

    -- ── Control-M Integration ─────────────────────────────────────────────────
    controlm_job_name       STRING  COMMENT 'HK job name in Control-M',
    dependent_on_controlm_job STRING COMMENT 'Upstream Control-M job that must complete before HK runs',

    -- ── Archival Engine Flags ─────────────────────────────────────────────────
    archive_enabled         BOOLEAN COMMENT 'Enable Export-then-Delete archival. Staging layer only.',
    archive_retention_days  INT     COMMENT 'Days to retain in hot staging. Older partitions archived.',
    archive_bucket          STRING  COMMENT 'Override archive S3 URI. Null = use ARCHIVE_BUCKET from settings.',

    -- ── Lifecycle Engine Flags (non-prod) ─────────────────────────────────────
    lifecycle_enabled       BOOLEAN COMMENT 'Enable Lifecycle Engine for this table (non-prod only)',

    -- ── v2 Design Fields ──────────────────────────────────────────────────────
    processing_cadence      STRING  COMMENT 'hourly | daily | weekly | monthly',
    partition_column        STRING  COMMENT 'Primary partition column (denormalised from hk_config for quick access)',
    partition_type          STRING  COMMENT 'date | timestamp | int_yyyymmdd | string | identity | none'. Drives hot partition filter window.',
    properties_synced       BOOLEAN COMMENT 'true if vacuum_max_snapshot_age_seconds + vacuum_min_snapshots_to_keep ALTER TABLE has been applied',
    last_execution_id       STRING  COMMENT 'Last successful execution ID — used for idempotency dedupe',

    -- ── Audit ─────────────────────────────────────────────────────────────────
    registered_at           TIMESTAMP COMMENT 'First registered',
    registered_by           STRING    COMMENT 'cli | streamlit | auto-discovery',
    updated_at              TIMESTAMP COMMENT 'Last config update',
    notes                   STRING    COMMENT 'Free text context'

)
LOCATION 's3://your-zamboni-metadata-bucket/stream_registry/'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'PARQUET',
    'write_compression' = 'SNAPPY',
    'optimize_rewrite_delete_file_threshold' = '10'
);
