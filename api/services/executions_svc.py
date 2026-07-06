"""
Zamboni API -- executions service (contracts.md §6 routers/executions.py).

Lifts query logic from app/pages/7_Execution_Log.py (list/drill-in),
6_Dry_Run_Viewer.py (gate summary + planned-ops preview), 4_Health_Dashboard.py
+ Home KPI queries, 8_Cost_Report.py (cost by group), and
10_Stale_Resources.py (stale/zero-row/nonprod-stale scans).
"""
from __future__ import annotations

from config.settings import (
    EXECUTION_LOG_TABLE,
    HK_CONFIG_TABLE,
    NONPROD_REGISTRY_TABLE,
    S3_STANDARD_USD_PER_GB_MONTH,
    STREAM_REGISTRY_TABLE,
    VACUUM_AUDIT_TABLE,
)
from engine.core.cost_explorer import get_athena_cost
from engine.core.cost_explorer import is_enabled as cost_explorer_enabled
from engine.core.governance import fleet_conflict_summary
from engine.core.window_evaluator import EXECUTE, evaluate
from engine.strategies.binpack import build_optimize_sql
from engine.utils.athena_client import read_sql
from engine.utils.partition_utils import build_hot_partition_filter


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


# ── executions list / detail ─────────────────────────────────────────────────

def list_executions(
    page: int, size: int, fqn: str | None = None, engine: str | None = None,
    status: str | None = None, from_date: str | None = None, to_date: str | None = None,
) -> tuple[list[dict], int]:
    conditions = []
    if fqn:
        conditions.append(f"table_fqn = '{_esc(fqn)}'")
    if engine:
        conditions.append(f"engine = '{_esc(engine)}'")
    if status:
        conditions.append(f"status = '{_esc(status)}'")
    if from_date:
        conditions.append(f"execution_date >= '{_esc(from_date)}'")
    if to_date:
        conditions.append(f"execution_date <= '{_esc(to_date)}'")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total_df = read_sql(f"SELECT COUNT(*) AS cnt FROM {EXECUTION_LOG_TABLE} {where}", workgroup="app")
    total = int(total_df.iloc[0]["cnt"]) if not total_df.empty else 0

    offset = max(page - 1, 0) * size
    sql = f"""
        SELECT * FROM {EXECUTION_LOG_TABLE} {where}
        ORDER BY started_at DESC
        LIMIT {int(size)} OFFSET {int(offset)}
    """
    df = read_sql(sql, workgroup="app")
    return df.to_dict(orient="records"), total


def get_execution(execution_id: str) -> dict | None:
    df = read_sql(
        f"SELECT * FROM {EXECUTION_LOG_TABLE} WHERE execution_id = '{_esc(execution_id)}' LIMIT 1",
        workgroup="app",
    )
    if df.empty:
        return None
    return df.iloc[0].to_dict()


# ── dry-run viewer ────────────────────────────────────────────────────────────

def dry_run_view(fqn: str) -> dict | None:
    reg_df = read_sql(f"SELECT * FROM {STREAM_REGISTRY_TABLE} WHERE table_fqn = '{_esc(fqn)}' LIMIT 1", workgroup="app")
    if reg_df.empty:
        return None
    reg = reg_df.iloc[0].to_dict()

    cfg_df = read_sql(f"SELECT * FROM {HK_CONFIG_TABLE} WHERE table_fqn = '{_esc(fqn)}' LIMIT 1", workgroup="app")
    cfg = cfg_df.iloc[0].to_dict() if not cfg_df.empty else {}

    window_json = cfg.get("window_config") or ""
    decision = evaluate(window_json, force=bool(reg.get("force_run", False))) if window_json else EXECUTE

    upstream = reg.get("dependent_on_controlm_job") or reg.get("controlm_pipeline_job") or reg.get("dependent_job_name")
    gates = {
        "gate1_enabled": bool(cfg.get("gate1_enabled", 0)),
        "gate2_enabled": bool(cfg.get("gate2_enabled", 1)),
        "gate3_enabled": bool(cfg.get("gate3_enabled", 1)),
        "upstream_job": upstream,
        "window_decision": decision,
    }

    sql_preview = None
    if cfg.get("compaction_strategy") == "binpack":
        part_col = cfg.get("partition_column")
        part_type = cfg.get("partition_type", "date") or "date"
        no_filter = part_type in ("none", "identity") or not part_col
        part_filter = (
            build_hot_partition_filter(part_col, days=cfg.get("partition_filter_days"), partition_type=part_type)
            if not no_filter else None
        )
        sql_preview = build_optimize_sql(
            table_fqn=fqn,
            target_file_size_mb=cfg.get("compaction_target_file_size_mb", 128),
            partition_filter=part_filter,
        )

    return {
        "table_fqn": fqn,
        "registration": reg,
        "config": cfg,
        "gates": gates,
        "planned_sql": sql_preview,
    }


# ── health KPIs ───────────────────────────────────────────────────────────────

def health_kpis(env: str = "prod", domain: str | None = None) -> dict:
    conditions = [f"environment = '{_esc(env)}'"]
    if domain:
        conditions.append(f"domain = '{_esc(domain)}'")
    where = "WHERE " + " AND ".join(conditions)

    fleet_sql = f"""
        SELECT
            COUNT(*)                                                AS total,
            SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END)     AS enabled,
            SUM(CASE WHEN table_format = 'iceberg' THEN 1 ELSE 0 END) AS iceberg,
            SUM(CASE WHEN dry_run_until >= CURRENT_DATE THEN 1 ELSE 0 END) AS in_dry_run
        FROM {STREAM_REGISTRY_TABLE} {where}
    """
    fleet_df = read_sql(fleet_sql, workgroup="app")
    frow = fleet_df.iloc[0].to_dict() if not fleet_df.empty else {}

    fail_sql = f"""
        SELECT COUNT(*) AS cnt FROM {EXECUTION_LOG_TABLE}
        WHERE status = 'FAILURE' AND execution_date >= CURRENT_DATE - INTERVAL '7' DAY
    """
    fail_df = read_sql(fail_sql, workgroup="app")
    failures_7d = int(fail_df.iloc[0]["cnt"]) if not fail_df.empty else 0

    # Same aggregate queries 4_Health_Dashboard.py already runs -- relocated
    # here (not duplicated) so the VP-level Home charts and the Health
    # Dashboard read one shared source of truth, per this route's own
    # contracts.md §6 description ("home + health dashboard numbers").
    coverage_sql = f"""
        SELECT
            domain,
            COUNT(*)                                                AS total,
            SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END)     AS enabled,
            ROUND(SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS pct_enabled
        FROM {STREAM_REGISTRY_TABLE} {where}
        GROUP BY domain
        ORDER BY pct_enabled DESC
    """
    coverage_df = read_sql(coverage_sql, workgroup="app")

    trend_sql = f"""
        SELECT execution_date, status, COUNT(*) AS count
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '14' DAY
          AND engine = 'hk'
        GROUP BY execution_date, status
        ORDER BY execution_date
    """
    trend_df = read_sql(trend_sql, workgroup="app")

    return {
        "total_registered": int(frow.get("total") or 0),
        "hk_enabled": int(frow.get("enabled") or 0),
        "iceberg_tables": int(frow.get("iceberg") or 0),
        "in_dry_run": int(frow.get("in_dry_run") or 0),
        "failures_7d": failures_7d,
        "conflicts": fleet_conflict_summary(),
        "coverage_by_domain": coverage_df.to_dict(orient="records"),
        "execution_trend": trend_df.to_dict(orient="records"),
        "reclaimed_storage_trend": _reclaimed_storage_trend(),
        "top_tables_by_reclaim": _top_tables_by_reclaim(),
        "cost_trend": _cost_trend(),
        "storage_savings": _storage_savings_estimate(),
        "fleet_health": _fleet_health_summary(),
        "nonprod_funnel": _nonprod_lifecycle_funnel(),
        "dry_run_adoption": _dry_run_adoption(),
    }


# ── Health Dashboard detail (relocated from 4_Health_Dashboard.py, extended) ──
# All of the below back the Health Dashboard's "major uplift" charts. Kept as
# private helpers of health_kpis() rather than a second endpoint, since
# contracts.md §6 documents GET /api/health/kpis as serving both Home and the
# Health Dashboard from one shared source of truth.

def _reclaimed_storage_trend(days: int = 30) -> list[dict]:
    """
    Daily GB genuinely freed (not just rewritten): vacuum's orphan/snapshot
    removal + archival's move-to-cheaper-tier. Compaction's bytes_rewritten
    is deliberately excluded -- it rewrites files for query performance, it
    doesn't reduce total bytes, so it isn't a "reclaim" in the storage-cost
    sense (see decisions.md).
    """
    vacuum_sql = f"""
        SELECT SUBSTR(completed_at, 1, 10) AS day, SUM(bytes_reclaimed) AS bytes_reclaimed
        FROM {VACUUM_AUDIT_TABLE}
        WHERE aborted = false AND dry_run = false
          AND completed_at >= CURRENT_DATE - INTERVAL '{int(days)}' DAY
        GROUP BY SUBSTR(completed_at, 1, 10)
    """
    archive_sql = f"""
        SELECT execution_date AS day, SUM(bytes_archived) AS bytes_archived
        FROM {EXECUTION_LOG_TABLE}
        WHERE engine = 'archival' AND status = 'SUCCESS'
          AND execution_date >= CURRENT_DATE - INTERVAL '{int(days)}' DAY
        GROUP BY execution_date
    """
    vacuum_df = read_sql(vacuum_sql, workgroup="app")
    archive_df = read_sql(archive_sql, workgroup="app")

    by_day: dict[str, dict] = {}
    for _, row in vacuum_df.iterrows():
        day = str(row["day"])
        by_day.setdefault(day, {"day": day, "vacuum_gb": 0.0, "archived_gb": 0.0})
        by_day[day]["vacuum_gb"] = round((row["bytes_reclaimed"] or 0) / 1e9, 2)
    for _, row in archive_df.iterrows():
        day = str(row["day"])
        by_day.setdefault(day, {"day": day, "vacuum_gb": 0.0, "archived_gb": 0.0})
        by_day[day]["archived_gb"] = round((row["bytes_archived"] or 0) / 1e9, 2)

    return sorted(by_day.values(), key=lambda r: r["day"])


def _top_tables_by_reclaim(days: int = 30, limit: int = 10) -> list[dict]:
    vacuum_sql = f"""
        SELECT v.table_fqn, r.domain, SUM(v.bytes_reclaimed) AS bytes_reclaimed
        FROM {VACUUM_AUDIT_TABLE} v
        LEFT JOIN {STREAM_REGISTRY_TABLE} r ON v.table_fqn = r.table_fqn
        WHERE v.aborted = false AND v.dry_run = false
          AND v.completed_at >= CURRENT_DATE - INTERVAL '{int(days)}' DAY
        GROUP BY v.table_fqn, r.domain
    """
    archive_sql = f"""
        SELECT table_fqn, domain, SUM(bytes_archived) AS bytes_archived
        FROM {EXECUTION_LOG_TABLE}
        WHERE engine = 'archival' AND status = 'SUCCESS'
          AND execution_date >= CURRENT_DATE - INTERVAL '{int(days)}' DAY
        GROUP BY table_fqn, domain
    """
    vacuum_df = read_sql(vacuum_sql, workgroup="app")
    archive_df = read_sql(archive_sql, workgroup="app")

    by_table: dict[str, dict] = {}
    for _, row in vacuum_df.iterrows():
        fqn = row["table_fqn"]
        by_table.setdefault(fqn, {"table_fqn": fqn, "domain": row.get("domain") or "", "bytes": 0})
        by_table[fqn]["bytes"] += row["bytes_reclaimed"] or 0
    for _, row in archive_df.iterrows():
        fqn = row["table_fqn"]
        by_table.setdefault(fqn, {"table_fqn": fqn, "domain": row.get("domain") or "", "bytes": 0})
        by_table[fqn]["domain"] = row.get("domain") or by_table[fqn]["domain"]
        by_table[fqn]["bytes"] += row["bytes_archived"] or 0

    ranked = sorted(by_table.values(), key=lambda r: r["bytes"], reverse=True)[:limit]
    return [{"table_fqn": r["table_fqn"], "domain": r["domain"], "gb_reclaimed": round(r["bytes"] / 1e9, 2)} for r in ranked]


def _cost_trend(days: int = 30) -> list[dict]:
    sql = f"""
        SELECT execution_date, ROUND(SUM(bytes_scanned) / 1e12 * 5, 4) AS cost_usd
        FROM {EXECUTION_LOG_TABLE}
        WHERE status = 'SUCCESS' AND execution_date >= CURRENT_DATE - INTERVAL '{int(days)}' DAY
        GROUP BY execution_date
        ORDER BY execution_date
    """
    return read_sql(sql, workgroup="app").to_dict(orient="records")


def _storage_savings_estimate() -> dict:
    """
    All-time (not just the 30d trend window) reclaimed capacity, priced at
    the flat S3_STANDARD_USD_PER_GB_MONTH estimate -- an illustrative
    "ongoing monthly savings" figure, same estimation convention as costs()'s
    $5/TB Athena scan-cost number. Not a live AWS Cost Explorer figure.
    """
    vacuum_sql = f"SELECT SUM(bytes_reclaimed) AS b FROM {VACUUM_AUDIT_TABLE} WHERE aborted = false AND dry_run = false"
    archive_sql = f"SELECT SUM(bytes_archived) AS b FROM {EXECUTION_LOG_TABLE} WHERE engine = 'archival' AND status = 'SUCCESS'"
    vacuum_df = read_sql(vacuum_sql, workgroup="app")
    archive_df = read_sql(archive_sql, workgroup="app")
    vacuum_bytes = int((vacuum_df.iloc[0]["b"] or 0) if not vacuum_df.empty else 0)
    archive_bytes = int((archive_df.iloc[0]["b"] or 0) if not archive_df.empty else 0)
    total_gb = round((vacuum_bytes + archive_bytes) / 1e9, 1)
    return {
        "total_gb_reclaimed": total_gb,
        "estimated_monthly_savings_usd": round(total_gb * S3_STANDARD_USD_PER_GB_MONTH, 2),
    }


def _fleet_health_summary() -> dict:
    """
    Proxy health classification from queryable signals -- deliberately not
    engine/core/health_checker.py::check() (that needs a live Iceberg
    $snapshots/$files call per table; fine for one table in the Dry Run
    Viewer, too expensive to run fleet-wide on every Home/Health load).
    AT_RISK: an integrity check failed in the last 7d, a dual-optimizer
    conflict is active, or the table hasn't had a successful run in 14d.
    NEEDS_ATTENTION: at least one failure in 7d but otherwise fine.
    HEALTHY: everything else.
    """
    sql = f"""
        SELECT
            r.table_fqn, r.domain, r.layer, r.tier,
            r.aws_opt_compaction, r.aws_opt_retention, r.aws_opt_orphan,
            SUM(CASE WHEN l.status = 'FAILURE' AND l.execution_date >= CURRENT_DATE - INTERVAL '7' DAY THEN 1 ELSE 0 END) AS failures_7d,
            SUM(CASE WHEN l.integrity_status = 'FAILED' AND l.execution_date >= CURRENT_DATE - INTERVAL '7' DAY THEN 1 ELSE 0 END) AS integrity_failures_7d,
            SUM(CASE WHEN l.status = 'SUCCESS' AND l.execution_date >= CURRENT_DATE - INTERVAL '14' DAY THEN 1 ELSE 0 END) AS housekept_recently
        FROM {STREAM_REGISTRY_TABLE} r
        LEFT JOIN {EXECUTION_LOG_TABLE} l ON r.table_fqn = l.table_fqn
        WHERE r.hk_enabled = true AND r.table_format = 'iceberg' AND r.environment = 'prod'
        GROUP BY r.table_fqn, r.domain, r.layer, r.tier, r.aws_opt_compaction, r.aws_opt_retention, r.aws_opt_orphan
    """
    df = read_sql(sql, workgroup="app")

    healthy = needs_attention = at_risk = 0
    flagged: list[dict] = []
    for _, row in df.iterrows():
        conflicted = _truthy_any(row.get("aws_opt_compaction"), row.get("aws_opt_retention"), row.get("aws_opt_orphan"))
        integrity_failed = int(row.get("integrity_failures_7d") or 0) > 0
        never_recent = int(row.get("housekept_recently") or 0) == 0
        failed_7d = int(row.get("failures_7d") or 0) > 0

        if integrity_failed or conflicted or never_recent:
            at_risk += 1
            reasons = []
            if integrity_failed:
                reasons.append("integrity check failed (7d)")
            if conflicted:
                reasons.append("AWS optimizer conflict")
            if never_recent:
                reasons.append("no successful run in 14d")
            flagged.append({
                "table_fqn": row["table_fqn"], "domain": row["domain"], "layer": row["layer"], "tier": row["tier"],
                "status": "AT_RISK", "reason": ", ".join(reasons),
            })
        elif failed_7d:
            needs_attention += 1
            flagged.append({
                "table_fqn": row["table_fqn"], "domain": row["domain"], "layer": row["layer"], "tier": row["tier"],
                "status": "NEEDS_ATTENTION", "reason": f"{int(row['failures_7d'])} failure(s) in 7d",
            })
        else:
            healthy += 1

    flagged.sort(key=lambda r: (r["status"] != "AT_RISK", r["table_fqn"]))
    return {"healthy": healthy, "needs_attention": needs_attention, "at_risk": at_risk, "tables": flagged[:50]}


def _truthy_any(*values) -> bool:
    return any(bool(v) and str(v).lower() not in ("0", "false", "none", "nan") for v in values if v is not None)


def _nonprod_lifecycle_funnel() -> list[dict]:
    sql = f"SELECT lifecycle_state, COUNT(*) AS count FROM {NONPROD_REGISTRY_TABLE} GROUP BY lifecycle_state"
    return read_sql(sql, workgroup="app").to_dict(orient="records")


def _dry_run_adoption(stale_days: int = 30) -> list[dict]:
    """
    Not a historical trend -- the schema has no "graduated_at" event to plot
    a real trend line against. Instead: which domains have the most tables
    still sitting in dry-run, and how long the *oldest* has been waiting --
    a direct answer to "which teams are hesitant to turn HK on," per Sujith's
    framing, without fabricating data the engine doesn't capture.
    """
    sql = f"""
        SELECT
            domain,
            COUNT(*) AS tables_in_dry_run,
            MAX(DATE_DIFF('day', registered_at, NOW())) AS max_days_waiting
        FROM {STREAM_REGISTRY_TABLE}
        WHERE dry_run_until >= CURRENT_DATE
        GROUP BY domain
        ORDER BY max_days_waiting DESC
    """
    df = read_sql(sql, workgroup="app")
    rows = df.to_dict(orient="records")
    for row in rows:
        row["stale"] = int(row.get("max_days_waiting") or 0) > stale_days
    return rows


# ── costs ─────────────────────────────────────────────────────────────────────

def costs(group_by: str = "domain", from_days: int = 30) -> dict:
    if group_by not in ("domain", "layer", "tier"):
        group_by = "domain"

    group_sql = f"""
        SELECT
            {group_by},
            ROUND(SUM(bytes_scanned) / 1e12 * 5, 4) AS cost_usd,
            ROUND(SUM(bytes_scanned) / 1e9, 1)       AS gb_scanned
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '{int(from_days)}' DAY
          AND status = 'SUCCESS'
        GROUP BY {group_by}
        ORDER BY cost_usd DESC
    """
    group_df = read_sql(group_sql, workgroup="app")

    totals_sql = f"""
        SELECT
            ROUND(SUM(bytes_scanned) / 1e12 * 5, 2)  AS estimated_athena_cost_usd,
            ROUND(SUM(bytes_scanned) / 1e9, 1)        AS gb_scanned,
            ROUND(SUM(bytes_rewritten) / 1e9, 1)      AS gb_compacted,
            ROUND(SUM(bytes_archived) / 1e9, 1)       AS gb_archived
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '{int(from_days)}' DAY
          AND status IN ('SUCCESS', 'DRY_RUN')
    """
    totals_df = read_sql(totals_sql, workgroup="app")
    totals = totals_df.iloc[0].to_dict() if not totals_df.empty else {}

    result = {
        "group_by": group_by,
        "totals": totals,
        "by_group": group_df.to_dict(orient="records"),
        "cost_explorer_enabled": cost_explorer_enabled(),
    }
    if cost_explorer_enabled():
        result["live_billing"] = get_athena_cost(days=from_days)
    return result


# ── stale resources ───────────────────────────────────────────────────────────

def stale(kind: str, domain: str | None = None, days: int = 30, threshold: int = 0) -> list[dict]:
    domain_clause = f"AND domain = '{_esc(domain)}'" if domain else ""

    if kind == "hk":
        sql = f"""
            SELECT
                r.table_fqn, r.domain, r.layer, r.tier, r.environment, r.hk_enabled,
                MAX(l.completed_at) AS last_successful_hk
            FROM {STREAM_REGISTRY_TABLE} r
            LEFT JOIN {EXECUTION_LOG_TABLE} l
                ON r.table_fqn = l.table_fqn AND l.status = 'SUCCESS'
            WHERE r.environment = 'prod' AND r.table_format = 'iceberg' {domain_clause.replace("domain", "r.domain")}
            GROUP BY r.table_fqn, r.domain, r.layer, r.tier, r.environment, r.hk_enabled
            HAVING MAX(l.completed_at) IS NULL
            ORDER BY r.table_fqn
            LIMIT 200
        """
        return read_sql(sql, workgroup="app").to_dict(orient="records")

    if kind == "zero_row":
        sql = f"""
            SELECT table_fqn, domain, layer,
                   MAX(rows_archived) AS last_rows_archived,
                   MAX(partition_date) AS last_partition_archived
            FROM {EXECUTION_LOG_TABLE}
            WHERE engine = 'archival' AND status = 'SUCCESS' {domain_clause}
            GROUP BY table_fqn, domain, layer
            HAVING MAX(rows_archived) <= {int(threshold)}
            ORDER BY last_rows_archived ASC
            LIMIT 100
        """
        return read_sql(sql, workgroup="app").to_dict(orient="records")

    if kind == "nonprod":
        sql = f"""
            SELECT table_fqn, domain, environment, lifecycle_state,
                   days_since_activity, last_query_at, last_write_at,
                   created_at, is_backup_pattern
            FROM {NONPROD_REGISTRY_TABLE}
            WHERE lifecycle_state IN ('STALE_CANDIDATE', 'GREENZONE', 'PENDING_DROP') {domain_clause}
            ORDER BY lifecycle_state, days_since_activity DESC
            LIMIT 100
        """
        return read_sql(sql, workgroup="app").to_dict(orient="records")

    if kind == "orphan":
        # Orphaned S3 prefixes requires a live bucket scan (no query-based
        # equivalent) -- CloudTrail-backed cross-reference is a Phase 2+
        # follow-up per 10_Stale_Resources.py's own caption. Empty here.
        return []

    raise ValueError(f"Unknown stale kind '{kind}'. Must be one of: hk, orphan, zero_row, nonprod.")
