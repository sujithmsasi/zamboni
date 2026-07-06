"""
Zamboni — Metadata Rollback Tooling (Workstream A / Phase 1c)

Turns "metadata lost" from a P1 into a 5-minute fix. Every orchestrated
OPTIMIZE/SAFE-VACUUM step (Phase 1b) logs metadata_location_before/after to
execution_log -- this module lists those prior pointer values as rollback
candidates, validates a target metadata.json is still safely restorable
(not orphan-deleted), then flips Glue's Parameters['metadata_location']
back to it via engine.utils.glue_client.update_metadata_location().

Safety: rollback_metadata() REFUSES (no Glue call, no log write) if
validate_rollback_target() reports the target missing. The refusal message
cites ORPHAN_MIN_AGE_HOURS_FLOOR (72h, contracts.md D2) exactly -- see
.claude/decisions.md's "72h floor = guaranteed rollback window" note. That
floor is what makes the refusal message true: Athena's combined VACUUM
(contracts.md §5-A) never deletes files referenced by a snapshot younger
than the floor, so a target inside the window is expected to still exist.

Local mode: there is no live S3/Glue catalog to validate against (same gap
integrity_checker.capture_state() documents for metadata_location) -- see
_validate_local()/_rollback_local() below for the documented simulation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from config.settings import (
    EXECUTION_LOG_TABLE,
    ORPHAN_MIN_AGE_HOURS_FLOOR,
    STREAM_REGISTRY_TABLE,
    ZAMBONI_LOCAL_MODE,
)
from engine.core import execution_log, notifier, registry
from engine.core.audit import AuditAction, AuditEvent, audit
from engine.core.execution_log import LogEntry
from engine.utils import glue_client, s3_client
from engine.utils.athena_client import read_sql, run_query
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn

log = get_logger(__name__)

ORPHAN_REFUSAL_REASON = (
    "target metadata was likely orphan-deleted; rollback window is "
    f"ORPHAN_MIN_AGE_HOURS_FLOOR ({ORPHAN_MIN_AGE_HOURS_FLOOR}h)"
)


# ── Candidates ─────────────────────────────────────────────────────────────────

def get_rollback_candidates(fqn: str, limit: int = 10) -> list[dict]:
    """
    execution_log rows for fqn with metadata_location_before set, newest
    first. Each row's metadata_location_before is the pointer value Phase
    1b's capture_state() captured immediately before that step's OPTIMIZE
    or SAFE-VACUUM commit -- rolling back to it undoes that step.
    """
    sql = f"""
        SELECT run_id, operation, status, integrity_status,
               metadata_location_before, metadata_location_after,
               snapshot_id_before, snapshot_id_after,
               started_at, completed_at
        FROM {EXECUTION_LOG_TABLE}
        WHERE table_fqn = '{fqn}'
          AND metadata_location_before IS NOT NULL
        ORDER BY started_at DESC
        LIMIT {int(limit)}
    """
    try:
        df = read_sql(sql, workgroup="app")
    except Exception as e:
        if "column" in str(e).lower():
            log.info("recovery.candidates_column_missing", table_fqn=fqn)
            return []
        log.warning("recovery.get_rollback_candidates_failed", table_fqn=fqn, error=str(e))
        return []

    if df.empty:
        return []
    return df.to_dict(orient="records")


# ── Validation ─────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    valid:             bool
    reason:            str
    metadata_location: str
    snapshot_id:       int | None = None
    snapshot_count:    int | None = None
    sampled_files:     int = 0
    missing_files:     int | None = None


def validate_rollback_target(fqn: str, metadata_location: str) -> ValidationResult:
    """
    a) S3 head_object: confirm the metadata.json itself still exists (has
       not been orphan-deleted by a prior VACUUM).
    b) Parse it: confirm it is valid Iceberg metadata (current-snapshot-id
       + snapshots present); report snapshot_id + count.
    c) Best-effort, capped spot-check of a sample of referenced data files.
    """
    if ZAMBONI_LOCAL_MODE:
        return _validate_local(fqn, metadata_location)
    return _validate_live(fqn, metadata_location)


def _validate_live(fqn: str, metadata_location: str) -> ValidationResult:
    bucket, key = s3_client.parse_s3_uri(metadata_location)

    if s3_client.head_object(bucket, key) is None:
        log.warning("recovery.validate.target_missing", table_fqn=fqn, metadata_location=metadata_location)
        return ValidationResult(False, ORPHAN_REFUSAL_REASON, metadata_location)

    try:
        metadata = json.loads(s3_client.get_object_bytes(bucket, key))
    except Exception as e:
        reason = f"target metadata.json exists but failed to parse: {e}"
        log.error("recovery.validate.parse_failed", table_fqn=fqn, error=str(e))
        return ValidationResult(False, reason, metadata_location)

    if "current-snapshot-id" not in metadata or "snapshots" not in metadata:
        reason = "target file exists but is not valid Iceberg metadata (missing current-snapshot-id/snapshots)"
        return ValidationResult(False, reason, metadata_location)

    snapshot_id    = metadata.get("current-snapshot-id")
    snapshot_count = len(metadata.get("snapshots") or [])
    spot           = _spot_check_data_files(metadata)

    if spot["checked"]:
        detail = f"spot-check: {spot['missing']}/{spot['sampled']} sampled data file(s) missing"
    else:
        detail = f"data-file spot-check skipped ({spot['reason']})"

    reason = f"valid Iceberg metadata: snapshot_id={snapshot_id}, {snapshot_count} snapshot(s) on file; {detail}"

    return ValidationResult(
        True, reason, metadata_location,
        snapshot_id=snapshot_id, snapshot_count=snapshot_count,
        sampled_files=spot.get("sampled", 0), missing_files=spot.get("missing"),
    )


def _spot_check_data_files(metadata: dict, sample_manifests: int = 3, sample_files_per_manifest: int = 3) -> dict:
    """
    Best-effort, capped: read the current snapshot's manifest-list, sample
    a few manifests, sample a few data-file paths from each, head_object
    each one. Requires fastavro (optional dependency, lazily imported) --
    both Iceberg manifest-lists and manifest files are Avro. Never raises;
    any failure degrades to "skipped" rather than blocking validation.
    """
    try:
        import fastavro
    except ImportError:
        return {"checked": False, "reason": "fastavro not installed", "sampled": 0, "missing": None}

    import io

    snapshots  = metadata.get("snapshots") or []
    current_id = metadata.get("current-snapshot-id")
    snap = next((s for s in snapshots if s.get("snapshot-id") == current_id), snapshots[-1] if snapshots else None)
    if not snap or not snap.get("manifest-list"):
        return {"checked": False, "reason": "no manifest-list on current snapshot", "sampled": 0, "missing": None}

    try:
        bucket, key = s3_client.parse_s3_uri(snap["manifest-list"])
        body = s3_client.get_object_bytes(bucket, key)
        manifest_entries = list(fastavro.reader(io.BytesIO(body)))
        manifest_paths = [m["manifest_path"] for m in manifest_entries[:sample_manifests] if m.get("manifest_path")]

        sampled = 0
        missing = 0
        for mpath in manifest_paths:
            mbucket, mkey = s3_client.parse_s3_uri(mpath)
            mbody = s3_client.get_object_bytes(mbucket, mkey)
            for rec in list(fastavro.reader(io.BytesIO(mbody)))[:sample_files_per_manifest]:
                file_path = (rec.get("data_file") or {}).get("file_path")
                if not file_path:
                    continue
                sampled += 1
                fbucket, fkey = s3_client.parse_s3_uri(file_path)
                if s3_client.head_object(fbucket, fkey) is None:
                    missing += 1

        return {"checked": True, "sampled": sampled, "missing": missing}
    except Exception as e:
        log.warning("recovery.spot_check_failed", error=str(e))
        return {"checked": False, "reason": f"spot-check error: {e}", "sampled": 0, "missing": None}


def _validate_local(fqn: str, metadata_location: str) -> ValidationResult:
    """
    Local mode has no live S3/Glue catalog to check against (same
    documented gap as integrity_checker.capture_state()). Simulated: a
    metadata_location containing "_orphaned" simulates an orphan-deleted
    target (so the refusal path is exercisable without real AWS);
    everything else simulates a still-present, valid metadata.json with a
    deterministic fake snapshot_id.
    """
    if "_orphaned" in metadata_location:
        return ValidationResult(False, ORPHAN_REFUSAL_REASON, metadata_location)

    fake_snapshot_id = abs(hash((fqn, metadata_location))) % 10_000_000
    reason = (
        "local mode — simulated validation (no live S3/Glue catalog): "
        f"treated as present with fake snapshot_id={fake_snapshot_id}"
    )
    return ValidationResult(
        True, reason, metadata_location,
        snapshot_id=fake_snapshot_id, snapshot_count=3, sampled_files=0, missing_files=None,
    )


# ── Rollback ─────────────────────────────────────────────────────────────────

def rollback_metadata(
    fqn: str, metadata_location: str, actor: str, reason: str, dry_run: bool = True,
) -> bool:
    """
    Point fqn's catalog entry back at metadata_location. Refuses (no Glue
    call, no execution_log/audit write) if validate_rollback_target()
    reports the target invalid -- callers should surface
    ValidationResult.reason to the operator before calling this.

    On success, records the CURRENT location as metadata_location_before
    and the target as metadata_location_after in a new execution_log row
    (operation='ROLLBACK'), writes an audit_log entry, and sends an SNS
    notification.
    """
    validation = validate_rollback_target(fqn, metadata_location)
    if not validation.valid:
        log.error("recovery.rollback.refused", table_fqn=fqn, reason=validation.reason)
        return False

    current = _current_metadata_location(fqn)

    if ZAMBONI_LOCAL_MODE:
        ok = _rollback_local(fqn, metadata_location, dry_run)
    else:
        _, database, table = parse_table_fqn(fqn)
        ok = glue_client.update_metadata_location(database, table, metadata_location, dry_run=dry_run)

    if not ok:
        log.error("recovery.rollback.write_failed", table_fqn=fqn)
        return False

    if dry_run:
        integrity_status = "SKIPPED"
        status           = "DRY_RUN"
    else:
        after = _current_metadata_location(fqn)
        integrity_status = "VERIFIED" if after == metadata_location else "FAILED"
        status           = "SUCCESS" if integrity_status == "VERIFIED" else "FAILURE"

    run_id = execution_log.new_run_id()
    execution_log.write(
        LogEntry(
            run_id=run_id, engine="hk", operation="ROLLBACK", table_fqn=fqn,
            domain="", layer="", tier="", environment="prod",
            status=status, dry_run=dry_run,
            metadata_location_before=current, metadata_location_after=metadata_location,
            snapshot_id_after=validation.snapshot_id, integrity_status=integrity_status,
        ),
        dry_run=dry_run,
    )

    audit(AuditEvent(
        actor=actor, action_type=AuditAction.METADATA_ROLLBACK,
        page_source="recover_metadata_cli", target_type="table", target_id=fqn,
        dry_run=dry_run, status=status, reason=reason,
        before_value=current or "", after_value=metadata_location,
    ))

    notifier.send_alert(
        subject=f"Metadata rollback{' (dry run)' if dry_run else ''} — {fqn}",
        message=f"Actor: {actor}\nReason: {reason}\nRolled back from {current} to {metadata_location}",
        table_fqn=fqn, dry_run=dry_run,
    )

    return integrity_status != "FAILED"


def _current_metadata_location(fqn: str) -> str | None:
    if ZAMBONI_LOCAL_MODE:
        row = registry.get_table(fqn)
        return (row or {}).get("metadata_location")

    _, database, table = parse_table_fqn(fqn)
    t = glue_client.get_table(database, table)
    return (t or {}).get("Parameters", {}).get("metadata_location") if t else None


def _rollback_local(fqn: str, metadata_location: str, dry_run: bool) -> bool:
    """
    Local-mode-only simulation of the Glue update_table call: writes to
    stream_registry.metadata_location (a local-simulation column, not part
    of contracts.md's locked Athena DDL -- in real mode, the current
    pointer always comes from live Glue Parameters, never from
    stream_registry, so there is no schema conflict).
    """
    escaped = metadata_location.replace("'", "''")
    sql = f"""
        UPDATE {STREAM_REGISTRY_TABLE}
        SET metadata_location = '{escaped}'
        WHERE table_fqn = '{fqn}'
    """
    try:
        run_query(sql, workgroup="app", dry_run=dry_run)
        return True
    except Exception as e:
        if "column" in str(e).lower():
            log.info("recovery.rollback_local.column_missing", table_fqn=fqn)
            return True
        log.error("recovery.rollback_local.failed", table_fqn=fqn, error=str(e))
        return False
