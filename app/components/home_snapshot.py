"""
Zamboni — Home Snapshot Generator
Daily cached snapshot logic for the Home page.

First user of the day → triggers generation → saves to home_snapshot table
All other users for that day → read cached snapshot
Manual refresh button → regenerates snapshot
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime

from config.settings import (
    EXECUTION_LOG_TABLE,
    HOME_SNAPSHOT_TABLE,
    STREAM_REGISTRY_TABLE,
)
from engine.utils.athena_client import read_sql, run_query
from engine.utils.logger import get_logger

log = get_logger(__name__)

def get_or_generate(force_refresh: bool = False, generated_by: str = "unknown") -> tuple[dict, str]:
    """
    Return today's home snapshot. Generate if missing or force_refresh.

    Returns:
        (snapshot_dict, source) — source is 'cached' or 'fresh'
    """
    today = date.today()

    if not force_refresh:
        cached = _load_snapshot(today)
        if cached:
            log.info("home_snapshot.cache_hit", date=str(today))
            return cached, "cached"

    log.info("home_snapshot.generating", date=str(today), by=generated_by)
    snapshot = _generate(today)
    _save_snapshot(today, snapshot, generated_by)
    return snapshot, "fresh"


# ── Generation ────────────────────────────────────────────────────────────────

def _generate(snapshot_date: date) -> dict:
    """Run all home page queries and assemble the snapshot dict."""
    return {
        "snapshot_date":         snapshot_date.isoformat(),
        "generated_at":          datetime.now(UTC).isoformat(),
        "kpi":                   _kpi_cards(),
        "fleet_coverage":        _fleet_coverage(),
        "compaction_needed":     _compaction_needed(),
        "recent_failures":       _recent_failures(),
        "domain_stats":          _domain_stats(),
        "cost_summary":          _cost_summary(),
    }


def _kpi_cards() -> dict:
    """Top 4 KPI cards on home."""
    sql = f"""
        SELECT
            COUNT(*)                                          AS total_tables,
            SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END) AS hk_enabled
        FROM {STREAM_REGISTRY_TABLE}
        WHERE table_format = 'iceberg'
    """
    try:
        df = read_sql(sql, workgroup="app")
        total = int(df.iloc[0].get("total_tables") or 0)
        enabled = int(df.iloc[0].get("hk_enabled") or 0)
    except Exception as e:
        log.warning("kpi.registry_query_failed", error=str(e))
        total, enabled = 0, 0

    sql_failures = f"""
        SELECT COUNT(*) AS failures
        FROM {EXECUTION_LOG_TABLE}
        WHERE status = 'FAILURE'
          AND execution_date >= CURRENT_DATE - INTERVAL '7' DAY
    """
    try:
        df = read_sql(sql_failures, workgroup="app")
        failures = int(df.iloc[0].get("failures") or 0)
    except Exception:
        failures = 0

    sql_bytes = f"""
        SELECT
            COALESCE(SUM(bytes_archived), 0) + COALESCE(SUM(bytes_rewritten), 0) AS bytes_reclaimed
        FROM {EXECUTION_LOG_TABLE}
        WHERE status         = 'SUCCESS'
          AND execution_date >= CURRENT_DATE - INTERVAL '30' DAY
    """
    try:
        df = read_sql(sql_bytes, workgroup="app")
        bytes_reclaimed = int(df.iloc[0].get("bytes_reclaimed") or 0)
    except Exception:
        bytes_reclaimed = 0

    return {
        "total_tables":      total,
        "hk_enabled":        enabled,
        "failures_7d":       failures,
        "bytes_reclaimed_30d": bytes_reclaimed,
    }


def _fleet_coverage() -> list:
    sql = f"""
        SELECT
            domain,
            layer,
            COUNT(*) AS total,
            SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END) AS enabled,
            SUM(CASE WHEN dry_run_until >= CURRENT_DATE THEN 1 ELSE 0 END) AS in_dry_run
        FROM {STREAM_REGISTRY_TABLE}
        GROUP BY domain, layer
        ORDER BY domain, layer
    """
    try:
        df = read_sql(sql, workgroup="app")
        return df.to_dict(orient="records")
    except Exception as e:
        log.warning("fleet_coverage.failed", error=str(e))
        return []


def _compaction_needed() -> list:
    """Top 20 tables with most snapshots — proxy for needing HK."""
    sql = f"""
        SELECT
            r.table_fqn,
            r.domain,
            r.layer,
            r.tier,
            r.hk_enabled
        FROM {STREAM_REGISTRY_TABLE} r
        WHERE r.hk_enabled    = true
          AND r.table_format  = 'iceberg'
          AND r.environment   = 'prod'
        ORDER BY r.tier, r.domain
        LIMIT 20
    """
    try:
        df = read_sql(sql, workgroup="app")
        return df.to_dict(orient="records")
    except Exception:
        return []


def _recent_failures() -> list:
    sql = f"""
        SELECT
            table_fqn, domain, engine, operation,
            error_message, started_at
        FROM {EXECUTION_LOG_TABLE}
        WHERE status = 'FAILURE'
          AND execution_date >= CURRENT_DATE - INTERVAL '7' DAY
        ORDER BY started_at DESC
        LIMIT 20
    """
    try:
        df = read_sql(sql, workgroup="app")
        return df.to_dict(orient="records")
    except Exception:
        return []


def _domain_stats() -> list:
    sql = f"""
        SELECT
            domain,
            COUNT(*)                                     AS total_tables,
            SUM(CASE WHEN hk_enabled = true THEN 1 ELSE 0 END) AS hk_enabled,
            SUM(CASE WHEN archive_enabled = true THEN 1 ELSE 0 END) AS archive_enabled
        FROM {STREAM_REGISTRY_TABLE}
        WHERE environment = 'prod'
        GROUP BY domain
        ORDER BY domain
    """
    try:
        df = read_sql(sql, workgroup="app")
        return df.to_dict(orient="records")
    except Exception:
        return []


def _cost_summary() -> list:
    sql = f"""
        SELECT
            domain,
            ROUND(SUM(bytes_scanned) / 1e9, 2)      AS gb_scanned,
            ROUND(SUM(bytes_scanned) / 1e12 * 5, 4) AS estimated_cost_usd,
            COUNT(*)                                 AS run_count
        FROM {EXECUTION_LOG_TABLE}
        WHERE execution_date >= CURRENT_DATE - INTERVAL '30' DAY
          AND status         = 'SUCCESS'
        GROUP BY domain
        ORDER BY gb_scanned DESC NULLS LAST
        LIMIT 10
    """
    try:
        df = read_sql(sql, workgroup="app")
        return df.to_dict(orient="records")
    except Exception:
        return []


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_snapshot(snapshot_date: date) -> dict | None:
    sql = f"""
        SELECT * FROM {HOME_SNAPSHOT_TABLE}
        WHERE snapshot_date = DATE '{snapshot_date.isoformat()}'
        LIMIT 1
    """
    try:
        df = read_sql(sql, workgroup="app")
        if df.empty:
            return None
        row = df.iloc[0]
        def _si(v, default=0):
            """Safe int — returns default if v is non-numeric string."""
            try:
                return int(v or default)
            except (ValueError, TypeError):
                return default

        return {
            "snapshot_date":     str(row.get("snapshot_date", "")),
            "generated_at":      str(row.get("generated_at", "")),
            "generated_by":      str(row.get("generated_by", "") or ""),
            "kpi": {
                "total_tables":        _si(row.get("total_tables")),
                "hk_enabled":          _si(row.get("hk_enabled_count")),
                "failures_7d":         _si(row.get("failures_today") or row.get("failures_7d")),
                "bytes_reclaimed_30d": _si(row.get("bytes_reclaimed_30d")),
            },
            "fleet_coverage":    _safe_json(row.get("fleet_coverage_json")),
            "compaction_needed": _safe_json(row.get("compaction_needed_json")),
            "recent_failures":   _safe_json(row.get("recent_failures_json")),
            "domain_stats":      _safe_json(row.get("domain_stats_json")),
            "cost_summary":      _safe_json(row.get("cost_summary_json")),
        }
    except Exception as e:
        log.warning("home_snapshot.load_failed", error=str(e))
        return None


def _save_snapshot(snapshot_date: date, snapshot: dict, generated_by: str) -> None:
    """Save snapshot to Iceberg table. Delete existing row for same date first."""
    delete_sql = f"""
        DELETE FROM {HOME_SNAPSHOT_TABLE}
        WHERE snapshot_date = DATE '{snapshot_date.isoformat()}'
    """
    try:
        run_query(delete_sql, workgroup="app")
    except Exception:
        pass  # Probably no row yet

    kpi = snapshot.get("kpi", {})
    _now_str = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    insert_sql = f"""
        INSERT INTO {HOME_SNAPSHOT_TABLE} (
            snapshot_date, generated_at, generated_by,
            total_tables, hk_enabled_count,
            failures_7d, bytes_reclaimed_30d,
            fleet_coverage_json, compaction_needed_json,
            recent_failures_json, domain_stats_json,
            cost_summary_json
        ) VALUES (
            '{snapshot_date.isoformat()}',
            '{_now_str}',
            '{_esc(generated_by)}',
            {kpi.get('total_tables', 0)},
            {kpi.get('hk_enabled', 0)},
            {kpi.get('failures_7d', 0)},
            {kpi.get('bytes_reclaimed_30d', 0)},
            '{_esc(json.dumps(snapshot.get('fleet_coverage', []), default=str))}',
            '{_esc(json.dumps(snapshot.get('compaction_needed', []), default=str))}',
            '{_esc(json.dumps(snapshot.get('recent_failures', []), default=str))}',
            '{_esc(json.dumps(snapshot.get('domain_stats', []), default=str))}',
            '{_esc(json.dumps(snapshot.get('cost_summary', []), default=str))}'
        )
    """
    try:
        run_query(insert_sql, workgroup="app")
        log.info("home_snapshot.saved", date=str(snapshot_date))
    except Exception as e:
        log.error("home_snapshot.save_failed", error=str(e))


def _safe_json(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return []


def _esc(value: str) -> str:
    return str(value).replace("'", "''")
