# Runbook: Metadata Recovery

**Owner:** Zamboni engine team · **Applies to:** Workstream A (Engine Hardening) · **Last updated:** 2026-07-06

## Symptoms

- Athena query against a table fails with `metadata not found` / `Failed to open input stream for file ...metadata.json`.
- A table that previously had N snapshots suddenly shows far fewer (or zero)
  in `"{table}$snapshots"`, and time-travel queries to a recently-known-good
  snapshot fail.
- A HK run in `execution_log` shows `integrity_status = 'FAILED'` after an
  `optimize` or `vacuum` step — this is the orchestrator (Phase 1b) catching
  the problem at commit time, before an operator would otherwise notice.
- Circuit breaker tripped for a table with no other explanation.

This is the exact failure mode Workstream A exists to prevent (root cause:
clock-spaced dual maintenance racing against AWS Glue's own table optimizer —
see `.claude/contracts.md` §0). Gate 0 blocks the race going forward; this
runbook is for a table that was already affected, or where the target
metadata is suspect for any other reason.

## Triage queries

Find recent activity and integrity status for the table:

```sql
SELECT run_id, operation, status, integrity_status,
       metadata_location_before, metadata_location_after,
       snapshot_id_before, snapshot_id_after, started_at, completed_at
FROM glue_catalog.zamboni_catalog.execution_log
WHERE table_fqn = 'glue_catalog.<db>.<table>'
ORDER BY started_at DESC
LIMIT 20;
```

Any row with `integrity_status = 'FAILED'` is a strong candidate: its
`metadata_location_before` is the pointer value immediately before the step
that failed verification.

Check whether an AWS Glue table optimizer was also active (the root-cause
conflict):

```sql
SELECT aws_opt_compaction, aws_opt_retention, aws_opt_orphan, aws_opt_checked_at
FROM glue_catalog.zamboni_catalog.stream_registry
WHERE table_fqn = 'glue_catalog.<db>.<table>';
```

## Fix: the recovery CLI

`scripts/recover_metadata.py` wraps `engine/core/recovery.py` — list
candidates, validate a target, and roll the Glue catalog pointer back to it.

```bash
# 1) List rollback candidates for the table (reads execution_log)
python scripts/recover_metadata.py --fqn glue_catalog.<db>.<table> --dry-run

# 2) Or roll back to a specific metadata.json directly
python scripts/recover_metadata.py --fqn glue_catalog.<db>.<table> \
    --to s3://<bucket>/<db>/<table>/metadata/00041-....metadata.json --dry-run

# 3) Once the dry run looks right, repeat without --dry-run.
#    You will be asked to type the table's full FQN to confirm.
python scripts/recover_metadata.py --fqn glue_catalog.<db>.<table> \
    --to s3://<bucket>/<db>/<table>/metadata/00041-....metadata.json
```

What it does, step by step:

1. **List candidates** — `get_rollback_candidates()` reads every
   `execution_log` row for the table with `metadata_location_before` set,
   newest first. Each is a point the catalog could be rolled back to (the
   pointer value captured immediately before that step's commit).
2. **Validate** — `validate_rollback_target()`:
   - Confirms the target `metadata.json` still exists in S3 (`head_object`).
   - Parses it and confirms it is valid Iceberg metadata
     (`current-snapshot-id` + `snapshots` present); reports the snapshot id
     and count.
   - Best-effort, capped spot-check of a few referenced data files (requires
     the optional `fastavro` dependency; skips gracefully if it isn't
     installed — this never blocks a rollback).
3. **Confirm** — for a real (non-dry-run) rollback, you must type the exact
   table FQN back at the prompt.
4. **Roll back** — `rollback_metadata()` calls Glue `update_table`, setting
   `Parameters['metadata_location']` to the target while preserving every
   other table parameter and the storage descriptor untouched. It then:
   - Writes a new `execution_log` row (`operation = 'ROLLBACK'`) recording
     the location it moved *from* as `metadata_location_before` and the
     target as `metadata_location_after`.
   - Writes an `audit_log` entry with the actor and reason.
   - Sends an SNS alert.

## The 72-hour window

**Rollback refuses if the target `metadata.json` no longer exists in S3.**
The refusal message is exact and not negotiable:

> target metadata was likely orphan-deleted; rollback window is
> ORPHAN_MIN_AGE_HOURS_FLOOR (72h)

This is not an arbitrary number. `ORPHAN_MIN_AGE_HOURS_FLOOR = 72`
(`config/settings.py`, contracts.md D2) is the floor
`engine/core/maintenance_ops.py::_clamp_vacuum_properties()` enforces on
`vacuum_max_snapshot_age_seconds` before every SAFE-VACUUM run — Athena's
combined `VACUUM` (contracts.md §5-A) never expires a snapshot, and
therefore never deletes the files it alone references, until it is at least
that old. In other words: **the 72-hour floor is the guaranteed rollback
window.** A target inside that window should still be physically present in
S3; a refusal past it means the files backing that metadata are gone and
there is nothing left to roll back to — the fix at that point is restoring
from an external backup, not this tool.

## Escalation path

If the tool refuses and there is no valid candidate inside the 72h window:

1. Check whether the table has any external backup/archive copy (Archival
   Engine cold-storage exports, if enabled for this table).
2. Escalate to the data engineering on-call — this is now a data-recovery
   incident, not a metadata-pointer fix.
3. File a ticket referencing the `run_id`/`execution_id` of the failed step
   from the triage query above.

## Prevent recurrence

- Confirm Gate 0 (`.claude/contracts.md` §4) is active for this table and
  not sitting under an expired/stale `gate0_override_until`.
- Check the Dual-Optimizer Risk Report (Health Dashboard → 🛡️ Maintenance
  Governance tab, or `engine.core.governance.dual_optimizer_report()`) for
  this table — if an AWS Glue table optimizer is also enabled, that is the
  root-cause conflict Gate 0 exists to block. Disable the AWS-native
  optimizer or coordinate which system owns maintenance for this table.
