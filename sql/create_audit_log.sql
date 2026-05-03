-- =============================================================================
-- Zamboni -- Audit Log DDL
-- Iceberg table storing all significant user/system actions.
-- Partitioned by audit_date for efficient time-range queries.
-- =============================================================================
CREATE TABLE IF NOT EXISTS glue_catalog.zamboni_catalog.audit_log (

    -- ── Identity ──────────────────────────────────────────────────────────────
    audit_id            STRING  COMMENT 'Unique audit event ID (uuid)',
    timestamp           TIMESTAMP COMMENT 'UTC timestamp of the action',
    actor               STRING  COMMENT 'User who performed the action (current_user())',

    -- ── Action ────────────────────────────────────────────────────────────────
    action_type         STRING  COMMENT 'domain_create | table_register | policy_change | bulk_template_apply | hk_enable | hk_disable | dry_run_promote | run_hk_now | cancel_query | kill_switch | circuit_breaker_reenable | lifecycle_exemption | claim_table | report_export | settings_change | escalation_change',
    page_source         STRING  COMMENT 'Streamlit page or CLI module that triggered the action',

    -- ── Target ────────────────────────────────────────────────────────────────
    target_type         STRING  COMMENT 'table | domain | run | query | setting | registry',
    target_id           STRING  COMMENT 'Primary identifier: table_fqn, domain, run_id, query_id, setting_key',
    domain              STRING  COMMENT 'Business domain (for filtering)',
    environment         STRING  COMMENT 'prod | preprod | dev | test',

    -- ── Mode ──────────────────────────────────────────────────────────────────
    dry_run             BOOLEAN COMMENT 'true = simulated, false = live execution',
    status              STRING  COMMENT 'SUCCESS | FAILURE | DRY_RUN | REJECTED',

    -- ── Context ───────────────────────────────────────────────────────────────
    reason              STRING  COMMENT 'User-provided reason for the action',
    ticket_number       STRING  COMMENT 'Change/ITSM ticket number if provided',
    before_value        STRING  COMMENT 'Serialised previous state (JSON string)',
    after_value         STRING  COMMENT 'Serialised new state (JSON string)',
    error_message       STRING  COMMENT 'Error detail if status=FAILURE or REJECTED',

    -- ── Partition ─────────────────────────────────────────────────────────────
    audit_date          DATE    COMMENT 'UTC date -- partition key'
)
LOCATION 's3://your-zamboni-metadata/audit_log/'
TBLPROPERTIES (
    'table_type'                          = 'ICEBERG',
    'format'                              = 'PARQUET',
    'write_compression'                   = 'SNAPPY',
    'partitioning'                        = 'audit_date',
    'vacuum_max_snapshot_age_seconds'     = '604800',
    'vacuum_min_snapshots_to_keep'        = '30'
);
