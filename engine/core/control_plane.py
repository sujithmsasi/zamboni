"""
Zamboni -- Control Plane (SQLite-primary for config/control tables)

stream_registry, hk_config, domain_registry, nonprod_registry, and
controlm_jobs are SQLite-primary in production, not just local/demo mode:
the API/UI write here first (fast, synchronous, no queue) and the engine
reads the SAME file directly -- safe specifically because SQLite is now
the first point of write, so engine reads are fresh by construction. There
is no staleness window to protect against, unlike a periodically-refreshed
cache, so there is no outbox and no safety-critical bypass list here --
every write is just a normal SQL statement against SQLite.

execution_log, audit_log, and vacuum_audit stay 100% Athena-primary
(engine.utils.athena_client, untouched) -- real engine output from
data-plane work, not config the UI writes. A handful of engine-owned
columns on stream_registry (aws_opt_*, last_execution_id,
metadata_location, properties_synced) also stay Athena-direct via their
own dedicated functions (conflict_detector.py, idempotency.py,
recovery.py, property_sync.py) and are excluded from this module's schema
entirely -- see config/control_plane_schema.py's docstring.

Signature-compatible with engine.utils.athena_client.read_sql()/run_query()
so callers (engine/core/registry.py, config.py,
engine/engines/lifecycle_engine.py, and api/services/*.py) migrate with an
import swap, not a rewrite -- they already emit Athena-dialect SQL
(TIMESTAMP '...', true/false, etc.) that engine.utils.local_db._translate()
already handles.

Resolves to ZAMBONI_LOCAL_DB when ZAMBONI_LOCAL_MODE=true -- zero behavior
change for local/demo, still the same file scripts/seed_local_db.py seeds
-- and ZAMBONI_CONTROL_PLANE_DB otherwise, used by both aws_local and
aws_ec2. A periodic background sync (scripts/control_plane_sync.py) pushes
the control-plane DB's current state to real Athena for reporting/
recovery/history -- one-way, SQLite to Athena, full-table overwrite each
cycle (see that script for why, not MERGE/outbox).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from engine.utils import local_db
from engine.utils.logger import get_logger

log = get_logger(__name__)


def _db_path() -> str:
    from config.settings import (
        ZAMBONI_CONTROL_PLANE_DB,
        ZAMBONI_LOCAL_DB,
        ZAMBONI_LOCAL_MODE,
    )
    return ZAMBONI_LOCAL_DB if ZAMBONI_LOCAL_MODE else ZAMBONI_CONTROL_PLANE_DB


def read_sql(
    sql: str,
    workgroup: str = "app",
    database: str | None = None,
    timeout_s: int | None = None,
) -> pd.DataFrame:
    """Drop-in replacement for athena_client.read_sql() against the
    control-plane DB. workgroup/database/timeout_s accepted for signature
    compatibility with callers that pass them positionally or by keyword;
    unused against SQLite."""
    return local_db.read_sql_local(sql, db_path=_db_path())


def run_query(
    sql: str,
    workgroup: str = "app",
    database: str | None = None,
    dry_run: bool = False,
    timeout_s: int | None = None,
) -> str | None:
    """Drop-in replacement for athena_client.run_query() against the
    control-plane DB."""
    if dry_run:
        log.info("control_plane.dry_run", sql=sql[:150])
        return None
    return local_db.run_query_local(sql, db_path=_db_path())


def _esc(value) -> str:
    return str(value).replace("'", "''")


def _sql_literal(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if value is None:
        return "NULL"
    return f"'{_esc(value)}'"


def update_row(
    table: str,
    key_col: str,
    key_val: str,
    column_values: dict,
    *,
    dry_run: bool,
    touch_updated_at: bool = True,
) -> None:
    """
    Single-row UPDATE against the control-plane DB. Drop-in replacement for
    the old (now-retired) athena_cache.write_columns(), minus the
    safety-critical split -- every column here is a normal write, since
    SQLite being the primary write target is exactly what makes freshness
    a non-issue for the engine's next read. No-ops if column_values is
    empty.
    """
    if not column_values:
        return
    if touch_updated_at:
        column_values = {
            **column_values,
            "updated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        }
    sets = ", ".join(f"{col} = {_sql_literal(val)}" for col, val in column_values.items())
    sql = f"UPDATE {table} SET {sets} WHERE {key_col} = '{_esc(key_val)}'"
    run_query(sql, dry_run=dry_run)
