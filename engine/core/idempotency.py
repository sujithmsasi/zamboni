"""
Zamboni -- Idempotency Helper
v2 Section "13) Idempotency" -- strict idempotency key per operation.

Identity key (what IS hashed):
    table_fqn + operation + window_id

NOT hashed (accepted for backward compatibility only):
    run_id -- excluded from hash so two different engine invocations
    (e.g. EventBridge safety-net + Control-M trigger) that process the
    same table in the same window produce the SAME execution_id and
    correctly dedupe via check_already_executed().

The execution_id is stored in:
  - execution_log.run_id  (audit trail per row)
  - stream_registry.last_execution_id  (fast skip dedupe on next run)

Backward compatible -- silently no-ops if last_execution_id column
does not exist in older environments.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from engine.utils.logger import get_logger

log = get_logger(__name__)


def build_execution_id(
    run_id:    str,
    table_fqn: str,
    operation: str,
    window_id: str | None = None,
) -> str:
    """
    Build a deterministic execution_id from logical operation identity.
    Stable across run_ids — same table+operation+window always yields the
    same execution_id regardless of which engine invocation produced it.
    This enables cross-run dedupe: EventBridge safety-net + Control-M
    triggers for the same window produce the same ID.

    NOTE: run_id is accepted for backward compatibility but is NOT hashed.

    Args:
        run_id:    Engine run identifier — accepted but NOT included in hash
        table_fqn: Fully qualified table name
        operation: compaction | vacuum | orphan_cleanup | hk_run | property_sync
        window_id: Window identifier (e.g. "20260427"). Defaults to UTC date.

    Returns:
        Deterministic SHA1-based execution ID, e.g.
        "exec_a1b2c3d4e5f6_finance_staging_compaction_20260427"
    """
    win = window_id or datetime.now(UTC).strftime("%Y%m%d")
    # run_id intentionally excluded — hash must represent logical operation
    # identity (table+operation+window) so two concurrent engine invocations
    # for the same table produce the same execution_id and dedupe correctly.
    raw = f"{table_fqn}|{operation}|{win}"
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
            updated_at        = TIMESTAMP '{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}'
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
    return datetime.now(UTC).strftime("%Y%m%d")
