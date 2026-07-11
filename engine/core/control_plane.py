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


class ControlPlaneWriteError(RuntimeError):
    """
    Raised when a write against the control-plane DB fails, or (for
    update_row(), see expect_rowcount below) matches zero rows.

    2026-07-10 audit fix: the lenient local_db.run_query_local() catches
    every exception and returns None on failure (by design -- it's shared
    with athena_client.py's much wider blast radius, which isn't being
    changed here). Every api/services/*.py write path built on top of
    this module (tables_svc, domains_svc, policies_svc, gates_svc,
    lifecycle_svc, controlm_svc) either ignored run_query()'s return
    value entirely or returned a hardcoded True regardless of it -- a
    genuine SQLite write failure (disk full, corruption, a locked file)
    would report success to the caller and, in the API layer, a 200 to
    the user, while nothing was actually persisted.

    2026-07-11 audit fix: now backed by local_db.run_query_strict() (a
    dedicated strict variant, not a behavior change to run_query_local()
    itself -- that stays lenient for local/demo UI simulation, which
    still calls it directly, e.g. via athena_client.py in
    ZAMBONI_LOCAL_MODE) rather than checking "was the return value None."
    """


class ControlPlaneReadError(RuntimeError):
    """
    Raised when a read against the control-plane DB fails.

    2026-07-11 audit fix: local_db.read_sql_local() (kept as-is for
    local/demo UI simulation) returns an empty DataFrame on any read
    failure -- indistinguishable from a query that legitimately matched
    zero rows. A GET endpoint built on that would return 200 with an
    empty list for a disk-full/locked-file/malformed-query failure
    instead of a 500. Backed by local_db.read_sql_strict(), which only
    raises on a genuine exception, never on a real empty result.
    """


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
    """
    Drop-in replacement for athena_client.read_sql() against the
    control-plane DB. workgroup/database/timeout_s accepted for signature
    compatibility with callers that pass them positionally or by keyword;
    unused against SQLite.

    Raises ControlPlaneReadError if the underlying SQLite read fails --
    see that class's docstring.
    """
    try:
        return local_db.read_sql_strict(sql, db_path=_db_path())
    except local_db.LocalDbReadError as e:
        raise ControlPlaneReadError(str(e)) from e


def run_query(
    sql: str,
    workgroup: str = "app",
    database: str | None = None,
    dry_run: bool = False,
    timeout_s: int | None = None,
    expect_rowcount: bool = False,
) -> str | None:
    """
    Drop-in replacement for athena_client.run_query() against the
    control-plane DB.

    Raises ControlPlaneWriteError if the underlying SQLite write fails,
    or (when expect_rowcount=True) if it succeeds but matches zero rows
    -- see that class's docstring.
    """
    if dry_run:
        log.info("control_plane.dry_run", sql=sql[:150])
        return None
    try:
        return local_db.run_query_strict(sql, db_path=_db_path(), expect_rowcount=expect_rowcount)
    except local_db.LocalDbWriteError as e:
        raise ControlPlaneWriteError(str(e)) from e


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

    2026-07-11 audit fix: passes expect_rowcount=True -- this helper is
    always a single-row UPDATE keyed by a primary key the caller already
    resolved (e.g. via a prior GET), so matching zero rows has no
    legitimate meaning here (the row was deleted concurrently, or the key
    is stale/wrong) and must raise rather than silently "succeed" having
    changed nothing. Not applied to run_query() generically -- bulk
    updates and idempotent deletes elsewhere in this codebase (e.g.
    controlm_svc.delete_job(), tables_svc.bulk_controlm()) can
    legitimately match zero rows.
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
    run_query(sql, dry_run=dry_run, expect_rowcount=True)
