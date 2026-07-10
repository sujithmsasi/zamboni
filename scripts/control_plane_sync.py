"""
Zamboni -- Control Plane Sync Loop

Long-running process (own systemd unit, deploy/systemd/
zamboni-control-plane-sync.service) that periodically pushes the SQLite
control-plane DB's current state to real Athena, for reporting/recovery/
history. One-way, SQLite -> Athena -- stream_registry, hk_config,
domain_registry, nonprod_registry, and controlm_jobs are SQLite-primary
now (engine/core/control_plane.py), so Athena is a downstream replica for
these 5 tables, not the other direction. Deliberately a long-running loop
rather than a systemd timer: it re-reads the sync interval from
platform_settings on every cycle, so changing it via the Settings UI takes
effect on the next tick with no redeploy.

Unlike the earlier (now-retired) Athena-cache design, there is no outbox
and no read-refresh direction here -- nothing writes to real Athena for
these tables except this one script, so there is nothing to merge against.

Usage:
    python scripts/control_plane_sync.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.platform_settings import get_settings  # noqa: E402
from config.settings import (  # noqa: E402
    ATHENA_DATABASE,
    ATHENA_WORKGROUPS,
    CONTROLM_JOBS_TABLE,
    DOMAIN_REGISTRY_TABLE,
    HK_CONFIG_TABLE,
    NONPROD_REGISTRY_TABLE,
    STREAM_REGISTRY_TABLE,
    get_boto3_session,
)
from engine.core import control_plane  # noqa: E402
from engine.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)

_MIN_TICK_SECONDS = 15

# bare table name (as it exists in the SQLite control-plane DB) -> real
# Athena table name (the portion after "database.").
_SYNCED_TABLES: dict[str, str] = {
    "domain_registry":  DOMAIN_REGISTRY_TABLE.rsplit(".", 1)[-1],
    "stream_registry":  STREAM_REGISTRY_TABLE.rsplit(".", 1)[-1],
    "hk_config":        HK_CONFIG_TABLE.rsplit(".", 1)[-1],
    "nonprod_registry": NONPROD_REGISTRY_TABLE.rsplit(".", 1)[-1],
    "controlm_jobs":    CONTROLM_JOBS_TABLE.rsplit(".", 1)[-1],
}

# Columns that stay Athena-direct per config/control_plane_schema.py's
# docstring -- SQLite's copy of the table never has them at all. Without
# preserving them across this script's full-table overwrite, every sync
# cycle would either fail on the schema mismatch or (depending on
# awswrangler's overwrite/schema-evolution behavior) silently drop them
# from the real Athena table -- destroying state Gate 0's conflict cache,
# idempotency's execution dedupe, and the metadata-rollback tooling read
# directly from that same table. Only stream_registry has any.
_ENGINE_OWNED_COLUMNS: dict[str, list[str]] = {
    "stream_registry": [
        "aws_opt_compaction", "aws_opt_retention", "aws_opt_orphan",
        "aws_opt_checked_at", "last_execution_id", "metadata_location",
        "properties_synced",
    ],
}


class EngineOwnedColumnsUnavailable(RuntimeError):
    """
    Raised when the pre-overwrite fetch of engine-owned columns fails for
    a reason OTHER than the target Athena table not existing yet. Must
    propagate out of sync_table() (aborting that table's sync for this
    cycle, caught by _run_once()'s per-table try/except) rather than be
    swallowed into "proceed with nulled columns" -- a transient read
    failure (throttling, a workgroup/permission quirk) is not the same
    as a genuinely fresh table, and nulling Gate 0's conflict cache /
    idempotency / recovery state because of one is worse than just
    retrying next cycle.
    """


# Substrings Athena/Trino's StateChangeReason uses for a genuinely missing
# table -- the one failure mode where nulling these columns is correct,
# since a table with no Athena row yet has nothing to preserve. Deliberately
# a substring allowlist, not a blocklist of "safe to ignore" strings: any
# error that doesn't match one of these is treated as unexpected and aborts.
_TABLE_NOT_FOUND_MARKERS = ("TABLE_NOT_FOUND", "does not exist", "table not found")


def _fetch_engine_owned_columns(athena_table: str, columns: list[str]):
    """
    Current values of `columns` straight from the real Athena table, keyed
    by table_fqn -- read fresh every cycle (never cached here) so this
    script only carries them across its own overwrite, it never becomes a
    second source of truth for them.

    Returns an empty frame specifically when the table doesn't exist yet
    (a fresh environment's very first sync -- sync_table() nulls these
    columns in that case, which is correct: nothing to preserve). Any
    OTHER failure raises EngineOwnedColumnsUnavailable instead of
    returning empty, so sync_table() aborts rather than silently pushing
    an overwrite with these columns nulled out for a reason that has
    nothing to do with the table being fresh.
    """
    from engine.utils.athena_client import read_sql as athena_read_sql

    cols_sql = ", ".join(columns)
    try:
        return athena_read_sql(
            f"SELECT table_fqn, {cols_sql} FROM {athena_table}",
            workgroup=ATHENA_WORKGROUPS.get("app", "app"),
        )
    except Exception as e:
        msg = str(e)
        if any(marker.lower() in msg.lower() for marker in _TABLE_NOT_FOUND_MARKERS):
            log.info(
                "control_plane_sync.engine_owned_table_not_found_yet",
                table=athena_table, error=msg,
            )
            import pandas as pd
            return pd.DataFrame(columns=["table_fqn", *columns])
        log.error(
            "control_plane_sync.engine_owned_fetch_failed_aborting_sync",
            table=athena_table, error=msg,
        )
        raise EngineOwnedColumnsUnavailable(
            f"could not verify engine-owned columns for {athena_table}: {msg}"
        ) from e


def sync_table(bare_name: str, athena_table: str) -> None:
    """
    Push bare_name's current full contents from the SQLite control-plane DB
    to the real Athena table, as a full-table overwrite -- never a MERGE.
    Athena/Trino MERGE has no "delete rows missing from the source" clause,
    so it can't express a real SQLite-side DELETE (e.g.
    controlm_svc.delete_job()); an overwrite naturally reflects deletes,
    a MERGE would silently leave them behind in Athena forever. Table
    sizes here are config-metadata scale (tens to low thousands of rows),
    so a full read-and-replace per cycle is cheap. Deliberately does NOT
    skip an empty result -- a table that's been emptied out (last row
    deleted) must still overwrite Athena down to zero rows, not leave the
    last-synced data stranded there.

    Known caveat, not fixed here: if a table's very first sync ever
    happens while it holds zero rows, pandas has no data to infer real
    column types from (everything reads as generic object dtype), which
    could produce a wrong initial Athena schema on table creation. Not
    expected in practice -- domain_registry is seeded immediately and the
    others accumulate rows well before the first sync interval elapses --
    but worth knowing if a fresh environment's very first sync looks odd.
    """
    import awswrangler as wr

    df = control_plane.read_sql(f"SELECT * FROM {bare_name}")

    engine_owned = _ENGINE_OWNED_COLUMNS.get(bare_name)
    if engine_owned and not df.empty:
        preserved = _fetch_engine_owned_columns(athena_table, engine_owned)
        if not preserved.empty:
            df = df.merge(preserved, on="table_fqn", how="left")
        else:
            for col in engine_owned:
                df[col] = None

    wr.athena.to_iceberg(
        df=df,
        database=ATHENA_DATABASE,
        table=athena_table,
        mode="overwrite",
        workgroup=ATHENA_WORKGROUPS.get("app", "app"),
        boto3_session=get_boto3_session(),
    )
    log.info("control_plane_sync.table_synced", table=bare_name, rows=len(df))


def _run_once() -> None:
    for bare_name, athena_table in _SYNCED_TABLES.items():
        try:
            sync_table(bare_name, athena_table)
        except Exception as e:
            log.error("control_plane_sync.table_sync_failed", table=bare_name, error=str(e))


def _run_forever() -> None:
    from config.settings import ZAMBONI_LOCAL_MODE
    if ZAMBONI_LOCAL_MODE:
        # Nothing to push -- control_plane.read_sql() would read the local
        # demo fixture (ZAMBONI_LOCAL_DB), and there's no real Athena to
        # push it to in local mode. This script only makes sense for
        # aws_local/aws_ec2 deployments; matches scripts/aws_smoke_test.py's
        # local-mode-is-a-no-op convention.
        log.info("control_plane_sync.skipped_local_mode")
        return

    last_sync_at = 0.0

    while True:
        settings = get_settings()
        interval = max(int(settings.get("control_plane_sync_interval_seconds", 300)), _MIN_TICK_SECONDS)

        now = time.monotonic()
        if now - last_sync_at >= interval:
            _run_once()
            last_sync_at = now

        time.sleep(min(interval, _MIN_TICK_SECONDS * 2))


if __name__ == "__main__":
    log.info("control_plane_sync.starting")
    _run_forever()
