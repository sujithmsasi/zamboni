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
