-- =============================================================================
-- Safety Core (Workstream A, Phase 1a) — schema additions for lock
-- coordination, AWS Glue optimizer conflict detection, Gate 0 override audit,
-- and post-VACUUM integrity tracking. See .claude/contracts.md §3.2 and §4.
--
-- Idempotency note: Athena's ALTER TABLE ... ADD COLUMNS fails if a column
-- already exists. Run each statement once per environment, or guard it with
-- your deployment tool's "does this column exist" check before executing.
-- =============================================================================

-- stream_registry: AWS Glue table-optimizer conflict cache
ALTER TABLE zamboni_catalog.stream_registry ADD COLUMNS (
    aws_opt_compaction  BOOLEAN,
    aws_opt_retention   BOOLEAN,
    aws_opt_orphan      BOOLEAN,
    aws_opt_checked_at  TIMESTAMP
);

-- hk_config: Gate 0 time-boxed override (contracts.md §1 D3)
ALTER TABLE zamboni_catalog.hk_config ADD COLUMNS (
    gate0_override_until  TIMESTAMP,
    gate0_override_reason STRING,
    gate0_override_by     STRING
);

-- execution_log: lock + integrity verification columns (contracts.md §5-A)
-- NOTE: execution_log has a Parquet writer mode (EXECUTION_LOG_MODE) --
-- these columns must also flow through engine/core/execution_log_parquet.py.
ALTER TABLE zamboni_catalog.execution_log ADD COLUMNS (
    lock_id                  STRING,
    metadata_location_before STRING,
    metadata_location_after  STRING,
    snapshot_id_before       BIGINT,
    snapshot_id_after        BIGINT,
    integrity_status         STRING   -- VERIFIED | FAILED | SKIPPED
);
