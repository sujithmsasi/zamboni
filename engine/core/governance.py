"""
Zamboni — Dual-Optimizer Risk Report (Governance, Workstream A / Phase 1c)

VP-facing view of tables where Zamboni HK maintenance is enabled AND an AWS
Glue table optimizer is also active -- exactly the conflict Gate 0
(contracts.md §4) refuses to run alongside. This is the reusable engine
function GET /api/conflicts (contracts.md §6, routers/gates.py) will call in
Phase 2; the Streamlit stopgap panel (app/pages/4_Health_Dashboard.py) calls
it directly for the July 17 showcase.
"""
from __future__ import annotations

from datetime import UTC, datetime

from config.settings import CONFLICT_CACHE_TTL_HOURS, HK_CONFIG_TABLE, STREAM_REGISTRY_TABLE
from engine.utils.athena_client import read_sql
from engine.utils.logger import get_logger

log = get_logger(__name__)

_EMPTY_REPORT = {"data": [], "total": 0, "page": 1, "size": 50}
_EMPTY_SUMMARY = {"total": 0, "scanned": 0, "conflicted": 0, "stale_cache": 0, "overridden": 0}

_CONFLICT_WHERE = """
    WHERE r.hk_enabled = true
      AND (r.aws_opt_compaction = true OR r.aws_opt_retention = true OR r.aws_opt_orphan = true)
"""


def dual_optimizer_report(
    page: int = 1, size: int = 50, domain: str | None = None, export_all: bool = False,
) -> dict:
    """
    stream_registry joined to hk_config where hk_enabled=true AND any
    aws_opt_* is true. export_all=True ignores paging (CSV export).
    """
    domain_clause = f" AND r.domain = '{domain}'" if domain else ""
    base = f"""
        FROM {STREAM_REGISTRY_TABLE} r
        JOIN {HK_CONFIG_TABLE} h ON r.table_fqn = h.table_fqn
        {_CONFLICT_WHERE}{domain_clause}
    """

    try:
        count_df = read_sql(f"SELECT COUNT(*) AS cnt {base}", workgroup="app")
        total = int(count_df.iloc[0]["cnt"]) if not count_df.empty else 0
    except Exception as e:
        if "column" in str(e).lower():
            log.info("governance.dual_optimizer_report.column_missing")
            return {**_EMPTY_REPORT, "page": page, "size": size}
        log.warning("governance.dual_optimizer_report.count_failed", error=str(e))
        return {**_EMPTY_REPORT, "page": page, "size": size}

    data_sql = f"""
        SELECT
            r.table_fqn, r.domain, r.layer, r.tier,
            r.aws_opt_compaction, r.aws_opt_retention, r.aws_opt_orphan,
            r.aws_opt_checked_at,
            h.gate0_override_until, h.gate0_override_reason, h.gate0_override_by
        {base}
        ORDER BY r.domain, r.table_fqn
    """
    if not export_all:
        offset = max(page - 1, 0) * size
        data_sql += f" LIMIT {int(size)} OFFSET {int(offset)}"

    try:
        df = read_sql(data_sql, workgroup="app")
    except Exception as e:
        log.warning("governance.dual_optimizer_report.data_failed", error=str(e))
        return {"data": [], "total": total, "page": page, "size": size}

    return {"data": df.to_dict(orient="records"), "total": total, "page": page, "size": size}


def fleet_conflict_summary() -> dict:
    """
    Counts for Home/Health KPIs: scanned (aws_opt_checked_at not null),
    conflicted (hk_enabled AND any aws_opt_* true), stale_cache
    (checked_at older than CONFLICT_CACHE_TTL_HOURS), overridden
    (gate0_override_until in the future).

    Staleness/override are computed in Python (age_hours from a parsed
    timestamp), not via SQL "NOW() - INTERVAL 'n' HOUR" -- the same
    convention conflict_detector.get_cached() already uses. Athena/Presto
    supports that interval syntax fine, but engine/utils/local_db.py's SQL
    translator only rewrites DAY-unit INTERVAL literals (see its
    _translate()), so an HOUR-unit clause would reach SQLite untranslated
    and fail the whole query in local mode.
    """
    sql = f"""
        SELECT
            r.aws_opt_compaction, r.aws_opt_retention, r.aws_opt_orphan,
            r.aws_opt_checked_at, h.gate0_override_until
        FROM {STREAM_REGISTRY_TABLE} r
        LEFT JOIN {HK_CONFIG_TABLE} h ON r.table_fqn = h.table_fqn
        WHERE r.hk_enabled = true
    """
    try:
        df = read_sql(sql, workgroup="app")
    except Exception as e:
        if "column" in str(e).lower():
            log.info("governance.fleet_conflict_summary.column_missing")
        else:
            log.warning("governance.fleet_conflict_summary_failed", error=str(e))
        return dict(_EMPTY_SUMMARY)

    if df.empty:
        return dict(_EMPTY_SUMMARY)

    now = datetime.now(UTC)
    scanned = conflicted = stale_cache = overridden = 0

    for _, row in df.iterrows():
        checked_at = _parse_ts(row.get("aws_opt_checked_at"))
        if checked_at is not None:
            scanned += 1
            if (now - checked_at).total_seconds() / 3600 > CONFLICT_CACHE_TTL_HOURS:
                stale_cache += 1

        if any(_truthy(row.get(c)) for c in ("aws_opt_compaction", "aws_opt_retention", "aws_opt_orphan")):
            conflicted += 1

        override_until = _parse_ts(row.get("gate0_override_until"))
        if override_until is not None and override_until > now:
            overridden += 1

    return {
        "total":       len(df),
        "scanned":     scanned,
        "conflicted":  conflicted,
        "stale_cache": stale_cache,
        "overridden":  overridden,
    }


def _truthy(v) -> bool:
    """bool(v) that treats pd.NA/NaN/None as False -- pd.NA raises on direct
    bool() coercion (see .claude/context_hints.md)."""
    if v is None:
        return False
    try:
        import pandas as pd
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    return bool(v)


def _parse_ts(value) -> datetime | None:
    """Parse a DB timestamp value (str/Timestamp/datetime) into a tz-aware datetime."""
    if value is None:
        return None
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            from dateutil import parser as dtparser
            dt = dtparser.parse(str(value))
        except Exception:
            return None
    if dt.tzinfo is None:
        from pytz import utc
        dt = utc.localize(dt)
    return dt
