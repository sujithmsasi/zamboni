"""
Zamboni — Integrity Checker (Workstream A / Phase 1b, contracts.md §5-A)

Commit-verification for the orchestrator: capture a table's Iceberg
metadata pointer + snapshot state before and after an operation, then
assert the change went the direction that operation is supposed to move it.
This is the direct fix for the metadata-loss incident — no step is
considered done until its commit is verified.

Athena engine v3 reality (see .claude/context_hints.md, engine/operations/
vacuum.py): OPTIMIZE and VACUUM both commit a new snapshot and therefore
ADVANCE metadata_location. There is no separate orphan-only call that
would leave the pointer unchanged — contracts.md §5-A explicitly voids the
older §5 "pointer unchanged for orphan" rule for that reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from config.settings import ZAMBONI_LOCAL_MODE
from engine.utils.athena_client import read_sql
from engine.utils.glue_client import get_table
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)

_LOCAL_STUB_LOCATION = "local-mode-stub"


@dataclass
class TableState:
    """Point-in-time snapshot of a table's Iceberg metadata identity."""
    table_fqn:           str
    metadata_location:   str | None
    current_snapshot_id: int | None
    snapshot_count:      int | None
    current_snapshot_ts: datetime | None
    captured_at:         datetime
    # False when the Glue metadata_location lookup itself failed or the
    # table wasn't found -- as opposed to genuinely fetching and finding no
    # value. 2026-07-10 audit fix: verify_advanced() used to compare
    # metadata_location None-vs-a-real-value as "the pointer changed" any
    # time a capture failed on exactly one side, reporting a false VERIFIED
    # result for a run where the before/after state was never actually
    # confirmed. This flag lets verify_advanced() fail closed on "couldn't
    # tell" instead of silently treating it as a change.
    metadata_capture_ok: bool = True
    # 2026-07-11 audit fix: tracked separately from metadata_capture_ok --
    # Glue (metadata_location) and Athena ("$snapshots") are two
    # independent calls that can fail independently. A table where the
    # Glue lookup succeeds but the Athena snapshot query throws used to
    # leave snapshot_count=None on both sides, which the snapshot-count/
    # age checks below silently skipped via `is not None` guards --
    # meaning a real snapshot-count regression could go unverified any
    # time the Athena side merely failed to capture, even though the
    # metadata-pointer check itself looked fine. Now checked explicitly so
    # a snapshot-capture failure fails the whole verification, not just
    # the parts of it that happen to depend on a non-None value.
    snapshot_capture_ok: bool = True


@dataclass
class IntegrityResult:
    status:    str   # VERIFIED | FAILED | SKIPPED
    operation: str
    detail:    str


def capture_state(fqn: str) -> TableState:
    """
    Capture metadata_location (Glue), snapshot_count + current_snapshot_id/ts
    (Athena "$snapshots") for a table.

    Local mode: there is no live Glue/Iceberg catalog to introspect and no
    SQLite equivalent for "$snapshots" (engine/utils/local_db.py has no
    metadata-table translation). Returns a documented stub state so
    orchestration is end-to-end testable without asserting real pointer
    semantics — verify_advanced() treats the stub as SKIPPED, matching how
    dry runs are handled.
    """
    now = datetime.now(UTC)

    if ZAMBONI_LOCAL_MODE:
        return TableState(
            table_fqn=fqn, metadata_location=_LOCAL_STUB_LOCATION,
            current_snapshot_id=None, snapshot_count=None,
            current_snapshot_ts=None, captured_at=now,
        )

    _, database, table = parse_table_fqn(fqn)

    metadata_location = None
    metadata_capture_ok = True
    try:
        glue_table = get_table(database, table)
        if glue_table:
            metadata_location = glue_table.get("Parameters", {}).get("metadata_location")
        else:
            metadata_capture_ok = False
    except Exception as e:
        log.warning("integrity_checker.capture_state_glue_failed", table_fqn=fqn, error=str(e))
        metadata_capture_ok = False

    current_snapshot_id = None
    current_snapshot_ts  = None
    snapshot_count       = None
    snapshot_capture_ok  = True
    try:
        latest_sql = f"""
            SELECT snapshot_id, committed_at
            FROM "{database}"."{table}$snapshots"
            ORDER BY committed_at DESC
            LIMIT 1
        """
        df = read_sql(latest_sql, workgroup="app", database=database)
        if not df.empty:
            current_snapshot_id = int(df.iloc[0]["snapshot_id"])
            current_snapshot_ts = _parse_ts(df.iloc[0]["committed_at"])

        count_sql = f"""
            SELECT COUNT(*) AS cnt
            FROM "{database}"."{table}$snapshots"
        """
        count_df = read_sql(count_sql, workgroup="app", database=database)
        if not count_df.empty:
            snapshot_count = int(count_df.iloc[0]["cnt"])
        else:
            snapshot_capture_ok = False
    except Exception as e:
        log.warning("integrity_checker.capture_state_snapshots_failed", table_fqn=fqn, error=str(e))
        snapshot_capture_ok = False

    return TableState(
        table_fqn=fqn, metadata_location=metadata_location,
        current_snapshot_id=current_snapshot_id, snapshot_count=snapshot_count,
        current_snapshot_ts=current_snapshot_ts, captured_at=now,
        metadata_capture_ok=metadata_capture_ok,
        snapshot_capture_ok=snapshot_capture_ok,
    )


def verify_advanced(
    before: TableState,
    after:  TableState,
    operation: str,
    min_snapshot_age_hours: float | None = None,
) -> IntegrityResult:
    """
    Assert commit-verified progress between before/after TableState.

    operation="optimize": metadata pointer must CHANGE; snapshot_count must
      not decrease (compaction commits a new snapshot on top).
    operation="vacuum": metadata pointer must CHANGE (Athena engine v3's
      single combined VACUUM always commits — contracts.md §5-A supersedes
      §5's "pointer unchanged for orphan" rule); snapshot_count must not
      increase; if min_snapshot_age_hours is given, the resulting current
      snapshot must be at least that old (the floor the property clamp set).
    """
    if before.metadata_location == _LOCAL_STUB_LOCATION or after.metadata_location == _LOCAL_STUB_LOCATION:
        return IntegrityResult("SKIPPED", operation, "local mode — no live metadata to verify")

    # 2026-07-10 audit fix: a failed metadata capture on either side must
    # not be allowed to reach the None-vs-value comparison below -- None !=
    # a real string is True, which used to report a false "pointer
    # advanced" (VERIFIED) result for a run whose before/after state was
    # never actually confirmed. Fail closed instead: this IS the incident
    # this module exists to catch, so it must not be silently waved through.
    if not before.metadata_capture_ok or not after.metadata_capture_ok:
        return IntegrityResult(
            "FAILED", operation,
            "metadata capture failed on one or both sides -- cannot verify pointer advancement",
        )

    # 2026-07-11 audit fix: same reasoning, for the Athena snapshot side --
    # a failed snapshot capture used to just leave snapshot_count/age as
    # None, which the checks below silently skip via `is not None` guards.
    # That let a real snapshot-count regression go unverified any time the
    # Athena side failed independently of Glue. Fail the whole
    # verification instead of only the parts that happen to depend on it.
    if not before.snapshot_capture_ok or not after.snapshot_capture_ok:
        return IntegrityResult(
            "FAILED", operation,
            "snapshot capture failed on one or both sides -- cannot verify snapshot-count/age safety",
        )

    advanced = before.metadata_location != after.metadata_location

    if operation == "optimize":
        if not advanced:
            return IntegrityResult("FAILED", operation, "metadata_location did not change after OPTIMIZE")
        if (
            before.snapshot_count is not None and after.snapshot_count is not None
            and after.snapshot_count < before.snapshot_count
        ):
            return IntegrityResult(
                "FAILED", operation,
                f"snapshot_count decreased after OPTIMIZE ({before.snapshot_count} -> {after.snapshot_count})",
            )
        return IntegrityResult("VERIFIED", operation, "metadata pointer advanced, snapshot count non-decreasing")

    if operation == "vacuum":
        if not advanced:
            return IntegrityResult(
                "FAILED", operation,
                "metadata_location did not change after VACUUM (combined expire+orphan is expected to always commit)",
            )
        if (
            before.snapshot_count is not None and after.snapshot_count is not None
            and after.snapshot_count > before.snapshot_count
        ):
            return IntegrityResult(
                "FAILED", operation,
                f"snapshot_count increased after VACUUM ({before.snapshot_count} -> {after.snapshot_count})",
            )
        if min_snapshot_age_hours is not None and after.current_snapshot_ts is not None:
            age_hours = (datetime.now(UTC) - after.current_snapshot_ts).total_seconds() / 3600
            if age_hours < min_snapshot_age_hours:
                return IntegrityResult(
                    "FAILED", operation,
                    f"current snapshot age {age_hours:.1f}h < floor {min_snapshot_age_hours:.1f}h",
                )
        return IntegrityResult(
            "VERIFIED", operation,
            "metadata pointer advanced, snapshot count non-increasing, age floor satisfied",
        )

    raise ValueError(f"verify_advanced: unknown operation {operation!r}")


def _parse_ts(value) -> datetime | None:
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
