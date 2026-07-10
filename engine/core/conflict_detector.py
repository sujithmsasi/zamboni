"""
Zamboni — Conflict Detector (Gate 0 support, contracts.md §4)

Detects whether AWS Glue's built-in table optimizer (compaction, retention,
orphan_file_deletion) is enabled on a table, so Gate 0 can refuse to run
Zamboni HK maintenance in parallel with it -- clock-spaced dual maintenance
is the root cause of the metadata-loss incident behind Workstream A.

Cache: stream_registry.aws_opt_compaction / aws_opt_retention / aws_opt_orphan
+ aws_opt_checked_at, refreshed at most every CONFLICT_CACHE_TTL_HOURS.
"""
from __future__ import annotations

from datetime import UTC, datetime

from config.settings import CONFLICT_CACHE_TTL_HOURS, STREAM_REGISTRY_TABLE, ZAMBONI_LOCAL_MODE
from engine.utils.athena_client import read_sql, run_query
from engine.utils.glue_client import get_table_optimizer
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)

# Glue optimizer type -> stream_registry cache column
_OPTIMIZER_COLUMNS = {
    "compaction":          "aws_opt_compaction",
    "retention":           "aws_opt_retention",
    "orphan_file_deletion": "aws_opt_orphan",
}


def check_table(fqn: str) -> dict:
    """
    Live Glue GetTableOptimizer check for all three optimizer types.
    Returns {"aws_opt_compaction": bool, "aws_opt_retention": bool, "aws_opt_orphan": bool}.

    Local mode always returns all-False -- there is no Glue optimizer to
    check, but callers should still stamp aws_opt_checked_at (see
    check_with_cache) so the cache behaves identically in both modes.
    """
    if ZAMBONI_LOCAL_MODE:
        return {col: False for col in _OPTIMIZER_COLUMNS.values()}

    _, database, table = parse_table_fqn(fqn)
    return {
        col: get_table_optimizer(database, table, opt_type)
        for opt_type, col in _OPTIMIZER_COLUMNS.items()
    }


def get_cached(fqn: str) -> dict | None:
    """
    Return the cached aws_opt_* values for a table if aws_opt_checked_at is
    within CONFLICT_CACHE_TTL_HOURS, else None (cache miss/stale/missing).
    """
    sql = f"""
        SELECT aws_opt_compaction, aws_opt_retention, aws_opt_orphan, aws_opt_checked_at
        FROM {STREAM_REGISTRY_TABLE}
        WHERE table_fqn = '{fqn}'
        LIMIT 1
    """
    try:
        df = read_sql(sql, workgroup="app")
    except Exception as e:
        if "column" in str(e).lower():
            return None  # aws_opt_* columns not migrated yet in this environment
        log.warning("conflict_detector.get_cached_failed", table_fqn=fqn, error=str(e))
        return None

    if df.empty:
        return None

    row        = df.iloc[0].to_dict()
    checked_at = _parse_ts(row.get("aws_opt_checked_at"))
    if checked_at is None:
        return None

    age_hours = (datetime.now(UTC) - checked_at).total_seconds() / 3600
    if age_hours > CONFLICT_CACHE_TTL_HOURS:
        return None  # stale

    return {
        "aws_opt_compaction": bool(row.get("aws_opt_compaction")),
        "aws_opt_retention":  bool(row.get("aws_opt_retention")),
        "aws_opt_orphan":     bool(row.get("aws_opt_orphan")),
        "aws_opt_checked_at": row.get("aws_opt_checked_at"),
    }


def check_with_cache(fqn: str) -> dict:
    """
    Return cached aws_opt_* values if fresh, else run a live check and write
    the result back to stream_registry. Always includes a "conflict" bool
    (True if any of the three optimizer types is enabled) and "source"
    ("cache" | "live").
    """
    cached = get_cached(fqn)
    if cached is not None:
        result = dict(cached)
        result["conflict"] = any(cached[c] for c in _OPTIMIZER_COLUMNS.values())
        result["source"]   = "cache"
        return result

    live = check_table(fqn)
    _write_back(fqn, live)
    result = dict(live)
    result["aws_opt_checked_at"] = datetime.now(UTC)
    result["conflict"]           = any(live.values())
    result["source"]             = "live"
    return result


def scan_fleet(fqns: list[str] | None = None, batch: int = 25) -> dict:
    """
    Force a live Glue optimizer check + cache write-back for every table in
    fqns (defaults to the full enabled fleet). Ignores the existing cache --
    always calls check_table() live. Returns {"scanned": N, "conflicts": N}.

    The Glue GetTableOptimizer call is inherently one-per-table (a live AWS
    API check, not SQL), but the cache write-back used to be too -- one
    Athena UPDATE per table, real fleet-scale row-by-row writes (found in
    the 2026-07-09 audit; this is the "Rescan conflicts" button on the
    Health Dashboard). Now batched into one UPDATE per `batch` chunk instead
    of one per table.
    """
    if fqns is None:
        from engine.core import registry
        fqns = [t["table_fqn"] for t in registry.get_enabled_tables()]

    scanned   = 0
    conflicts = 0
    for i in range(0, len(fqns), batch):
        chunk   = fqns[i:i + batch]
        results = {fqn: check_table(fqn) for fqn in chunk}
        _write_back_batch(results)
        scanned += len(chunk)
        conflicts += sum(1 for live in results.values() if any(live.values()))

    log.info("conflict_detector.scan_fleet_complete", scanned=scanned, conflicts=conflicts)
    return {"scanned": scanned, "conflicts": conflicts}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _write_back(fqn: str, live: dict, dry_run: bool = False) -> None:
    _write_back_batch({fqn: live}, dry_run=dry_run)


def _write_back_batch(results: dict[str, dict], dry_run: bool = False) -> None:
    """Single UPDATE for however many tables are in `results`, keyed by
    table_fqn via CASE -- stays Athena-direct on purpose (aws_opt_* are
    engine-owned columns deliberately excluded from the SQLite control
    plane, per config/control_plane_schema.py's docstring)."""
    if not results:
        return
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    def _case(col: str) -> str:
        whens = " ".join(
            f"WHEN '{_esc(fqn)}' THEN {str(bool(live.get(col))).lower()}"
            for fqn, live in results.items()
        )
        return f"CASE table_fqn {whens} END"

    in_list = ", ".join(f"'{_esc(fqn)}'" for fqn in results)
    sql = f"""
        UPDATE {STREAM_REGISTRY_TABLE}
        SET aws_opt_compaction = {_case("aws_opt_compaction")},
            aws_opt_retention  = {_case("aws_opt_retention")},
            aws_opt_orphan     = {_case("aws_opt_orphan")},
            aws_opt_checked_at = TIMESTAMP '{now}'
        WHERE table_fqn IN ({in_list})
    """
    try:
        run_query(sql, workgroup="app", dry_run=dry_run)
    except Exception as e:
        if "column" in str(e).lower():
            log.info("conflict_detector.write_back_column_missing", count=len(results))
            return
        log.warning("conflict_detector.write_back_batch_failed", count=len(results), error=str(e))


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _parse_ts(value) -> datetime | None:
    """Parse a DB timestamp value (str/Timestamp/datetime) into a tz-aware datetime."""
    if value is None:
        return None
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
