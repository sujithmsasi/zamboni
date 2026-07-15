-- =============================================================================
-- Vacuum Audit (Workstream A, Phase 1b — contracts.md §3.3)
-- One row per SAFE-VACUUM run (including sanity-aborts) — VP-reportable
-- audit trail for the metadata-loss incident fix. Database: zamboni_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS zamboni_catalog.vacuum_audit (

    run_id                 STRING  COMMENT 'UUID — groups all operations in one engine invocation',
    table_fqn               STRING  COMMENT 'Fully qualified table name',
    operation               STRING  COMMENT 'vacuum',

    snapshots_before        INT     COMMENT 'Snapshot count before VACUUM',
    snapshots_after         INT     COMMENT 'Snapshot count after VACUUM — null if aborted/dry_run',

    files_estimated         INT     COMMENT 'Live data file count at pre-flight ("$files")',
    files_deleted           INT     COMMENT 'Pre-flight vs post-audit file count delta',
    bytes_reclaimed         BIGINT  COMMENT 'Pre-flight vs post-audit byte count delta',

    older_than_hours_used   INT     COMMENT 'Effective vacuum_max_snapshot_age_seconds/3600 in effect at VACUUM time (the clamped floor, not a literal older_than call arg -- Athena engine v3 VACUUM takes no such parameter)',
    sanity_pct              DOUBLE  COMMENT 'would_expire_pct at pre-flight — snapshots older than the floor / total snapshots',

    aborted                  BOOLEAN COMMENT 'True if ORPHAN_SANITY_ABORT fired -- no VACUUM ran',
    aborted_reason           STRING  COMMENT 'ORPHAN_SANITY_ABORT | null',

    lock_id                  STRING  COMMENT 'Maintenance lock held for this run (table_fqn:lock_owner)',
    dry_run                  BOOLEAN COMMENT 'True if this was a dry-run evaluation -- no destructive VACUUM ran',

    started_at               TIMESTAMP COMMENT 'SAFE-VACUUM step start',
    completed_at              TIMESTAMP COMMENT 'SAFE-VACUUM step end'

)
LOCATION 's3://your-zamboni-metadata-bucket/vacuum_audit/'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'PARQUET',
    'write_compression' = 'SNAPPY'
);
