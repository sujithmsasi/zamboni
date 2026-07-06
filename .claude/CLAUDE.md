# Zamboni — Iceberg Table Governance Framework

Automated housekeeping (HK), archival, and lifecycle management for Apache
Iceberg tables at enterprise scale. Streamlit UI today; FastAPI+React
replatform in progress (see `.claude/contracts.md`).

## Three Engines
| Engine | Purpose | Trigger |
|---|---|---|
| HK Engine (`engine/engines/hk_engine.py`) | Compaction, snapshot expiry, orphan cleanup | EventBridge (hourly) or Control-M post-batch |
| Archival Engine (`engine/engines/archival_engine.py`) | Export-then-delete cold staging partitions to S3 Intelligent-Tiering | Weekly |
| Lifecycle Engine (`engine/engines/lifecycle_engine.py`) | Discover/clean up stale non-prod tables | Weekly |

## Repo Structure (verified 2026-07-05)
```
engine/core/        Registry, config, health, window evaluator, circuit breaker,
                     idempotency, property_sync, backpressure, commit_frequency,
                     cost_explorer, escalation, digest, execution_log(+_parquet)
engine/engines/      hk_engine.py, archival_engine.py, lifecycle_engine.py, base.py
engine/operations/   compaction.py, vacuum.py, archival.py, catalog_cleanup.py,
                      dynamic_router.py
engine/strategies/   binpack, sort, zorder
engine/utils/        athena_client, s3_client, glue_client, local_db (SQLite
                     shim + SQL translation), partition_utils, logger
engine/scripts/      run_hk.py, run_archival.py, run_cleanup.py,
                     run_lifecycle_cycle.py, run_lifecycle_scan.py
engine/cli/          register, enable, dry_run, cost_report, fleet_status
app/                 Streamlit app — Home.py + 12 pages under app/pages/,
                     shared components under app/components/
config/              settings.py (central; nothing reads os.environ elsewhere),
                     platform_settings.py
scripts/             seed_local_db.py (SQLite schema + migrations list),
                     seed_scale_test.py
deploy/              CodeDeploy/CodeBuild pieces — NO CloudFormation template
                     exists yet (see contracts.md §8 REALITY note)
tests/unit/          494 tests, all passing
```

## Engine Architecture Facts (Phase 0 audit — cite before assuming)

- **Vacuum model**: Athena engine v3 uses a single bare `VACUUM db.table;`
  call that does BOTH snapshot expiry and orphan file removal — there is no
  separate orphan-only delete call and no `older_than` parameter passed at
  call time. Retention is controlled entirely via TBLPROPERTIES
  (`vacuum_max_snapshot_age_seconds`, `vacuum_min_snapshots_to_keep`,
  `vacuum_max_metadata_files_to_keep`, `write_target_data_file_size_bytes`)
  set ahead of time by `engine/core/property_sync.py::apply_vacuum_properties`.
  See `engine/operations/vacuum.py:1-20` for the hard-rule comment block.
- **Gap numbering in vacuum.py**: Gaps 1, 2, 3, 9, 10 only (bare VACUUM SQL,
  orphan merged into VACUUM, iterative VACUUM for bloated tables, trivial-skip
  vs safety-floor skip, compaction-before-vacuum ordering guard). There is
  no partition-type-aware logic and no "12 gap types" — that framing belongs
  to the org-side vacuum hardening (see `.claude/org_divergence.md`).
- **Commit-frequency tiers** (`engine/core/commit_frequency.py:36-55`):
  HIGH >48 commits/day → 7d retention; MEDIUM 12-48 → 14d; LOW <12 → 30d.
  All three floors already exceed the new 72h orphan floor being introduced
  in Workstream A, so no numeric clamp conflict — only a mechanism conflict
  (see contracts.md REALITY note under §5).
- **Execution log** has two writer paths: `engine/core/execution_log.py`
  (per-row Athena INSERT, always available) and
  `engine/core/execution_log_parquet.py::ParquetLogBuffer` (batches to a
  single Parquet file on S3 then registers via `CALL system.add_files`).
  Mode selected by `EXECUTION_LOG_MODE` env var (`parquet|insert|both|auto`,
  default `auto`) — `config/settings.py:131`. New columns must be added to
  BOTH `execution_log.write()`'s positional INSERT (execution_log.py:117-152)
  AND `ParquetLogBuffer._entry_to_dict()` (execution_log_parquet.py:210-272).
- **Backpressure**: `engine/core/backpressure.py::wait_for_capacity` /
  `can_dispatch` check Athena `list_query_executions` + `batch_get_query_execution`
  against per-workgroup concurrency ceilings in `_DEFAULT_LIMITS` (lines 22-28).
  Fails open on any check failure. Workgroup routing itself lives in
  `engine/operations/dynamic_router.py::route()` (tier → execution class,
  size/files → worker type) — this is the "workgroup mapping" any new
  orchestration must route through.
- **Idempotency** (`engine/core/idempotency.py`): deterministic
  `execution_id = sha1(table_fqn|operation|window_id)` — `run_id` is
  deliberately excluded from the hash so two different triggers (EventBridge
  safety-net + Control-M) for the same table+window dedupe to the same ID.
  Backward-compatible no-op if `stream_registry.last_execution_id` column
  is missing.
- **Mode / session factory**: `get_mode()` and `get_boto3_session()` as
  described in contracts.md §2 DO NOT EXIST anywhere in this repo (verified
  via repo-wide grep). Only `ZAMBONI_LOCAL_MODE` (bool, `config/settings.py:118`)
  and `ZAMBONI_LOCAL_DB` exist today — no `ZAMBONI_MODE` env var, no
  `aws_local`/`aws_ec2` distinction. Phase 1a must implement this fresh.
- **Lock service / DynamoDB**: no lock table, lock service, or DynamoDB
  usage exists anywhere in the repo (verified via repo-wide grep). Gate 0
  does not exist — `engine/engines/hk_engine.py` only wires gate1/2/3
  (lines 268-336). All of Workstream A is genuinely greenfield here.
- **Deploy**: no `deploy/zamboni-cfn.yaml` or any CloudFormation template
  exists. `deploy/` has `buildspec.yml` (CodeBuild), `appspec.yml` +
  `scripts/{before,after}_install.sh`/`app_start.sh` (CodeDeploy hooks),
  `iam_policy.json`, `ec2-trust-policy.json`, `cloudwatch/{alarms,dashboard}.json`,
  `setup_ec2.sh`, `pipeline_config.md`. Phase 6 (contracts §10 R10.3) already
  plans to author the CFN fresh, so this is a documentation-only mismatch,
  not a blocker.
- **DATE_DIFF char-scan parser**: `engine/utils/local_db.py:150+` —
  `_translate_date_diff()` hand-scans for nested parens to rewrite
  `DATE_DIFF('unit', a, b)` into SQLite `julianday`/`CAST` equivalents for
  local-mode SQL translation. Do not replace with a regex — it was
  hand-written specifically to handle nested-paren args regex couldn't. See
  `.claude/context_hints.md`.

## Control-M Integration (complete)
`stream_registry` columns: `controlm_pipeline_job`, `controlm_hk_job`,
`dependent_on_controlm_job`, `dependent_job_type` (default `'glue'`),
`controlm_job_start_time`, `controlm_expected_duration_min` — all present in
`scripts/seed_local_db.py` (lines 80-83, 672-673). `controlm_jobs` registry
table + `app/components/ctrlm_helper.py` + CSV job-mapping import/export UI.
Gate1 in `hk_engine.py` reads these fields for the Control-M dependency check.

## Test Baseline (2026-07-05)
```
python -m pytest tests/unit/ -q   → 494 passed in 33.78s
ruff check .                      → All checks passed!
```

## Migration Progress
2026-07-05 Phase 0: baseline 494 tests (ruff clean), delta report done,
contracts committed to `.claude/contracts.md` with REALITY annotations.
Open questions: see the Conflict List in `.claude/contracts.md` header —
(1) the four legacy `.claude/*.md` memory files this phase was told to
"regenerate" never existed in this repo's git history (confirmed via
`git log --all`), so this is a first write, not a refresh; (2) vacuum.py's
actual mechanism (single bare VACUUM, TBLPROPERTIES-driven, no `older_than`
param) conflicts with contracts §5's "REMOVE ORPHANS — two-phase ... delete
with older_than=..." design — Gate 0/orchestrator design needs Sujith's
call on how orphan estimate/sanity-check/delete map onto a call that doesn't
take an age parameter; (3) `engine/operations/vacuum.py` already exists in
this repo with 5 named gaps (1,2,3,9,10), not "12 gap types" — the 12-gap
partition-aware version referenced in the phase 0 changelog is the org-only
version and belongs in `.claude/org_divergence.md`, not here.

2026-07-06 Phase 1a: Safety Core shipped — lock service, conflict detector,
Gate 0. 518 tests passing (494 + 24 new), ruff clean.
- `config/settings.py`: added `get_mode()`/`get_boto3_session()` (contracts
  §2, exactly as specified — this repo had neither) and the 8 maintenance-
  safety constants + `clamp_orphan_age()` helper. Kept `ORPHAN_MIN_RETENTION_HOURS`
  (=48) alongside the new `ORPHAN_MIN_AGE_HOURS_FLOOR` (=72) per Conflict
  List item 3 — documented in a comment rather than retired, since 2 existing
  tests assert it and nothing consumes it at runtime either way.
- `engine/core/lock_service.py` (new): `LockService` with SQLite backend
  (`maintenance_locks` table, transactional DELETE-expired-then-INSERT) and
  DynamoDB backend (conditional Put/Update/Delete, exactly per contracts
  §3.1). `mode` is an optional constructor param (defaults to `get_mode()`)
  so tests can pin a backend without mutating process env vars.
- `scripts/create_lock_table.py` (new): idempotent DynamoDB table creation
  with TTL on `expires_at` — the Phase 1a fallback for the CFN resource
  Phase 6 will add (no `deploy/zamboni-cfn.yaml` exists yet).
- `engine/core/conflict_detector.py` (new) + `engine/utils/glue_client.py::
  get_table_optimizer()`: live Glue `GetTableOptimizer` check (compaction/
  retention/orphan_file_deletion), `stream_registry.aws_opt_*` cache with
  `CONFLICT_CACHE_TTL_HOURS` staleness, write-back on live checks, and
  `scan_fleet()` for a fleet-wide force-refresh.
- Gate 0 wired into `engine/engines/hk_engine.py::_process_table`, before
  Gate 1: override check (`hk_config.gate0_override_until/reason/by`, logs
  `GATE0_OVERRIDDEN` and proceeds) → conflict check (`SKIP_AWS_OPTIMIZER_CONFLICT`)
  → in-flight check via new `execution_log.get_running()` (`SKIP_ALREADY_RUNNING`
  — no writer sets status='RUNNING' yet; Phase 1b's orchestrator will) → lock
  acquire (`SKIP_LOCK_HELD`). The per-table gate/operation body was split into
  `_run_gates_and_operations()` so the acquired lock releases via try/finally
  regardless of return path, without re-indenting ~330 existing lines.
- Schema: `stream_registry.aws_opt_*` (4 cols), `hk_config.gate0_override_*`
  (3 cols), `execution_log.{lock_id,metadata_location_before/after,
  snapshot_id_before/after,integrity_status}` (6 cols, appended at the end
  to match Athena's ALTER TABLE ADD COLUMNS append-only ordering) — added to
  both `scripts/seed_local_db.py` (DDL + migrations + new `maintenance_locks`
  table) and `sql/alter_safety_core.sql` (new — the Athena-side ALTER
  statements). `execution_log.py::write()` and `execution_log_parquet.py::
  _entry_to_dict()` both updated per the two-writer-path rule in
  `context_hints.md`.
- Deferred to Phase 1b (orchestrator): no code writes `execution_log.status
  = 'RUNNING'` yet, so Gate 0's in-flight check is wired but inert until the
  orchestrator's step-by-step logging lands; no heartbeat calls wired into
  `hk_engine.py` (operations aren't long-running yet in this engine — Phase
  1b's orchestrator owns heartbeat-during-long-ops per contracts §4 step 5).
- No changes to `vacuum.py` logic. No UI changes.

2026-07-06 Phase 1b: Orchestrator, Integrity Checker, Safe Vacuum shipped.
542 tests passing (518 + 24 new), ruff clean.
- **CRITICAL OVERRIDE applied**: contracts §5-A (bare combined Athena
  VACUUM, no `older_than` param, pointer ADVANCES) supersedes §5's
  three-step "REMOVE ORPHANS — two-phase ... delete with older_than="
  design and the phase brief's `run_expire()`/`run_orphan_delete()`
  naming. See `.claude/decisions.md` for the full reconciliation.
- `engine/core/integrity_checker.py` (new): `TableState` + `capture_state()`
  (Glue `Parameters.metadata_location` + Athena `"$snapshots"` count/latest
  snapshot id/ts; local mode returns a documented stub) and
  `verify_advanced(before, after, operation, min_snapshot_age_hours=None)`
  — `operation="optimize"` requires the pointer to change and snapshot
  count non-decreasing; `operation="vacuum"` requires the pointer to
  change (§5-A voids §5's old "pointer unchanged for orphan" rule),
  snapshot count non-increasing, and (if given) the resulting snapshot
  age ≥ the clamped floor.
- `engine/core/maintenance_ops.py` (new): `run_optimize()` (thin wrapper
  over `compaction.run_compaction`) and `run_safe_vacuum()` implementing
  §5-A's a→d sequence — property clamp (`vacuum_max_snapshot_age_seconds`
  ≥ max(policy, `ORPHAN_MIN_AGE_HOURS_FLOOR`, `SNAPSHOT_MIN_AGE_HOURS`)),
  pre-flight sanity (`"$snapshots"`/`"$files"` scope estimate, abort above
  `MAX_ORPHAN_DELETE_PCT` with no delete), the existing bare-VACUUM call
  via `vacuum.run_expire_snapshots` (unmodified), and post-audit
  files/bytes delta. `write_vacuum_audit()` persists one row per run
  (including sanity-aborts) to the new `vacuum_audit` table, through both
  local SQLite and Athena.
- `engine/core/orchestrator.py` (new): `run_table_maintenance(fqn,
  dry_run=True, run_id=None) -> RunResult` — Gate 0 (from 1a) → lock held
  → Gate 1/2/idempotency/Gate 3/Gate 4 (duplicated here rather than
  extracted from `hk_engine.py`, see decisions.md) → property sync →
  health check → capture_state → OPTIMIZE → `verify_advanced("optimize")`
  → capture_state → SAFE-VACUUM → `verify_advanced("vacuum")` → release
  (finally). Any integrity FAILED or step exception immediately trips the
  circuit breaker (not threshold-gated, unlike the legacy op-failure path)
  and sends an SNS alert, then halts remaining steps. Every step writes
  `execution_log` with `lock_id` + before/after metadata + snapshot ids +
  `integrity_status`, routed through the existing `EXECUTION_LOG_MODE`
  buffered/insert paths; the `RUNNING` marker is always written
  immediately (unbuffered) so Gate 0's in-flight check is visible to
  concurrent triggers for the run's duration.
- `engine/engines/hk_engine.py`: `ORCHESTRATED_MAINTENANCE` (new
  `config/settings.py` constant, default `true`) opt-in wired at the top
  of `_process_table()` — when true, delegates the whole table to
  `orchestrator.run_table_maintenance()` instead of the pre-existing
  Gate 0 + `_run_gates_and_operations()` flow below it (untouched,
  reachable by setting the constant to `false` — the rollback lever named
  in the phase brief). `_is_due()`'s body was promoted to a module-level
  `is_due()` function (delegated to, unchanged behavior) so both
  `hk_engine.py` and `orchestrator.py` share one frequency/dedupe
  implementation instead of duplicating `_FREQUENCY_HOURS`.
- Schema: new `vacuum_audit` table (contracts §3.3) —
  `sql/create_vacuum_audit.sql` (Athena DDL) + `scripts/seed_local_db.py`
  SQLite mirror. No new `execution_log`/`stream_registry`/`hk_config`
  columns needed — Phase 1a already added all six Safety Core columns
  `execution_log.write()` and `execution_log_parquet.py` use here.
- `config/settings.py`: added `ORCHESTRATED_MAINTENANCE` (bool) and
  `VACUUM_AUDIT_TABLE` constants.
- Test-suite fix (not a product regression): `ORCHESTRATED_MAINTENANCE`
  defaulting to `true` bypassed the legacy `_process_table()` gates that
  five `test_safety_core.py` tests and one `test_sprint5_gaps.py` test
  exercise directly — those six tests now pin
  `ORCHESTRATED_MAINTENANCE=False` since they test the rollback-lever path
  specifically, not `engine/core/orchestrator.py`.
- New tests: `tests/unit/test_integrity.py` (verify_advanced matrix, local
  stub, capture_state failure-safety), `tests/unit/test_maintenance_ops.py`
  (property clamp floor, orphan sanity abort/proceed, dry-run post-audit
  skip, vacuum_audit INSERT shape), `tests/unit/test_orchestrator.py`
  (happy path optimize→vacuum ordering, mid-step integrity failure halts +
  trips breaker, dry_run skips verify/mark_executed, backpressure routes
  through `_TIER_TO_WORKGROUP`, Gate 0 lock-held skip).
- Verified via a real local dry run against the seeded DB
  (`glue_catalog.finance_master_db.fin_payment_master`, gate2/frequency
  bypassed to reach the operations): produced
  `RunResult(status='SUCCESS', steps=[StepResult(step='vacuum',
  status='DRY_RUN', integrity_status='SKIPPED', ...)])` and a real
  `vacuum_audit` row (`aborted=0, dry_run=1, older_than_hours_used=720,
  sanity_pct=0.0`) — the `0.0` sanity_pct is the documented local-mode
  approximation (no `"$snapshots"`/`"$files"` equivalent in SQLite, see
  decisions.md), not a bug.
- **Found, not fixed** (pre-existing, out of scope — see decisions.md):
  `execution_log.write()`'s positional INSERT (38 values) doesn't match
  `scripts/seed_local_db.py`'s local `execution_log` table (37 columns,
  missing `partition_date`/`archive_s3_path`/`pre_validation`/
  `post_validation`, carrying three unrelated extra columns). Caught and
  logged by `local_db.py`, never raises, predates this phase — no test
  before Phase 1b exercised a real positional INSERT against the seeded
  local table.
- No changes to `vacuum.py` logic beyond what Phase 1a already made. No UI
  changes.
