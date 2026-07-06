# Zamboni — Architecture Decisions

This file records decisions inferred from the current code (first-written
2026-07-05, Phase 0 — no prior version existed in git history). Locked
replatform decisions (D1-D6) live in `.claude/contracts.md`, not here — this
file is for decisions already baked into the shipped engine/app, plus notes
on where a replatform decision needs reconciling against them.

## Vacuum: single bare VACUUM, not separate expire/orphan calls
Athena engine v3's `VACUUM db.table;` handles snapshot expiry AND orphan file
removal in one call (`engine/operations/vacuum.py:1-20`). There is no
separate "delete orphans older than X hours" call — retention is entirely
TBLPROPERTIES-driven, set ahead of time by `property_sync.py`. Decided this
way because Athena v3 genuinely does not support VACUUM clauses/options
(bare syntax only) — the code comments call this out as a hard rule, not a
stylistic choice.
> REALITY vs contracts.md §5: the orchestrator's "REMOVE ORPHANS — two-phase"
> step (estimate scope → sanity check → delete with `older_than`) assumes an
> orphan-only call that can take an age parameter. That call doesn't exist
> in this codebase's Athena v3 model. Needs Sujith's decision on how Gate 0's
> orphan-sanity-abort logic attaches to a VACUUM call that always does both
> snapshot expiry and orphan removal together.

## Idempotency key excludes run_id by design
`engine/core/idempotency.py:31-63` hashes `table_fqn|operation|window_id` and
deliberately excludes `run_id` so that two different trigger paths
(EventBridge safety-net vs Control-M) processing the same table in the same
window produce the same `execution_id` and dedupe correctly. This is a
documented, intentional tradeoff (see the module docstring) — don't "fix" it
by adding run_id back into the hash.

## Backward-compatible column probing instead of migrations
`idempotency.py` and `property_sync.py` both catch failures, inspect the
error string for `"column" ... "<name>"`, and silently no-op if a column is
missing, rather than requiring a migration to run first. This lets the same
code run against environments at different schema versions. New columns
added for Workstream A (lock_id, metadata_location_before/after, etc.)
should follow this same probe-and-no-op pattern in any code that might run
before the `ALTER TABLE ADD COLUMNS` has been applied everywhere.

## EXECUTION_LOG_MODE: parquet path is best-effort, insert is the fallback of record
`engine/core/execution_log_parquet.py` never raises on `auto`/`both` modes —
any Parquet/add_files failure falls back to the row-by-row Athena INSERT in
`execution_log.py`. Only `EXECUTION_LOG_MODE=parquet` (strict) propagates the
error. Any schema change (new columns) must be added to both paths or the
fallback will silently write fewer columns than the primary path.

## Circuit breaker signature
`engine/core/circuit_breaker.py::trip(table_fqn, failure_count, dry_run)` —
contracts.md §5 refers to `circuit_breaker.trip(fqn)` (single arg). The
existing function requires `failure_count`; Phase 1b's orchestrator will need
to pass it through (e.g. from the failed verify step) rather than calling
with just the fqn.

## Local mode is a real SQLite shim, not a mock
`ZAMBONI_LOCAL_MODE=true` (`config/settings.py:118`) switches to a SQLite
database (`zamboni_local.db`) via `engine/utils/local_db.py`, which
translates Athena/Trino SQL (DATE_DIFF, DATE_TRUNC, etc.) into SQLite
equivalents with a hand-written char-scanner (not regex) for nested-paren
handling. This is a load-bearing dev/demo path, not a test double — treat it
as a real backend when reasoning about behavior in `ZAMBONI_LOCAL_MODE`.

## Phase 1b: §5-A orphan-estimation approach actually used
`engine/core/maintenance_ops.py::_preflight_sanity()` computes
`would_expire_pct` as `COUNT(snapshots older than the clamped floor) /
COUNT(total snapshots)` via a single query against Iceberg's `"$snapshots"`
metadata table — this is the literal reading of contracts.md §5-A step b,
not an approximation of file-level scope. A true *file*-level estimate
(files referenced only by snapshots about to expire) would require walking
manifest lists per candidate snapshot, which Athena's `$files`/$snapshots`
metadata views don't expose directly and which the existing `vacuum.py`
gap-fix set (1,2,3,9,10) has no primitive for. Snapshot-count-based scope is
the same signal `health_checker.py::_check_snapshots` already uses for
`expired_snapshots`, so this reuses an established approximation rather than
inventing a new one.
In `ZAMBONI_LOCAL_MODE`, `"$snapshots"`/`"$files"` have no SQLite
equivalent (see the "Local mode is a real SQLite shim" note above — the
shim translates Athena SQL syntax, it does not fabricate Iceberg metadata
tables). `read_sql_local()` returns an empty DataFrame for these queries,
so `_preflight_sanity()` reports `would_expire_pct=0.0` (nothing to abort
on) — matching how `health_checker.py` already treats an empty `$snapshots`
result as "nothing to flag" in local mode. Confirmed via a real local
dry run against the seeded DB (see Migration Progress entry).

## Phase 1b: 72h floor = rollback window rationale
`ORPHAN_MIN_AGE_HOURS_FLOOR=72` (contracts.md D2) is enforced in
`maintenance_ops.py::_clamp_vacuum_properties()` by setting
`vacuum_max_snapshot_age_seconds` on the table to at least 72h worth of
seconds before every VACUUM call — this value is deliberately never used as
a one-off delete-time argument (Athena engine v3 VACUUM has none) but as a
*standing table property* that persists between runs. The reasoning: since
a single combined VACUUM both expires snapshots and removes the files only
those snapshots reference, the floor is the guaranteed minimum time window
during which a bad commit's prior snapshot is still recoverable via Iceberg
time-travel / rollback before its files can be physically deleted. Widening
the floor (e.g. `ORPHAN_DEFAULT_AGE_HOURS=96`) buys a longer recovery
window at the cost of slower orphan reclamation — the floor is a lower
bound, not a target.

## Phase 1b: run_expire()/run_orphan_delete() collapsed into run_safe_vacuum()
The phase brief's intro paragraph asked for `maintenance_ops.py` to expose
`run_optimize()`, `run_expire()`, `run_orphan_delete()`. The CRITICAL
OVERRIDE in the phase prompt makes contracts.md §5-A (single combined bare
`VACUUM`, no separable orphan-only call) supersede that framing wherever it
conflicts — and it conflicts here, since there is nothing for a standalone
`run_orphan_delete()` to call that `run_expire()` wouldn't also call.
`engine/core/maintenance_ops.py` exposes `run_optimize()` and
`run_safe_vacuum()` instead — the latter performing all of §5-A's a→d
sequence (clamp → sanity → VACUUM → post-audit) as one atomic step. No dead
aliases were added for the unused `run_expire`/`run_orphan_delete` names.

## Phase 1b: gates 1-4 duplicated (not extracted) into orchestrator.py
`engine/core/orchestrator.py::run_table_maintenance()` must be independently
callable (contracts.md §5's acceptance CLI) and therefore re-implements
Gate 1 (Control-M), Gate 2 (window), idempotency, Gate 3 (frequency), and
Gate 4 (circuit breaker) rather than calling into
`hk_engine.py::_run_gates_and_operations()`, which is tightly coupled to
`HKEngine` instance state (`self._write_log`, `self._log_buffer`) and mixes
gates with the legacy operations flow this phase replaces. Phase 1a already
established the norm of not re-indenting large existing blocks (it split
Gate 0 into `_run_gates_and_operations` specifically to avoid re-indenting
~330 lines). Extracting `is_due()` to module level was safe and done (both
`hk_engine.py` and `orchestrator.py` now share it); extracting the rest
would have meant restructuring ~130 tested lines for one new caller, so the
duplication was kept — bounded to straightforward conditional checks, never
the safety-critical operations/verification logic.

## Phase 1b: pre-existing execution_log local-schema column drift (found, not fixed)
Running a real local dry run through `engine/core/orchestrator.py` surfaces
(as a caught, logged, non-fatal error) `"table execution_log has 37 columns
but 38 values were supplied"` on every `execution_log.write()` call in
`ZAMBONI_LOCAL_MODE`. This predates Phase 1b: `scripts/seed_local_db.py`'s
`execution_log` SQLite DDL + migrations are missing `partition_date`,
`archive_s3_path`, `pre_validation`, `post_validation` (all present in the
Athena DDL and in `execution_log.py::write()`'s positional INSERT) while
carrying three extra local-only columns (`vacuum_iterations`,
`oldest_snapshot_id`, `newest_snapshot_id`) that `write()` never
references. Nothing before Phase 1b ever exercised a real positional
INSERT against the seeded local `execution_log` table (existing tests
mock `read_sql`/`run_query` above this layer), so the drift was latent.
Out of scope here — flagged for a follow-up fix (either realign the local
DDL positionally, or move `execution_log.write()` to a named-column
INSERT so schema order stops being load-bearing).
