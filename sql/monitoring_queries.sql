-- =============================================================================
-- Zamboni — Monitoring Queries
-- Run in Athena against glue_catalog.zamboni_catalog
-- Used by the Streamlit app and for ad-hoc investigation
-- =============================================================================


-- ── 1. Fleet Coverage ─────────────────────────────────────────────────────────
-- % of tables registered, enabled, in dry-run, by domain + layer
SELECT
    domain,
    layer,
    COUNT(*)                                                            AS total_tables,
    SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END)                 AS hk_enabled,
    SUM(CASE WHEN dry_run_until >= CURRENT_DATE THEN 1 ELSE 0 END)     AS in_dry_run,
    SUM(CASE WHEN table_format != 'iceberg' THEN 1 ELSE 0 END)         AS non_iceberg,
    ROUND(
        SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                                   AS pct_enabled
FROM glue_catalog.zamboni_catalog.stream_registry
GROUP BY domain, layer
ORDER BY domain, layer;


-- ── 2. Execution Summary — Last 7 Days ────────────────────────────────────────
SELECT
    engine,
    operation,
    status,
    COUNT(*)                            AS runs,
    SUM(snapshots_expired)              AS snapshots_expired,
    SUM(orphan_files_deleted)           AS orphans_deleted,
    ROUND(SUM(bytes_rewritten) / 1e9, 2)AS gb_rewritten,
    ROUND(AVG(duration_seconds), 0)     AS avg_duration_sec,
    ROUND(SUM(bytes_scanned) / 1e9, 2)  AS gb_scanned
FROM glue_catalog.zamboni_catalog.execution_log
WHERE execution_date >= CURRENT_DATE - INTERVAL '7' DAY
GROUP BY engine, operation, status
ORDER BY engine, operation;


-- ── 3. Tables Never Housekept ─────────────────────────────────────────────────
-- Enabled tables with no successful HK in the last 14 days
SELECT
    r.table_fqn,
    r.domain,
    r.layer,
    r.tier,
    MAX(l.completed_at) AS last_successful_hk
FROM glue_catalog.zamboni_catalog.stream_registry r
LEFT JOIN glue_catalog.zamboni_catalog.execution_log l
    ON  r.table_fqn = l.table_fqn
    AND l.status = 'SUCCESS'
    AND l.execution_date >= CURRENT_DATE - INTERVAL '14' DAY
WHERE r.hk_enabled   = true
  AND r.table_format = 'iceberg'
GROUP BY r.table_fqn, r.domain, r.layer, r.tier
HAVING MAX(l.completed_at) IS NULL
ORDER BY r.tier, r.domain;


-- ── 4. Circuit Breaker Candidates ─────────────────────────────────────────────
-- Tables with 3+ consecutive failures in last 30 days
SELECT
    table_fqn,
    domain,
    COUNT(*)            AS failure_count,
    MAX(started_at)     AS last_failure_at,
    MAX(error_message)  AS last_error
FROM glue_catalog.zamboni_catalog.execution_log
WHERE status = 'FAILURE'
  AND execution_date >= CURRENT_DATE - INTERVAL '30' DAY
GROUP BY table_fqn, domain
HAVING COUNT(*) >= 3
ORDER BY failure_count DESC;


-- ── 5. Archival Savings — Last 30 Days ───────────────────────────────────────
SELECT
    domain,
    COUNT(DISTINCT table_fqn)               AS tables_archived,
    COUNT(*)                                AS partitions_archived,
    SUM(rows_archived)                      AS total_rows,
    ROUND(SUM(bytes_archived) / 1e9, 2)    AS gb_archived,
    MIN(partition_date)                     AS oldest_partition,
    MAX(partition_date)                     AS newest_partition
FROM glue_catalog.zamboni_catalog.execution_log
WHERE engine    = 'archival'
  AND status    = 'SUCCESS'
  AND execution_date >= CURRENT_DATE - INTERVAL '30' DAY
GROUP BY domain
ORDER BY gb_archived DESC;


-- ── 6. Non-Prod Lifecycle Distribution ───────────────────────────────────────
SELECT
    environment,
    lifecycle_state,
    COUNT(*)                                AS tables,
    ROUND(AVG(days_since_activity), 0)      AS avg_days_inactive,
    ROUND(SUM(bytes_reclaimed) / 1e9, 2)   AS gb_reclaimed
FROM glue_catalog.zamboni_catalog.nonprod_registry
GROUP BY environment, lifecycle_state
ORDER BY environment, lifecycle_state;


-- ── 7. Cost by Domain — Athena Bytes Scanned ─────────────────────────────────
SELECT
    domain,
    DATE_TRUNC('month', execution_date)     AS month,
    ROUND(SUM(bytes_scanned) / 1e9, 2)     AS gb_scanned,
    ROUND(SUM(bytes_scanned) / 1e12 * 5, 4) AS estimated_athena_cost_usd
FROM glue_catalog.zamboni_catalog.execution_log
WHERE execution_date >= CURRENT_DATE - INTERVAL '90' DAY
GROUP BY domain, DATE_TRUNC('month', execution_date)
ORDER BY month DESC, gb_scanned DESC;


-- ── 8. Domain Registry Summary ───────────────────────────────────────────────
SELECT
    d.domain_name,
    d.display_name,
    d.owner_email,
    d.archive_enabled,
    d.hot_retention_days,
    COUNT(s.table_fqn)                                              AS registered_tables,
    SUM(CASE WHEN s.hk_enabled = true THEN 1 ELSE 0 END)           AS hk_enabled_tables,
    SUM(CASE WHEN s.layer = 'staging' THEN 1 ELSE 0 END)           AS staging_tables,
    SUM(CASE WHEN s.layer = 'datalake' THEN 1 ELSE 0 END)          AS datalake_tables,
    SUM(CASE WHEN s.layer = 'base' THEN 1 ELSE 0 END)              AS base_tables,
    SUM(CASE WHEN s.layer = 'master' THEN 1 ELSE 0 END)            AS master_tables
FROM glue_catalog.zamboni_catalog.domain_registry d
LEFT JOIN glue_catalog.zamboni_catalog.stream_registry s
    ON d.domain_name = s.domain
WHERE d.is_active = true
GROUP BY d.domain_name, d.display_name, d.owner_email, d.archive_enabled, d.hot_retention_days
ORDER BY d.domain_name;
