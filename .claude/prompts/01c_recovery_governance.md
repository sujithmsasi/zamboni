# PHASE 1c — Recovery Tooling & Governance Report

Context: 1a+1b shipped locks, Gate 0, orchestrated verified maintenance.
This phase adds (1) the metadata rollback tool that turns "metadata lost" from
a P1 into a 5-minute fix, and (2) the Dual-Optimizer Risk Report — the VP-facing
governance deliverable for the July 17 showcase. A Streamlit stopgap panel ships
now so the report is demo-able before React lands.

## Read first
.claude/CLAUDE.md, .claude/contracts.md (§4–§6 gates/system routers preview),
engine/core/integrity_checker.py, engine/core/conflict_detector.py,
engine/core/orchestrator.py, engine/utils/glue_client.py,
app/pages/4_Health_Dashboard.py, execution_log writer (read path for
metadata_location_before).

## Tasks

### 1. engine/core/recovery.py
```python
def get_rollback_candidates(fqn, limit=10) -> list[dict]
    # execution_log rows for fqn with metadata_location_before, newest first,
    # incl. operation, integrity_status, timestamps
def validate_rollback_target(fqn, metadata_location) -> ValidationResult
    # a) S3 head_object: the metadata.json still exists (NOT orphan-deleted)
    # b) parse it (s3 get, json) → confirm it's valid Iceberg metadata:
    #    has current-snapshot-id, snapshots list; report snapshot_id + count
    # c) list data-file existence spot-check: sample N manifests' file paths,
    #    head a few → report missing count (best-effort, capped)
def rollback_metadata(fqn, metadata_location, actor, reason, dry_run=True) -> bool
    # Glue update_table setting Parameters['metadata_location'] to the target,
    # preserving all other table parameters; records the CURRENT location as
    # a new execution_log row (operation='ROLLBACK', before=current,
    # after=target, integrity_status='VERIFIED' after re-read confirms);
    # audit_log entry; SNS notification.
```
- Local mode: simulate against SQLite (store a fake metadata_location in
  stream_registry if none exists — Phase 0 report says what's there; adapt).
- Safety: rollback REFUSES if validate finds the target file missing —
  the message must explicitly say "target metadata was likely orphan-deleted;
  rollback window is ORPHAN_MIN_AGE_HOURS_FLOOR (72h)".

### 2. scripts/recover_metadata.py (interactive CLI)
`python scripts/recover_metadata.py --fqn glue_catalog.db.tbl [--to <s3://...metadata.json>]`
- No --to → print numbered candidates (from get_rollback_candidates) with
  timestamps/operations/integrity, prompt selection.
- Runs validate → prints report → requires typing the table name to confirm →
  executes with dry_run=False. --dry-run flag supported. Uses get_boto3_session().

### 3. docs/runbooks/metadata_recovery.md
Concise operator runbook: symptoms (Athena "metadata not found", missing
snapshot), triage queries (execution_log lookups), the CLI walkthrough,
the 72h window explanation, escalation path, and "prevent recurrence" pointing
at Gate 0 + the conflict report. Also docs/runbooks/lock_operations.md:
viewing/force-releasing locks (DynamoDB console + upcoming DELETE /api/locks),
when force-release is safe.

### 4. Governance backend: conflict report query (pre-API)
engine/core/governance.py:
```python
def dual_optimizer_report(page=1, size=50, domain=None) -> dict
    # stream_registry ⋈ hk_config: hk_enabled true AND any aws_opt_* true;
    # columns: fqn, domain, layer, tier, which optimizers, aws_opt_checked_at,
    # gate0_override_until; total count; supports export (return full list flag)
def fleet_conflict_summary() -> dict
    # counts: scanned, conflicted, stale-cache, overridden — Home/Health KPIs
```
This is exactly what GET /api/conflicts will call in Phase 2 — write it as the
reusable engine function now.

### 5. Streamlit stopgap panel (demo insurance)
In app/pages/4_Health_Dashboard.py add a new tab/section
"🛡️ Maintenance Governance":
- KPI row from fleet_conflict_summary().
- itables grid of dual_optimizer_report (standard itables config from
  context_hints), CSV export button.
- "🔄 Rescan conflicts" button → conflict_detector.scan_fleet (respect dry-run
  none needed — it's read+cache-write; show progress count) → flash + rerun.
- Recent integrity failures: execution_log where integrity_status='FAILED'
  last 7 days, itables.
Follow ALL existing page patterns (_current_page, flash pattern, pd.isna).

### 6. Tests (tests/unit/test_recovery.py, test_governance.py)
- validate: missing S3 object → invalid with the 72h message; valid metadata
  json → ok with snapshot info (mock s3/glue via Stubber or existing mock style).
- rollback: dry_run makes no glue call; real path calls update_table with
  preserved parameters; refuses on invalid target; writes ROLLBACK log + audit.
- candidates ordering; governance report filters + summary counts on seeded data.

## Acceptance criteria
- pytest all green (prior + new), ruff clean.
- Local demo: `python scripts/recover_metadata.py --fqn <seeded fqn> --dry-run`
  shows candidates → validation → dry-run success (paste transcript).
- Streamlit: Health Dashboard governance tab renders with seeded data (describe
  what it shows; the seed should include ≥1 table with aws_opt_* true — add to
  seed data if absent).
- Both runbooks written; Migration Progress appended.
- decisions.md: note the "72h floor = guaranteed rollback window" line — this
  is showcase copy.

Suggested commit: `feat(engine): metadata rollback tooling, runbooks, dual-optimizer governance report + stopgap panel`
