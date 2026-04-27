"""
Zamboni — Idempotency Helper
v2 Section "13) Idempotency" — strict idempotency key per operation:
    run_id + table_fqn + operation + window_id

Used to prevent duplicate dispatch on retry or overlapping runs.
The execution_id is stored in:
  - execution_log.run_id (for audit trail per row)
  - stream_registry.last_execution_id (for fast skip dedupe lookup)

Backward compatible — silently no-ops if last_execution_id column doesn't
exist in older environments.
"""
import hashlib
from datetime import datetime, timezone
from typing import Optional

from engine.utils.logger import get_logger

log = get_logger(__name__)


def build_execution_id(
    run_id:    str,
    table_fqn: str,
    operation: str,
    window_id: Optional[str] = None,
) -> str:
    """
    Build a deterministic execution_id from the operation context.
    Two runs of the same logical operation in the same window will have
    identical execution_ids — this is what enables dedupe.

    Args:
        run_id:    Engine run identifier (e.g. "hk-20260427-153000")
        table_fqn: Fully qualified table name
        operation: compaction | vacuum | orphan_cleanup | hk_run | property_sync
        window_id: Optional window identifier (e.g. "2026-04-27-postbatch")
                   If None, falls back to today's date string.

    Returns:
        deterministic SHA1-based execution ID, e.g.
        "exec_a1b2c3d4e5f6_finance_staging_compaction_20260427"
    """
    win = window_id or datetime.now(timezone.utc).strftime("%Y%m%d")
    raw = f"{run_id}|{table_fqn}|{operation}|{win}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    table_short = table_fqn.split(".")[-1][:30]
    return f"exec_{digest}_{table_short}_{operation}_{win}"


def check_already_executed(
    execution_id: str,
    table_fqn:    str,
) -> bool:
    """
    Return True if this execution_id has already been recorded as SUCCESS.
    Failed/incomplete prior runs DO NOT count — preserves retry behaviour
    (v2 design B.4).

    Backward compatible — silently returns False if last_execution_id column
    does not exist in older environments.
    """
    from config.settings import STREAM_REGISTRY_TABLE
    try:
        from engine.utils.athena_client import read_sql
        sql = f"""
            SELECT last_execution_id
            FROM {STREAM_REGISTRY_TABLE}
            WHERE table_fqn = '{table_fqn}'
              AND last_execution_id = '{execution_id}'
            LIMIT 1
        """
        df = read_sql(sql, workgroup="app")
        return not df.empty
    except Exception as e:
        msg = str(e).lower()
        if "column" in msg and "last_execution_id" in msg:
            return False  # column doesn't exist — assume not yet executed
        log.warning(
            "idempotency.check_failed",
            execution_id=execution_id, error=str(e),
        )
        return False


def mark_executed(
    execution_id: str,
    table_fqn:    str,
    dry_run:      bool = False,
) -> bool:
    """
    Update stream_registry.last_execution_id on successful completion.
    Backward compatible — silently no-ops if column doesn't exist.
    """
    from config.settings import STREAM_REGISTRY_TABLE
    sql = f"""
        UPDATE {STREAM_REGISTRY_TABLE}
        SET last_execution_id = '{execution_id}',
            updated_at        = TIMESTAMP '{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}'
        WHERE table_fqn = '{table_fqn}'
    """
    if dry_run:
        log.info("idempotency.mark_executed.dry_run",
                 table_fqn=table_fqn, execution_id=execution_id)
        return True
    try:
        from engine.utils.athena_client import run_query
        run_query(sql, workgroup="app")
        return True
    except Exception as e:
        msg = str(e).lower()
        if "column" in msg and "last_execution_id" in msg:
            return False
        log.warning(
            "idempotency.mark_executed.failed",
            table_fqn=table_fqn, error=str(e),
        )
        return False


def get_window_id() -> str:
    """
    Get the current window_id — used as part of the idempotency key.
    For now, returns the date string. Could be extended to consider
    upstream batch timestamps, EventBridge invocation ID, etc.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%d")
