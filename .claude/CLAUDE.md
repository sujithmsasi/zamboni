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
                     cost_explorer, escalation, digest, execution_log(+_parquet),
                     control_plane (SQLite-primary for 5 config tables —
                     stream_registry/hk_config/domain_registry/
                     nonprod_registry/controlm_jobs; engine reads/writes it
                     via registry.py/config.py/lifecycle_engine.py)
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
                     seed_scale_test.py, init_control_plane_db.py,
                     control_plane_sync.py, control_plane_backup.py,
                     control_plane_integrity_check.py
deploy/              zamboni-cfn.yaml (Phase 6, complete standalone stack) +
                     CodeDeploy/CodeBuild pieces (buildspec.yml, appspec.yml,
                     scripts/, systemd/zamboni-{api,streamlit}.service)
api/                 FastAPI app (Phase 2) — main.py, deps.py, models.py,
                     routers/ (8, one per contracts §6 section), services/
                     (8, lift SQL from the matching Streamlit page)
ui/                  React 18 + TS + Vite + Ant Design v5 (Phase 3-5b) — all
                     13 contracts §7 routes are real pages, no
                     PlaceholderPage remains; UI is feature-complete,
                     Streamlit is fallback-only; see ui/PATTERN.md for the
                     canonical page structure and the page/endpoint
                     inventory table
tests/unit/          593 tests, all passing
tests/api/           99 tests — run as its own `pytest tests/api`
                     invocation, not combined with tests/unit (see Phase 2
                     entry below for why)
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

## Test Baseline (2026-07-06, updated through 2026-07-09 SQLite control-plane migration)
```
python -m pytest tests/unit -q   → 593 passed
python -m pytest tests/api -q    → 99 passed   (separate invocation — see Phase 2 entry)
ruff check .                     → All checks passed!
cd ui && npx tsc --noEmit        → clean
cd ui && npm run build           → clean
cd ui && npm run lint            → clean (oxlint)
cfn-lint deploy/zamboni-cfn.yaml → zero errors, zero warnings
```
> Note: 562→568 unit / 97→99 api reflects two commits made directly by
> Sujith between the Phase 5b close-out and Phase 6 start
> (`e8db72e` escalation drawer/Advanced tab validation/dry-run ramp-up,
> `584e8df` `domain_registry.is_active` enforcement) that were never given
> their own Migration Progress entry — recorded here so the count isn't
> read as a Phase 6 regression from the previously-documented 562/97.

## Deferred Work
- **Help doc / user guide generation**: intentionally not started. Sujith
  wants this done as a pass at the end of the project (once the page/feature
  set stabilizes), not incrementally alongside each wave — do not
  proactively draft help docs, in-app tooltips-as-documentation, or a user
  guide until asked. When that pass starts, the per-phase "Parity
  checklists" and `> ADDED` notes throughout Migration Progress below are
  the source material for what shipped and where it deviated from the
  Streamlit twin.

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

2026-07-06 Phase 1c: Recovery Tooling & Governance Report shipped —
Workstream A (Engine Hardening) is now complete. 562 tests passing
(542 + 20 new: 13 in `test_recovery.py`, 7 in `test_governance.py`), ruff
clean.
- `engine/core/recovery.py` (new): `get_rollback_candidates()` (execution_log
  rows with `metadata_location_before` set, newest first),
  `validate_rollback_target()` (S3 `head_object` existence check -> parse ->
  confirm valid Iceberg metadata -> best-effort capped data-file spot-check),
  `rollback_metadata()` (Glue `update_table` preserving all other
  parameters, writes an `execution_log` `ROLLBACK` row + `audit_log` entry +
  SNS alert). Refuses (no Glue call, no log write) when validation reports
  the target missing, citing `ORPHAN_MIN_AGE_HOURS_FLOOR` (72h) verbatim —
  see `.claude/decisions.md`'s "72h floor = guaranteed rollback window" note.
  Local mode simulates validate/rollback (documented gap, same pattern as
  `integrity_checker.capture_state()`) — see decisions.md.
- `engine/utils/glue_client.py`: added `update_metadata_location()` — sets
  `Parameters['metadata_location']` via `update_table`, preserving every
  other table field/parameter (filters out response-only keys like
  `DatabaseName`/`CreateTime`/`VersionId` rather than hand-listing what to
  keep).
- `engine/utils/s3_client.py`: added `get_object_bytes()` (thin
  `get_object(...)["Body"].read()` wrapper) so `recovery.py` never
  constructs raw boto3 clients inline — keeps every AWS call mockable via
  the same `monkeypatch.setattr(module, "name", ...)` convention already
  used throughout `tests/unit/`.
- `scripts/recover_metadata.py` (new): interactive CLI —
  `--fqn` (+ optional `--to <s3://...>` to skip candidate selection),
  lists/validates/confirms (types the table FQN back for a real rollback),
  `--dry-run`/`--no-dry-run`. Routes the process's default boto3 session
  through `get_mode()` (contracts.md §2) but is a no-op in
  `ZAMBONI_LOCAL_MODE` — no AWS calls happen there, and
  `boto3.Session().profile_name` always resolves to the literal string
  `"default"` even with nothing configured, so forwarding it blindly into
  `setup_default_session()` would force a profile lookup that fails on a
  machine with no `~/.aws/config` at all.
- `engine/core/governance.py` (new): `dual_optimizer_report()`
  (`stream_registry` ⋈ `hk_config`, `hk_enabled=true AND any aws_opt_*
  true`, paged or `export_all` for CSV) and `fleet_conflict_summary()`
  (scanned/conflicted/stale-cache/overridden counts) — written as the
  reusable engine functions `GET /api/conflicts` (contracts.md §6) will call
  in Phase 2. Staleness/override computed in Python (parsed timestamp +
  `age_hours`), not SQL `NOW() - INTERVAL 'n' HOUR` — see decisions.md for
  why (local_db's SQL translator only rewrites DAY-unit intervals).
- `app/pages/4_Health_Dashboard.py`: new "🛡️ Maintenance Governance" section
  — KPI row from `fleet_conflict_summary()`, itables grid of
  `dual_optimizer_report()` with CSV export, "🔄 Rescan conflicts" button
  (`conflict_detector.scan_fleet()` + flash + rerun, following the existing
  `st.session_state["*_flash"]` pattern from `3_Policy_Configuration.py`),
  and a "Recent Integrity Failures — Last 7 Days" grid. Verified with
  `streamlit.testing.v1.AppTest` (headless, no browser in this
  environment) against the seeded local DB: renders with no exceptions, all
  five governance KPIs show real values, the rescan button and grid render.
- `engine/core/audit.py`: added `AuditAction.METADATA_ROLLBACK`.
- `scripts/seed_local_db.py`: new migration
  `stream_registry.metadata_location` (local-simulation-only, see
  decisions.md); `fin_payment_master` seeded with `aws_opt_compaction=1` +
  recent `aws_opt_checked_at` (the ≥1 dual-optimizer-conflict demo row the
  Governance tab needs) plus a simulated `metadata_location`; new
  `seed_rollback_demo_rows()` adds 2 realistic `execution_log` rows
  (`vacuum` then `optimize`, chained `metadata_location_before/after`) so
  `get_rollback_candidates()` has real data to show — inserted via its own
  `insert_rows()` call, not concatenated onto `seed_execution_log()`'s list,
  since `insert_rows()` derives its INSERT column set from `rows[0].keys()`
  alone and would have silently dropped every Safety-Core column otherwise
  (found and fixed during this phase's own dry-run verification).
- `requirements.txt`: added `fastavro` (optional, lazily imported, same
  graceful-degradation pattern as `itables`) for the data-file spot-check.
- `docs/runbooks/metadata_recovery.md` and `docs/runbooks/lock_operations.md`
  (both new): operator runbooks — symptoms, triage queries, CLI walkthrough,
  the 72h window explanation, escalation path, prevent-recurrence pointers;
  lock viewing/force-release (DynamoDB console + CLI, SQLite for local mode,
  when force-release is safe, the upcoming `DELETE /api/locks/{fqn}`).
- Verified end-to-end against the seeded local DB:
  `python scripts/recover_metadata.py --fqn
  glue_catalog.finance_master_db.fin_payment_master --dry-run` lists 2
  candidates, validates the selected one (simulated, local mode), prompts
  for a reason, writes a `DRY_RUN`/`SKIPPED`-integrity `execution_log` row
  and an `audit_log` row with the reason and before/after locations, sends a
  dry-run SNS log line. Also verified the refusal path directly against a
  `--to ..._orphaned.metadata.json` target: prints the exact required 72h
  message and exits non-zero with no writes.
- No changes to `vacuum.py`/orchestrator/integrity-checker logic — this
  phase is additive tooling only.
- **Workstream A (Engine Hardening) is complete as of this phase** — tagged
  `engine-hardening-v1`.

2026-07-06 Phase 2: FastAPI Layer shipped — Workstream B begins. 623 tests
passing (562 unit + 61 api), ruff clean. 44 endpoints across 8 routers, all
paths/methods exactly matching contracts.md §6 (contract-smoke test guards
this). Engine called in-process throughout; zero Streamlit files touched.
- `api/main.py`: FastAPI app, CORS (dev origin `http://localhost:5173`),
  three exception handlers (HTTPException/RequestValidationError/generic
  Exception) all converting to the locked `{data,pagination,error}`
  envelope, `ui/dist` static mount (guarded, routes registered first — no
  `ui/` exists yet so this is currently a no-op). `/docs` on by default.
- `api/deps.py`: `get_current_user()` env-var stub (`ZAMBONI_USER`, default
  `"local-dev"` — the OIDC seam per contracts §10 D5), `get_dry_run_default()`,
  `PageParams` (page/size, size≤250).
- `api/models.py`: Pydantic v2 request models mirroring each engine
  function's signature, `MutationResult`, and `envelope()` — the latter
  runs every response through a recursive `_clean()` (NaN/NaT → null,
  numpy scalars → native, Timestamp/date → ISO string) so services can
  pass `DataFrame.to_dict(orient="records")` straight through; found this
  the hard way — Starlette's `JSONResponse` sets `allow_nan=False`, so any
  SQLite NULL surfacing as pandas `NaN` 500'd every list endpoint until
  this was centralized in one place instead of patched per-service.
- `api/services/*.py` (8 files: tables, policies, gates, lifecycle,
  executions, controlm, settings, system): lift the query/mutation SQL
  patterns from the corresponding Streamlit pages (2_Table_Registration.py
  browse/register/bulk-Control-M/job-mapping CSV import-export,
  3_Policy_Configuration.py view/edit/bulk-apply/templates,
  6_Dry_Run_Viewer.py gate summary + SQL preview, 9_NonProd_Lifecycle.py
  state overview/exempt/claim, 7/8/10 execution-log/cost/stale queries,
  11_Settings.py + 12_Audit_Log.py) into standalone functions the routers
  call — pages keep their own inline SQL this phase, nothing shared by
  import, per the phase brief's "lift, never duplicate."
- `api/routers/*.py` (8 files, one per contracts §6 section): thin HTTP
  layer over the services — builds `AuditEvent`s inline (actor from
  `get_current_user()`), returns `event.audit_id` as the mutation's
  `audit_id` (contracts' "uuid recorded in the event" option, since
  `engine.core.audit.audit()` doesn't return one and extending it wasn't
  needed — `AuditEvent.audit_id` already auto-generates via
  `default_factory=uuid4`). Escalation create/update/delete are the one
  exception: `engine.core.escalation.upsert_entry`/`delete_entry` return
  bare `bool`, so those three routes generate their own `uuid.uuid4()` for
  the envelope's `audit_id` rather than threading a new return value
  through the engine layer for three call sites.
- **Route-registration-order bug found and fixed during this phase's own
  testing**: `api/routers/tables.py` originally declared
  `GET /api/tables/{fqn:path}` before `GET /api/tables/job-mapping/export`
  — FastAPI matches GET routes in registration order and the `{fqn:path}`
  catch-all greedily matched `job-mapping/export` as a table FQN, 404'ing
  every request to the literal route behind it. Fixed by moving both
  `{fqn:path}` routes (GET, PUT) to the end of the file with a comment
  explaining why; this class of bug is exactly what the contract-smoke
  test (route existence) does NOT catch, since the path returns *some*
  response — only exercising the endpoint via TestClient caught it.
- **Found, not fixed** (pre-existing engine behavior, out of scope):
  `engine/utils/athena_client.py::run_query()` dispatches to
  `local_db.run_query_local()` when `ZAMBONI_LOCAL_MODE` is true *before*
  checking its own `dry_run` parameter — so in local mode, every SQL
  mutation writes regardless of `dry_run`. This predates Phase 2 (every
  Streamlit page's dry-run toggle has the same gap locally) and isn't
  something a new API layer should silently paper over or fix as a
  drive-by; documented here and in `tests/api/test_tables.py`'s
  `test_register_table_dry_run` (which asserts the DRY_RUN audit trail
  and envelope shape instead of data immutability, since the latter isn't
  true in local mode). One place this phase *did* fix it properly:
  `policies_svc.update_template()` writes straight to
  `config/policy_templates.json` on disk (not through `run_query`), so
  `dry_run` is genuinely honored and verified there — caught because the
  first version of that test accidentally overwrote `STAGING_DEFAULT`'s
  description in the real repo file (reverted via `git checkout`) before
  the `dry_run` gate was added.
- `engine/core/audit.py`: added `AuditAction.GATE0_OVERRIDE_SET` (contracts
  §6 gates router requirement).
- `engine/core/lock_service.py`: added `LockService.list_locks()` and
  `LockService.release_force(table_fqn)` (local SQLite + DynamoDB backends)
  for `GET /api/locks` / `DELETE /api/locks/{fqn}` — the latter is an
  unconditional delete (no `lock_owner` ConditionExpression), audited by
  the caller (`system_svc.release_lock`), not the lock service itself.
- `requirements.txt`: added `fastapi`, `uvicorn[standard]`,
  `python-multipart` (file uploads), `httpx` (FastAPI `TestClient`, tests
  only).
- `tests/api/` (new dir): `conftest.py` seeds a dedicated SQLite file
  (`tests/api/_zamboni_api_test.db`, cleaned up after the session) by
  calling `scripts/seed_local_db.py::main()` directly, with
  `ZAMBONI_LOCAL_MODE`/`ZAMBONI_MODE`/`ZAMBONI_LOCAL_DB`/`ZAMBONI_USER` set
  at conftest import time — this suite must run as its own `pytest
  tests/api` invocation (fresh interpreter), not combined into the same
  process as `tests/unit`, since `config/settings.py` reads
  `ZAMBONI_LOCAL_MODE` once at first import and several modules
  (`engine/utils/athena_client.py`, `glue_client.py`) bind it as a
  module-level constant at that time — setting env vars later in the same
  process wouldn't reach them. `tests/unit/` mocks AWS calls directly and
  doesn't need this. `test_contract_smoke.py` fetches `/openapi.json` via
  `TestClient` (robust to FastAPI's lazy `_IncludedRouter` internals in
  this installed version, where `app.routes` doesn't eagerly flatten
  included routers) and asserts all 44 contracts §6 (method, path) pairs
  are present. Per-router files cover: happy/filtered GET with envelope+
  pagination assertions, one dry-run mutation, one validation failure
  (422), plus the Gate 0 override cap test (`gate0_override_until` far
  beyond `GATE0_OVERRIDE_MAX_HOURS` is clamped, not rejected) and a 2-row
  CSV job-mapping import round-trip (one match, one blank line stripped).
- Verified end-to-end: `python -m pytest tests/unit -q` → 562 passed;
  `python -m pytest tests/api -q` → 61 passed; `ruff check .` → All checks
  passed. `uvicorn api.main:app --port 8000` against the existing seeded
  `zamboni_local.db` (`ZAMBONI_LOCAL_MODE=true`): `/docs` → 200;
  `curl localhost:8000/api/system/mode` →
  `{"data":{"mode":"local","app_env":"dev","dry_run_default":true},"pagination":null,"error":null}`;
  `curl "localhost:8000/api/tables?page=1&size=5"` → 5 rows with
  `"pagination":{"page":1,"size":5,"total":17}`.
- No Streamlit file modified this phase.

2026-07-06 Phase 3: React Foundation + Home shipped, then grew well past
its original "Home only" scope into a second fully-built page (Health
Dashboard) plus several rounds of UI polish, per Sujith's live review.
562 unit + 61 api tests passing throughout (backend additions were
additive-only), ruff clean. Zero engine logic touched.
- `ui/` (new): Vite + React 18 + TypeScript (strict) + Ant Design v5 +
  TanStack Query + react-router-dom, exactly per contracts §7. `npx tsc
  --noEmit` and `npm run build` both clean.
- **Theme**: started from contracts §7's light-sidebar starting tokens,
  then fully replaced with "Zamboni Arctic Blue" (`.claude/ui_design.md`,
  a design doc Sujith supplied mid-phase) — dark navy gradient Sider,
  Phosphor duotone icons (`@phosphor-icons/react`, replacing
  `@ant-design/icons` everywhere, not just the sidebar), mint-green
  "all clear" safety motif reused across the dry-run banner and Home's
  Governance card, coastal-gradient KPI cards. See `.claude/decisions.md`
  for the full reconciliation (including that the design doc's sample
  `fill` icon prop doesn't exist in the real Phosphor API — approximated
  with `color="currentColor"` + CSS-driven hover/active states instead).
- **Sidebar**: 13 routes grouped into 5 categories (Overview/Registry/
  Monitoring/Governance & Safety/Administration), one icon hue per
  category (not per-icon — reconciles Sujith's "add color" ask with the
  design doc's "no rainbow icons" rule). `position: sticky` + internal
  flex layout (brand → scrollable menu region → user-menu footer) so it
  stays fixed while page content scrolls. Took several rounds to get
  right — a double-nested `overflow:auto` was letting the *outer* Sider
  reserve its own scrollbar width on Windows' classic (non-overlay)
  scrollbars, squeezing nav labels; fixed by making only the inner menu
  region scrollable, then tightening `Menu` item spacing (`theme.ts`)
  until the full 13-item/5-group list fits without scrolling at all on
  normal viewport heights. Full history in `.claude/decisions.md`.
- **Home** (`ui/src/pages/Home/`, complete): 5 clickable KPI cards (each
  opens a `CoverageDetailModal` or `ExecutionsDetailModal` drill-down),
  Fleet Coverage by Domain + Execution Trend charts (`recharts`, newly
  added), a state-driven Governance card (mint when
  `conflicts.conflicted === 0 && activeLocks === 0`, amber otherwise —
  deliberately not static, since a fixed mint fill was contradicting a
  red conflict chip inside it), Recent Activity grid at the bottom.
  `hooks.ts`/`index.tsx` split is the canonical pattern documented in
  `ui/PATTERN.md` for Waves 1-2 to replicate.
- **Health Dashboard** (`ui/src/pages/HealthDashboard/`, also built —
  was meant to stay a `PlaceholderPage` this phase, but Sujith asked for
  a "major uplift" mid-session): Storage Reclaimed trend + Top Tables by
  Reclaim leaderboard, Estimated Athena Cost trend, Fleet Health
  Scorecard (donut) + Unhealthy Tables grid, Non-Prod Lifecycle Funnel,
  Dry-Run Adoption table. Two deliberate honesty calls, both in
  decisions.md: the health score is a proxy from failures/integrity/
  conflicts (not a live per-table Iceberg `$snapshots` call — too
  expensive fleet-wide), and "Dry-Run Adoption" is a snapshot ("which
  domains have tables waiting longest") rather than a fabricated
  historical trend, since the schema has no graduation-event log to plot
  a real one against.
- **Backend**: `api/services/executions_svc.py::health_kpis()` extended
  three separate times (all additive fields, same locked route — contracts
  §6 already documents it as serving "home + health dashboard numbers"):
  `coverage_by_domain`/`execution_trend` for Home's charts, then
  `reclaimed_storage_trend`/`top_tables_by_reclaim`/`cost_trend`/
  `storage_savings`/`fleet_health`/`nonprod_funnel`/`dry_run_adoption` for
  Health Dashboard. `api/services/system_svc.py::system_mode()` gained a
  `user` field for the new sidebar user/logout menu (still a stub — no
  real session exists yet, contracts §10 D5's OIDC seam is still
  unbuilt; "Log out" shows a `message.info` saying so rather than faking
  a session). `config/settings.py`: added `S3_STANDARD_USD_PER_GB_MONTH`
  (flat-rate storage-cost estimate, same convention as the existing
  $5/TB Athena estimate).
- **Found and fixed, not pre-existing to this phase**:
  `scripts/seed_local_db.py`'s dry-run ramp-up date had a sign bug
  (`_date(-7 + 14)` == `_date(7)` == 7 days *ago*, not "expires in 7 days"
  as the comment claimed) — `in_dry_run`/the new `dry_run_adoption` were
  silently always 0/empty in local mode until fixed to `_date(-7)`. Also
  added `seed_vacuum_audit_demo_rows()`/`seed_archival_demo_rows()` —
  `vacuum_audit` had zero seeded rows (only ever populated by hand during
  Phase 1b's own manual verification) and `execution_log` had zero
  `engine='archival'` rows at all, so the new reclaim charts had nothing
  to show without them.
- **Found and fixed during this phase's own prod-serve verification**:
  `api/main.py`'s `ui/dist` static mount 404'd on a direct GET to any
  client-side route (`/health`, `/tables`, ...) — `StaticFiles(html=True)`
  serves real files/index.html on exact matches only, no SPA fallback for
  unmatched paths. Invisible in dev (Vite's dev server has this built in)
  but real in prod-serve. Fixed with a `_SPAStaticFiles` subclass that
  retries `index.html` on a 404 — caught a second bug fixing the first:
  `except HTTPException` (FastAPI's subclass) never matched the
  `starlette.exceptions.HTTPException` instance Starlette's own
  `StaticFiles.get_response()` actually raises, so the first version of
  the fallback silently never fired. Verified: `/`, `/health`, `/tables`
  all 200 with the app shell; `/api/system/mode` still returns JSON
  (registered before the mount, unaffected).
- Verified end-to-end: `python -m pytest tests/unit -q` → 562 passed;
  `python -m pytest tests/api -q` → 61 passed; `ruff check .` → All
  checks passed; `npx tsc --noEmit` and `npm run build` (ui/) both clean;
  prod-serve (`uvicorn api.main:app`, no Vite dev server running) confirmed
  above. Full design-decision history — including the several rounds of
  visual back-and-forth — lives in `.claude/decisions.md`, not repeated
  here.

2026-07-06 Phase 4: Pages Wave 1 shipped — 7 read-heavy pages, RULE ZERO
(Home pattern replicated exactly: `index.tsx`/`hooks.ts`/`components/`
split, `<DataGrid>`, query-key convention, Skeleton/Alert/Empty states).
631 tests passing (562 unit + 69 api, up from 61 — 4 new domains tests + 1
integrity_status filter test + 3 existing suites untouched), ruff clean,
`tsc --noEmit` clean, `npm run build` clean, `npm run lint` (oxlint) clean.
Zero engine logic touched.

- **DomainManagement — no domains router existed anywhere in contracts.md
  §6** (confirmed: Phase 2's REALITY note only evaluated the 8 sections
  already specified there, and no `api/routers/domains.py` was ever
  written). Added `api/services/domains_svc.py` (`list_domains` — enriches
  with a per-domain `table_count` the way the Streamlit twin does,
  `get_domain`, `create_domain` wrapping `engine/core/registry.py::
  register_domain`, `update_domain` — raw `UPDATE domain_registry` over the
  Streamlit edit form's exact field set) + `api/routers/domains.py` (GET
  `/api/domains`, GET `/api/domains/{name}`, POST `/api/domains`, PUT
  `/api/domains/{name}` — same envelope/dry_run/audit conventions as every
  other router, `AuditAction.DOMAIN_CREATE`/`DOMAIN_UPDATE` already existed)
  + `RegisterDomainRequest`/`UpdateDomainRequest` in `api/models.py`.
  `.claude/contracts.md` §6 updated with a `> ADDED (Phase 4):` subsection;
  `tests/api/test_contract_smoke.py`'s route count bumped 44→48 (4 new
  routes); `tests/api/test_domains.py` (new, 7 tests) covers list/get/404/
  create-dry-run/validation-400/update-dry-run/update-404.
- `api/services/executions_svc.py::list_executions()` / `api/routers/
  executions.py`: added an `integrity_status` filter param (additive,
  optional) — needed for the Health Dashboard's integrity-failures grid;
  contracts.md §6 already documented `GET /api/executions` generically
  enough that this didn't need its own `> ADDED` note, just a test
  (`test_list_executions_filtered_by_integrity_status`).
- **Shared infra additions** (all additive, no pattern deviation): `ui/src/
  utils/csv.ts::downloadCsv()` — client-side CSV generation for endpoints
  that return full JSON rather than a real CSV stream (`GET /api/
  conflicts?export=csv` and the executions/audit list endpoints all return
  JSON envelopes, not `text/csv` — unlike `/api/tables/job-mapping/export`,
  which `CsvButtons` already assumes). `components/DataGrid.tsx` gained an
  optional `expandable` passthrough prop (AntD `Table`'s own prop, just not
  wired through before) for Execution Log's and Audit Log's row-expand
  detail panels. `GovernanceChip` promoted from `pages/Home/components/` to
  `components/` (now shared by Home, HealthDashboard's GovernanceSection,
  and DryRunViewer's gate chips) — reuse-driven, not a new pattern. New
  `api/hooks/`: `useExecutions.ts` (list/detail/dryrun/costs),
  `useGates.ts` (conflicts list/export/rescan mutation), `useAudit.ts`,
  `useDomains.ts` (list/detail/create/update), `useTables.ts` (search-only,
  minimal — Table Registration itself is a Wave 2 page). `useSystem.ts`
  gained `useReleaseLock()` and an optional `refetchInterval` param on
  `useLocks()`.
- **HealthDashboard** (`components/GovernanceSection.tsx`, new — extends
  the existing page rather than replacing it): Dual-Optimizer Risk Report
  KPI row (reuses `health_kpis().conflicts` already fetched on page load —
  no extra call), domain-filtered + paginated conflicts `<DataGrid>`,
  client-side CSV export, "Rescan conflicts" mutation invalidating
  `['conflicts']` + `['health']`, and a Recent Integrity Failures grid via
  the new `integrity_status` filter. This is the page the phase brief
  named as "REPLACES the 1c Streamlit stopgap as the governance showcase
  surface" — Streamlit's own Maintenance Governance section is untouched
  (still lives at `app/pages/4_Health_Dashboard.py`, this doesn't delete
  it, only supersedes it in the React app).
- **LiveActivity** (new page): the phase brief's one hard-refetch-interval
  page — `useExecutionsList(..., 10_000)` on both the running and recent
  grids plus `useLocks(10_000)`, replacing the Streamlit twin's blocking
  `time.sleep(30)` + manual toggle with real background polling.
  `components/LocksStrip.tsx` — active-lock chips with a force-release
  confirm `Modal` (admin override copy) wired to the new `useReleaseLock`
  mutation, audited server-side by `system_svc.release_lock`.
- **ExecutionLog** (new page): fqn/engine/status/date-range filters, row
  expand → `components/ExecutionDetailPanel.tsx` lazily calling
  `GET /api/executions/{id}` only once a row is actually opened, CSV
  export.
- **CostReport** (new page): group_by (domain/layer/tier) + period
  selectors, `Statistic` KPI row from `costs().totals`, plain `<Table>`
  (not `<DataGrid>` — `by_group` is a fixed array, not server-paginated,
  same documented exception as `UnhealthyTablesGrid`) for the cost-by-group
  breakdown, live-billing-vs-estimate-mode `Alert` banner. No charts added
  (phase brief: "prefer skipping charts this wave" unless
  `@ant-design/plots` is added — it wasn't, `recharts` already covers Home/
  Health Dashboard and a second charting lib for one page isn't worth it).
- **AuditLog** (new page): days/action/actor filters map to
  `GET /api/audit`'s real params; status is filtered client-side over the
  current page (backend has no status param — same approach the Streamlit
  twin used, filtering its own already-fetched window in pandas). Row
  expand → `components/AuditDetailPanel.tsx` showing before/after JSON.
  KPI row (total/failures/rejected/live-actions) computed from the
  status-filtered current page.
- **DryRunViewer** (new page): `Select showSearch` fed by
  `useTablesSearch()` (new, minimal `GET /api/tables?search=` hook) →
  `GET /api/dryrun/{fqn}` → Descriptions/GovernanceChip-based gate summary
  (parity-checked field-by-field against the Streamlit twin's gate rows)
  + conditional SQL preview (only for `compaction_strategy='binpack'`,
  exactly matching the Streamlit twin's own conditional) with a
  clipboard-copy button. Verified live end-to-end (search → select →
  render) against `fin_payment_master` (strategy=`zorder`, correctly hid
  the SQL preview since it's binpack-only).
- **DomainManagement** (new page): plain `<Table>` (bare-array response,
  same DataGrid exception as CostReport) with a Register/Edit
  `components/DomainFormModal.tsx` — create mode omits `domain_name` from
  edit, edit mode adds `is_active`/`digest_enabled`/`digest_email` fields
  plus a read-only "Escalation / Notification Routing" `Descriptions` block
  (owner email / digest recipient / digest-enabled state) sourced from the
  domain record already in hand — no new endpoint needed for it.
- **Not ported (documented, not silently dropped)**: LiveActivity's
  "Today's Engine Summary" aggregate table and grouped "Currently Running"
  view (no aggregation endpoint in contracts §6, only row-level
  `GET /api/executions`); ExecutionLog's SLA Breach Tracker (no equivalent
  endpoint — closest is `GET /api/stale?kind=hk`, a different page with
  different semantics, not in this wave); CostReport's Budget Alert and ROI
  Estimate sections (Budget depends on the Settings page, a later wave;
  ROI needs a second ad-hoc 30d query the endpoint doesn't expose
  independently of the selected period); DryRunViewer's "Promote to Live"
  action and "Domain Dry Run" bulk tab (no POST endpoint for either in
  contracts §6, and adding one wasn't authorized for this wave the way the
  domains router was).
- `ui/src/routes.tsx`: 6 routes switched from `PlaceholderPage` to their
  real pages (`/domains`, `/activity`, `/executions`, `/costs`, `/dryrun`,
  `/audit`); Health Dashboard's route was already live. 5 routes remain
  `PlaceholderPage` for Wave 2 (Table Registration, Policy Configuration,
  Non-Prod Lifecycle, Stale Resources, Settings).
- **Parity checklists** (Streamlit twin feature → ported / not ported):
  - *Health Dashboard governance section* vs `4_Health_Dashboard.py`'s
    Maintenance Governance block: ✅ 5 KPI stats, ✅ domain filter,
    ✅ dual-optimizer grid (compaction/retention/orphan flags, checked-at,
    override-until), ✅ rescan button + flash message, ✅ CSV export,
    ✅ integrity-failures grid (7d).
  - *Live Activity* vs `5_Live_Activity.py`: ✅ auto-refresh (improved: real
    10s polling vs blocking sleep toggle), ✅ manual refresh button,
    ✅ currently-running grid (row-level, not grouped — see Not Ported),
    ✅ recent-operations grid + engine/status filters, ✅ active-locks strip
    with force-release (new vs Streamlit, which had no lock concept at all
    — Workstream A postdates this page's Streamlit version). ❌ Today's
    Engine Summary aggregate (see Not Ported).
  - *Execution Log* vs `7_Execution_Log.py`: ✅ domain/engine/status/
    time-range filters (range via `RangePicker` instead of a day-count
    Select — finer-grained, strictly more capable), ✅ CSV export,
    ✅ drill-in detail (via row expand + `GET /api/executions/{id}` instead
    of a paste-an-ID box — same data, better UX). ❌ SLA Breach Tracker (see
    Not Ported), ❌ hide-DRY_RUN-by-default checkbox (status filter covers
    the same need — select `!= DRY_RUN` manually; minor UX gap, not a
    missing capability).
  - *Cost Report* vs `8_Cost_Report.py`: ✅ live-billing-vs-estimate banner,
    ✅ domain-independent group_by + period selectors (Streamlit had domain
    filter + fixed group; this wave's endpoint groups by domain/layer/tier
    instead, a cleaner axis choice already built into contracts §6),
    ✅ top-level KPIs, ✅ cost-by-group table. ❌ charts (deliberately
    skipped, see above), ❌ archival-by-domain pie / monthly trend chart
    (would need the skipped charting library), ❌ Budget Alert, ❌ ROI
    Estimate (see Not Ported).
  - *Audit Log* vs `12_Audit_Log.py`: ✅ time-range/action/actor filters,
    ✅ status filter (client-side, matching Streamlit's own approach),
    ✅ summary KPIs, ✅ before/after detail view (row expand instead of a
    select-by-audit_id box), ✅ CSV export. ❌ Domain / Target ID free-text
    filters (no backend params for them, see Not Ported).
  - *Dry Run Viewer* vs `6_Dry_Run_Viewer.py`: ✅ table search/select,
    ✅ domain/layer/tier summary, ✅ window evaluation, ✅ gate summary
    (all 3 gates + upstream job + window decision, field-for-field),
    ✅ conditional compaction SQL preview, ✅ copy-SQL action. ❌ Promote to
    Live, ❌ Domain Dry Run bulk tab (see Not Ported).
  - *Domain Management* vs `1_Domain_Management.py`: ✅ list with table
    counts + all display columns, ✅ register form (all fields, defaults
    matching Streamlit's), ✅ edit form (all fields incl. is_active/
    digest_enabled/digest_email), ✅ escalation/notification routing
    preview (reframed as a read-only summary rather than the Streamlit
    twin's interactive digest-preview button, since `build_digest()` has no
    API endpoint in this wave). ❌ live Weekly Digest Preview button (no
    endpoint; the static summary above covers the "where would this route"
    question without fabricating a live digest call).
- Verified live in dev mode (Playwright against a `uvicorn` instance
  serving the built `ui/dist` + the seeded local DB, screenshots reviewed
  then discarded — not committed): Domain Management (5 seeded domains,
  table counts correct), Health Dashboard's Governance section (shows
  exactly 1 conflicted table — `fin_payment_master`, `aws_opt_compaction`
  — confirming the ≥1-conflict acceptance bar), Live Activity (1024
  seeded executions, 10s `refetchInterval` confirmed in source and via
  network-idle re-fetch), Execution Log (filters + expand render real
  rows), Cost Report ($104.4 estimated cost, 4-domain breakdown), Audit
  Log (36 events, 3 statuses, expand shows before/after), Dry Run Viewer
  end-to-end (searched `fin_payment_master`, selected it, got a real gate
  summary — Gate 1 disabled, Gates 2/3 enabled with EXECUTE decision,
  SQL preview correctly absent since the table's strategy is `zorder` not
  `binpack`). Zero console errors across all 7 pages.
- No Streamlit file modified this phase.

2026-07-06 Phase 5a: Wave 2a — Table Registration (5 tabs) + Policy
Configuration (4 tabs) shipped, plus 3 new shared components
(`ControlMFields`, `GatesEditor`, `WindowBlackoutEditor` in
`ui/src/components/`). 642 tests passing (562 unit + 80 api, up from 69 —
10 new: register-returns-template, `database_name` filter,
bulk-controlm engine-flags + `exclude_fqns`, create/delete-template ×5,
bulk-apply `skip_overridden`), ruff clean, `tsc --noEmit` clean,
`npm run build` clean, `npm run lint` (oxlint) clean. Zero engine logic
touched.

- **Backend was ~90% already built in Phase 2** (`api/services/tables_svc.py`,
  `policies_svc.py`, `gates_svc.py`, `controlm_svc.py` already lifted almost
  every query/mutation this wave needed) — this phase's backend work was
  closing real gaps found while wiring the UI to it, not building from
  scratch:
  - `tables_svc.register_table()` now also infers + applies a policy
    template in the same call (`infer_template()` + `apply_template()`,
    matching 2_Table_Registration.py's Browse & Register tab exactly) and
    returns `{"success", "template"}` so the UI can show which template got
    applied; router response gained a `template` field (additive).
  - `tables_svc.list_tables()` gained an optional `database_name` filter
    (needed for Bulk Control-M's preview grid); `GET /api/tables` router
    param added to match.
  - `tables_svc.bulk_controlm()`'s `_BULK_SETTABLE` gained
    `hk_enabled`/`archive_enabled`/`lifecycle_enabled` (Engine Flags' Bulk
    Apply sub-tab reuses this one generic filter+set endpoint instead of a
    second bulk endpoint) — **found and fixed a bool-handling bug in the
    same function while extending it**: `isinstance(value, int) and not
    isinstance(value, bool)` fell through to the string-quoting branch for
    real booleans, which would have written `hk_enabled = 'True'` instead
    of `1`; reordered to check `isinstance(value, bool)` first.
    `_build_bulk_where()` also gained `exclude_fqns` support (Manual Bulk
    Apply's per-row exclude, now a native AntD `rowSelection` deselect
    instead of Streamlit's session-state multiselect).
  - `policies_svc.apply_template_bulk()` **found a real behavior gap, not
    just a UI polish item**: it didn't check `manually_overridden` at all,
    while the Streamlit twin's "Override existing manual overrides"
    checkbox (default unchecked) skips manually-overridden tables. Added
    `skip_overridden: bool = True` (default matches Streamlit's default),
    requiring a `LEFT JOIN hk_config` the function didn't have before.
    `TemplateApplyRequest` gained the matching field.
  - `> ADDED (Phase 5a)` in contracts.md §6 (same precedent as Phase 4's
    domains router): `POST /api/templates` and `DELETE /api/templates/{name}`
    — the Streamlit "Add Template"/"Delete Template" sub-tabs had no contract
    endpoint at all. `policies_svc.create_template()` (name-uppercase,
    duplicate-name rejected) and `delete_template()` (built-in-template set
    hardcoded same as Streamlit's `_BUILTIN`, usage-count guard via a new
    `count_template_usage()`) — both additive, same envelope/dry_run/audit
    conventions as every other route. Contract-smoke route count bumped
    48→50.
  - `system_svc.system_mode()` gained `gate0_override_max_hours` (from
    `config/settings.py::GATE0_OVERRIDE_MAX_HOURS`) so the Gate 0 override
    DatePicker can disable dates beyond the cap client-side instead of
    round-tripping a 400 — additive field on an already-locked route, same
    pattern as `health_kpis()`'s repeated extensions in Phase 3.
- **Shared components** (`ui/src/components/`, used by both pages per the
  phase brief): `ControlMFields.tsx` (6-field block — job name AutoComplete
  fed by `GET /api/jobs?search`, HK job, Gate 1 job, job type, TimePicker,
  duration; identical field names across Register/Edit/Bulk so no prefix
  plumbing needed), `GatesEditor.tsx` (Gate 1/2/3 switches +
  optional Gate 0 override control — DatePicker capped at
  `now + gate0_override_max_hours`, reason required, status Alert;
  `showOverride={false}` hides the override section for Templates, which
  carry no per-table override state), `WindowBlackoutEditor.tsx` (window
  type/timing + 7-preset/24-checkbox blackout grid, fully controlled,
  reacts instantly — no Streamlit rerun/session-state gymnastics).
- **Table Registration** (`ui/src/pages/TableRegistration/`, 5 tabs, all
  present in the app though the phase brief's prose only named 4 — Engine
  Flags was ported anyway per "skip no tab/sub-tab"): Browse & Register
  (Glue database Select incl. "All Databases" fanned out via TanStack
  Query's `useQueries` in parallel; native AntD `rowSelection` replaces
  Streamlit's Select-All/Clear-All session-state dance; register result
  shows the *actual* applied template from the API response rather than
  re-implementing `infer_template()`'s layer/tier mapping table in
  TypeScript, which would risk silent drift), Registered Tables (full
  filters + CSV export; gates deliberately not duplicated here — they
  already have a home in Policy Configuration's View Configs, and
  `GET /api/tables` is stream_registry-only, no hk_config join), Edit Table,
  Engine Flags (Single Table + Bulk Apply sub-tabs), Bulk Control-M (Manual
  Apply w/ preview+exclude, Import Mapping w/ dry-run-then-real-apply,
  Export Template w/ client-side CSV-string preview, Control-M Job
  Registry).
- **Policy Configuration** (`ui/src/pages/PolicyConfig/`, 4 tabs): View
  Configs, Edit Single Table (Gates card + Window/Blackout card + the same
  compaction/snapshot validations as Streamlit — reason required, numeric
  floors, sort/zorder-requires-glue cross-check, scheduled-window
  start-time-not-in-blackout check — replicated client-side, submits both
  `PUT /api/policies/{fqn}` and `PUT /api/gates/{fqn}`), Bulk Apply
  Template (domain+layer required, template preview stats,
  `skip_overridden` wired to the checkbox), Templates (View All / Edit
  [shared `GatesEditor`+`WindowBlackoutEditor`] / Add / Delete — Delete has
  no dedicated usage-count endpoint, so it preflights with a
  `DELETE ?dry_run=true` call, which already runs the same
  built-in/usage-count checks server-side without writing anything, and
  surfaces that message before the real confirm+delete).
- **Two real bugs found and fixed during this phase's own live
  verification** (Playwright against a fresh `uvicorn` + the built
  `ui/dist`, not just screenshots — a stale `uvicorn --reload` process from
  an earlier session was still serving pre-Phase-5a code and had to be
  killed/restarted first, see `context_hints.md`):
  1. `BrowseRegisterTab`'s register form never called `form.resetFields()`
     after a successful submit — since the form stays mounted across
     tab/selection changes (unlike Streamlit, which resets every widget on
     each rerun), Domain/Layer/Tier silently carried over into the *next*,
     unrelated batch of selected tables, including passing required-field
     validation it shouldn't have. Fixed by resetting the form after
     `setSelectedFqns([])` on success; same fix applied to
     `EngineFlagsTab`'s and `PolicyConfig/EditTableTab`'s per-table search
     pickers, which had the identical risk (a background refetch of
     `detail.data` mid-edit would re-run the `useEffect` that syncs local
     edit state from server data, silently discarding an in-progress Gate
     toggle right before Save) — all three now key their sync effect on
     `fqn` via a `useRef` guard, not on `detail.data`'s object identity.
  2. Three places used `useTablesList({ size: 500 })` / `{ size: 5000 })`
     to feed a table-search dropdown — `api/deps.py::PageParams` caps
     `size` at 250, so these 422'd outright rather than just being
     inefficient at scale. Fixed properly, not by lowering the number:
     `TableRegistration/EditTableTab` and `EngineFlagsTab`'s single-table
     picker now use the existing `useTablesSearch()` search-as-you-type
     hook (same pattern DryRunViewer already uses) instead of loading the
     whole fleet into one dropdown — the real fleet runs ~30k tables, so
     "load everything" was never going to work regardless of the cap.
     `ManualApply`'s preview grid kept `useTablesList` (it's genuinely
     paged/filtered) but capped at 250.
- Verified live end-to-end against the seeded local DB (`uvicorn` serving
  the production `ui/dist` build): registered `fin_claims_stg` via Browse &
  Register → template `STAGING_DEFAULT` auto-applied → confirmed via
  `GET /api/tables/{fqn}` (domain/layer/tier persisted) and the Registered
  Tables grid; submitting Register with no Domain selected blocked client-side
  ("Domain is required"); Policy Config Edit Table — setting a Gate 0
  override with no reason blocked ("A reason is required to set a Gate 0
  override"), then toggling Gate 2 off + setting a valid 2h override with a
  reason saved successfully (two audit IDs, one per PUT), View Configs
  reflected Gate 2 ❌ and `GET /api/gates/{fqn}` showed the override
  timestamp/reason; Bulk Control-M's Import Job Mapping — uploaded a 1-row
  CSV (`domain=finance,controlm_job_name=ACE-VERIFY-JOB-PRD`), applied it,
  confirmed via `GET /api/tables/{fqn}` that `controlm_pipeline_job` updated;
  Templates — added a throwaway custom template, toggled its Gate 1 on,
  saved, bulk-applied it to `finance/staging` — the apply correctly reported
  only 1 table affected (not 2), because `fin_claims_stg` was already
  `manually_overridden=1` from the Edit Table step above and
  `skip_overridden` (default true) correctly protected it, applying instead
  to the other `finance/staging` table — a live confirmation that the new
  `skip_overridden` fix actually works, not just that its unit test passes.
  Cleaned up the throwaway template and reset the affected table back to
  `STAGING_DEFAULT` afterward so `config/policy_templates.json` (a tracked
  repo file) isn't left mutated.
- No Streamlit file modified this phase.

2026-07-07 Ad-hoc UX pass: Policy Configuration's Edit Single Table
restructured, `stream_id` concept removed fleet-wide (org decision — not
adopting the stream-grouping model), and three Bulk Control-M sub-tabs
improved, all per Sujith's live review. 562 unit + 80 api tests still
passing (no test count change — this pass touched fixtures, not coverage),
ruff/tsc/build/lint all clean.
- **Policy Configuration → Edit Single Table**: the three always-open cards
  (Gates/Window/Compaction) replaced with a single-open `Collapse` — each
  collapsed section shows a status `Tag` (e.g. `2/3 active`, `post_batch ·
  8h blocked`, `zorder`) so context isn't lost when collapsed. The required
  override-reason field + Save button moved out of the bottom of the
  Compaction form into a `position: sticky; bottom: 0` footer, so they're
  visible at all times regardless of scroll position or which section is
  open. Verified live via Playwright: accordion enforces exactly one open
  panel, footer stays pinned to the viewport bottom after scrolling.
- **`stream_id` removed from the entire stack** (Sujith: org isn't adopting
  the pipeline-stream-grouping model). Removed cleanly from every layer,
  not just the UI:
  - `engine/core/registry.py`: `register_table()`'s `stream_id` param,
    `_generate_stream_id()`, and `get_tables_by_stream()` (no callers)
    deleted outright; `import uuid` removed (its only remaining use).
  - `engine/core/execution_log.py` / `execution_log_parquet.py`: `LogEntry.
    stream_id` field removed. `execution_log.write()`'s positional
    `INSERT ... VALUES (...)` (no column list — position is the only
    contract with the Athena table) required removing `stream_id` from
    `sql/create_execution_log.sql` at the exact same position, not just
    deleting the Python field — same care the two-writer-path rule in
    `context_hints.md` already calls out for *adding* columns applies
    symmetrically to *removing* one.
  - `engine/core/orchestrator.py`, `engine/engines/hk_engine.py`,
    `engine/engines/archival_engine.py`: dropped the `stream_id=table_row.
    get("stream_id")` kwarg from every `LogEntry(...)` construction site.
  - `api/models.py` (`RegisterTableRequest`/`UpdateTableRequest`),
    `api/services/tables_svc.py` (`_UPDATABLE_FIELDS`/`_BULK_SETTABLE`):
    field/set removed.
  - `scripts/seed_local_db.py`: `stream_registry`/`execution_log` local
    DDL columns removed; the 17-row seed tuple literals (`fin_aps`,
    `fin_claims`, `ers_bkg`, `mbr`, `clm`, `special` — each carried a
    `"STR-..."` positional element) stripped via a scoped regex pass
    rather than 17 manual edits, then the unpacking loop and 3 downstream
    `execution_log`/`archival` row-dict builders updated to match.
    `scripts/seed_scale_test.py` similarly (its `stream_id`/`seq` locals
    were unused for anything else once removed — deleted, not stubbed).
  - `app/pages/2_Table_Registration.py` (Streamlit legacy — still updated
    for consistency even though React has superseded this page): register
    form field, Registered Tables SELECT + column order + rename map, Edit
    Table's `e_stream` widget + its UPDATE SQL clause, and Bulk Control-M's
    `_bulk_stream` field + SET clause all removed.
  - `sql/create_stream_registry.sql`: DDL column removed (this one uses a
    named `INSERT (...) VALUES (...)` in `registry.py`, so — unlike
    `execution_log` — position here was never load-bearing).
  - `tests/unit/test_orchestrator.py`: `TABLE_ROW` fixture's `stream_id`
    key removed.
  - Reseeded `zamboni_local.db` end-to-end (`python scripts/
    seed_local_db.py --reset`) to confirm the edited seed script actually
    runs clean, not just imports clean — required stopping the running
    `uvicorn` first (Windows SQLite file lock on `reset_db()`'s
    `os.unlink`, per `context_hints.md`), then restarting it afterward.
    Full repo-wide grep for `stream_id`/`streamId`/`Stream ID` confirmed
    zero remaining references before calling this done.
  - **Found, not fixed** (pre-existing, unrelated): reseeding surfaced a
    `home_snapshot has no column named environment` insert failure —
    `home_snapshot`'s DDL never had an `environment` column and
    `seed_home_snapshot()` wasn't touched by this pass; out of scope as a
    drive-by fix.
- **Bulk Control-M — three sub-tabs improved** (Sujith's live feedback:
  "some over details there"):
  - **Manual Bulk Apply**: the flat 12-field block (job config + target
    filters, no visual grouping) split into two `Card`s, "1. Job Details"
    and "2. Target Tables" — reads as two sequential decisions instead of
    one wall of inputs. (`stream_id`'s removal above also dropped it from
    two fields to one in the Job Details card.)
  - **Export Mapping Template**: the always-rendered 10-row "CSV column
    guide" reference table moved into a `Collapse`, collapsed by default —
    it's opt-in reference material, not something needed on every visit.
  - **Control-M Job Registry**: was a static, unfiltered grid glued above
    the Add/Upload forms with no search and no way to remove a stale entry
    (`DELETE /api/jobs/{name}` already existed server-side, unused).
    Restructured into 3 sub-tabs — new **Job List** (server-side search via
    the existing `GET /api/jobs?search=` param, client-side domain/job-type
    filters, CSV export, and a Remove action wired to the existing delete
    hook with a confirm modal) alongside the existing Add Single Job / Bulk
    Upload CSV tabs.
  - Verified live via Playwright: Manual Bulk Apply shows both sections
    with the Stream ID field gone; Export Mapping's guide table is absent
    until the Collapse header is clicked; Job Registry's new Job List tab
    shows an added job immediately (real audit ID in the success toast),
    search-filters it down to 1 row, and Remove deletes it for real
    (confirmed gone from the list after the confirm-modal click).
- No engine *logic* changed (vacuum.py, gates, orchestration sequencing
  all untouched) — this pass is schema/field removal plus UI layout only.

2026-07-07 Bug fix: Bulk Control-M job-mapping CSV round-trip was
producing "0 table(s) will be updated" for every row when re-uploading an
unmodified Export Mapping Template — reported by Sujith after driving the
real flow. Root cause found via direct reproduction (export then
re-import the same CSV in a Python REPL), not guesswork. 562 unit + 81 api
tests passing (+1 regression test), ruff/tsc/build/lint clean.
- **Root cause**: `table_pattern` is always present in the exported CSV
  but blank on every row. A column that's blank on *every* row reads back
  via `pandas.read_csv` as all-NaN `float64`, not `object` dtype — but
  both `tables_svc.py::import_job_mapping` and `controlm_svc.py::
  import_jobs` (and their Streamlit-legacy twins in `2_Table_Registration.
  py`, `bc_tab_import`/`bc_tab_jobs`) only ran their NaN→`""` string
  cleanup over `df.select_dtypes(include="object").columns` — silently
  skipping any all-blank column. The raw `NaN` then stringified as the
  literal text `"nan"`, which got used as a `table_fqn LIKE '%.nan%'`
  filter that matches no real table — every row's `tables_matched` came
  back `0`. The one existing test for this path
  (`test_job_mapping_import_round_trip`) never caught it because it only
  ever sent `domain,controlm_job_name` with `table_pattern` *absent*
  entirely, which takes the separate "fill missing column with a default"
  branch and never NaN-round-trips through pandas at all — an absent
  column and an all-blank column are not the same bug surface.
  Fixed all four spots with one `df = df.fillna("")` immediately after the
  header lowercase-rename, before the per-column string cleanup, so blank
  cells are `""` regardless of the column's inferred dtype.
- Added `tests/api/test_tables.py::
  test_job_mapping_import_blank_table_pattern_column_still_matches` — a
  present-but-blank `table_pattern` column against a real seeded
  domain/layer/database combo, asserting `tables_matched > 0`. This is the
  regression guard the original test's "column absent" shape couldn't
  provide.
- **Second, independent issue from the same report**: the exported CSV's
  column order put `job_type` immediately after `controlm_job_name` (and
  the column guide table showed them adjacent too), which reads as "the
  type of controlm_job_name" — but `job_type`/`dependent_job_type` is
  actually the AWS service type for the *Gate 1* completion check
  (`aws_gate1_job`/`dependent_on_controlm_job`), unrelated to
  `controlm_job_name`'s own type. Fixed by reordering `job_type` to sit
  immediately after `aws_gate1_job` in `export_job_mapping()`'s SELECT
  (and the Streamlit twin's matching SQL + `_EXPECTED_COLS` + uploader
  help text) — a pure column-order change, safe because both import paths
  are column-name-driven, not positional. Also reworded the CSV column
  guide's `job_type` row notes to say explicitly which field it describes,
  and relabeled Import Job Mapping's preview grid column from generic
  "Type" to "Gate 1 Job Type" (that grid has no adjacent `aws_gate1_job`
  column to visually anchor next to, unlike the export guide/preview,
  so the ambiguity had to be resolved by wording instead of position).
- Verified live end-to-end via Playwright against the real flow that
  triggered the report: downloaded the actual template through
  `GET /api/tables/job-mapping/export`, re-uploaded it unmodified through
  the Import Job Mapping tab, confirmed the header order
  (`...,aws_gate1_job,job_type,job_start_time,...`) and the preview
  banner reading "39 table(s) will be updated across all rows" instead of
  0.
- No changes to engine core, orchestrator, or gate logic — this is a CSV
  parsing bug fix plus a labeling/ordering clarification only.

2026-07-08 Control-M Integration page: split out of Table Registration into
its own top-level route, plus a full revamp per Sujith's live UX review.
562 unit + 83 api tests passing (+2 regression tests), ruff/tsc/build/lint
all clean, verified live via Playwright end-to-end (register a job through
Manual Bulk Apply → confirm it shows up in the Job Registry → confirm the
same via the CSV Workflow's re-exported template).
- **New route**: `/controlm` ("Control-M Integration", Registry category,
  `LinkSimple` icon) — `ui/src/pages/ControlMIntegration/`. Table
  Registration's 5th tab (Bulk Control-M) is gone; its subtitle no longer
  claims to "manage Control-M integration." The old `TableRegistration/
  components/BulkControlMTab/` directory is deleted, not deprecated in
  place — its 4 files moved and were substantially rewritten, not just
  relocated.
- **Page structure, 3 tabs in the order Sujith asked for**: Control-M Job
  Registry (first — it's reference data, "what jobs exist"), Manual Bulk
  Apply (second), CSV Workflow (third, new — merges the old Export Mapping
  Template + Import Job Mapping tabs into one guided "Step 1 — Download
  Template" / "Step 2 — Upload Completed Mapping" flow, since they were
  always two alternatives for the same goal, not two unrelated features).
- **Real bug fixed**: jobs applied via Manual Bulk Apply or Import Job
  Mapping only ever wrote `controlm_pipeline_job`/`controlm_hk_job` onto
  matched `stream_registry` rows — neither flow ever touched
  `controlm_jobs`, so a job assigned through either path silently never
  appeared in the Control-M Job Registry grid, no matter how many tables
  referenced it. Fixed with `controlm_svc.register_job_if_missing()`
  (`INSERT OR IGNORE`, not `upsert_job()`'s `INSERT OR REPLACE` — a
  bulk-apply side effect must never clobber a curated registry entry's
  description/start-time/frequency), called from `tables_svc.py`'s
  `bulk_controlm()` and `import_job_mapping()` whenever a real (non
  dry-run) apply actually matches ≥1 table. Two new regression tests
  (`test_bulk_controlm_registers_job_in_registry`,
  `test_job_mapping_import_real_apply_registers_job_in_registry`) exercise
  both paths against `GET /api/jobs`. `useBulkControlM`/
  `useImportJobMapping` also now invalidate the `['jobs']` query key so
  the UI reflects it without a manual refresh.
- **`job_frequency` added** (Sujith: "Job freq can also be captured") —
  new `controlm_jobs.job_frequency` column (schema + idempotent ALTER
  migration in `seed_local_db.py`, `JobUpsertRequest.job_frequency` in
  `api/models.py`, threaded through `list_jobs`/`upsert_job`/
  `import_jobs`/`register_job_if_missing` in `controlm_svc.py`). Surfaced
  in Add Single Job's form, the Job List grid's new Frequency column, and
  Manual Bulk Apply's Job Details card — the latter is passed through
  `set_fields.job_frequency` but deliberately never reaches the
  `stream_registry` UPDATE (it's not a column there); `bulk_controlm()`
  pulls it back out solely for the job-registration side effect above.
- **Manual Bulk Apply reordered and reshaped** (Sujith: "Target Tables can
  be first, preview before entering the control-m detail, which is
  better?" → yes): "1. Target Tables" now comes before "2. Job Details".
  "Preview Matching Tables" opens a `Modal` (Sujith: "preview can be a
  popup also") with the row-selection grid and a "Confirm N Table(s)"
  button; confirming closes the modal and replaces it with a compact
  summary `Alert` ("✅ N selected · ⛔ M excluded" + a "Change selection"
  button that reopens the modal). Apply stays disabled until a selection
  is confirmed. Control-M Job Name is now an AntD `AutoComplete` (not a
  plain `Input`) sourced from the job registry — search/select an existing
  job, or type an entirely new name, which is exactly what
  `register_job_if_missing()` above then picks up. CI Number moved behind
  a "+ More options" toggle (Sujith asked whether the section felt
  cluttered — at 7-8 fields grouped flat, yes; tucking the one
  genuinely-skippable field away was the fix, not restructuring further).
- **Job List search → suggestion dropdown** (Sujith: "search by can be a
  suggestion drop down") — the plain `Input.Search` became an
  `AutoComplete` fed by the already-fetched job list (client-side, no
  extra round trip — the registry is a few dozen rows, same reasoning as
  the existing client-side domain/type filters).
- **CSV Workflow's stale-preview bug fixed** (Sujith: "already imported
  details are still there even after saving and navigating away... clear
  when coming back") — `runImport`'s success handler now clears
  `report`/`file` state immediately whenever the apply was real
  (`!dryRun`), not just on unmount. This also incidentally fixes the
  navigate-away-and-back case, since AntD Tabs keeps inactive panes
  mounted (documented gotcha) — there was nothing stale left to persist
  once the state resets right at save time instead of relying on
  navigation to reset it.
- **Focused-input styling** (Sujith: "text boxes, when focused, change the
  colour") — added a global rule in `ui/src/index.css` strengthening
  AntD's default focus ring (border + soft glow, `colorPrimary` #167D9A)
  across `Input`/`Select`/`DatePicker`/`InputNumber` app-wide, not just on
  this page.
- **Playwright gotcha hit again**: AntD's `AutoComplete`/`Select` render
  their placeholder as a sibling `<span class="ant-select-selection-
  placeholder">`, not a native `<input placeholder="...">` attribute —
  `input[placeholder*="..."]` selectors silently match zero elements
  against these components (plain `Input`/`Input.Search` are unaffected).
  Verification scripts now locate these fields via their label text
  (`.ant-col:has-text('Control-M Job Name')`) and
  `.ant-select-selection-search-input`, not by placeholder.
- No changes to engine core, orchestrator, gate logic, or vacuum.py.

2026-07-08 Control-M Integration follow-up fixes, per Sujith's live testing
of the page shipped above. 562 unit + 84 api tests passing (+1 regression
test), ruff/tsc/build/lint clean, live-verified via Playwright (including
against a throwaway job created through the real API to get a
deterministic non-default start-time/duration/frequency to autofill from).
- **Job List: "Tables Mapped" column** — `controlm_svc.list_jobs()` gained
  a correlated-subquery count against `stream_registry` (`controlm_pipeline_job
  = j.job_name OR controlm_hk_job = j.job_name OR dependent_on_controlm_job
  = j.job_name`), surfaced as a new sortable column. Answers Sujith's "is
  that expected?" about removed jobs' names lingering on tables: yes —
  `controlm_jobs` is a catalog, not a foreign key, so `DELETE`ing a
  registry entry was never going to cascade into `stream_registry`. Made
  the consequence visible instead of just accepting it silently: the
  Remove confirm dialog now reads the row's `tables_mapped` count and, if
  >0, warns explicitly that those tables will keep showing the job name
  until edited individually.
- **Manual Bulk Apply, three real fixes**:
  1. Selecting an *existing* job from the Control-M Job Name AutoComplete
     now autofills Job run start time / Expected duration / Job Frequency
     from that job's own registry record (`onSelect`, not `onChange` --
     deliberately not firing on every keystroke while typing a brand-new
     name, only on an actual pick).
  2. Apply's `onSuccess` no longer resets `confirmed`/`selectedFqns`/
     `confirmedRows` — it used to, which flashed the "Select target tables
     above first" warning tag immediately after a successful apply
     (reads like something broke). Only the Job Details fields reset now;
     the confirmed table selection is still valid and stays shown.
  3. Spacing tightened complaint ("More Option and Apply button close
     together") — wrapped the toggle + CI Number block in its own
     `marginBottom: 24` container instead of the previous
     conditionally-zero margin that collapsed to nothing when collapsed.
- **CSV Workflow**: "Step 1 — Download Template" is now a `Collapse`
  (open by default, matching prior behavior, but collapsible) instead of
  a plain `Card` — repeat visitors who already know the format can
  collapse it out of the way.
- No changes to engine core, orchestrator, gate logic, or vacuum.py.

2026-07-08 Control-M Integration, second follow-up: Job List gained Edit +
a drill-in "Tables Mapped" popup, plus a sample CSV download for Bulk
Upload. 562 unit + 86 api tests passing (+2 regression tests),
ruff/tsc/build/lint clean, live-verified via Playwright.
- **New route** `GET /api/jobs/{name}/tables` (`> ADDED` note in
  contracts.md, same precedent as prior additions; contract-smoke route
  count 50→51) — `controlm_svc.get_mapped_tables()` returns every table
  referencing the job in any of its three role columns
  (`controlm_pipeline_job`/`controlm_hk_job`/`dependent_on_controlm_job`).
- **Job List "Tables Mapped" is now a link** (only when count > 0) opening
  a `MappedTablesModal` — table/domain/layer plus a `Role` column
  (Pipeline/HK/Gate 1 tags, since a table can reference the same job in
  more than one role) sourced from the new endpoint.
- **Job List gained an Edit action** — `EditJobModal` pre-fills from the
  row and reuses the existing `useUpsertJob()` mutation (`INSERT OR
  REPLACE` keyed on `job_name`). Job Name itself is deliberately not
  editable in this modal: renaming would silently create a second
  registry row and orphan the original rather than rename anything, since
  upsert's uniqueness key *is* job_name.
- **Bulk Upload CSV gained a "Download Sample CSV" button** — a static
  2-row example (`SAMPLE_CSV` const) covering every documented column
  (`job_name, job_type, domain, description, expected_start_time,
  expected_duration_min, job_frequency`), via the same `downloadRawCsv()`
  helper Export Mapping Template already uses.
- Two new regression tests: `test_get_job_mapped_tables` (real seeded
  mapping resolves), `test_get_job_mapped_tables_empty_for_unknown_job`.
- No changes to engine core, orchestrator, gate logic, or vacuum.py.

2026-07-08 Control-M Integration, third follow-up: Bulk Upload CSV gained
a dry-run preview + row-selection step before saving (Sujith: "when
uploading I need to show the list in a table and ask for saving, now its
saving without any prompt... rows selectable... too much?" — agreed it
wasn't, since Import Job Mapping already has this exact pattern and
`upsert_job` is a full overwrite with no undo). Deliberately no inline
cell editing, only select/exclude -- same scope call as Manual Bulk
Apply's preview modal. 562 unit + 88 api tests passing (+2 regression
tests), ruff/tsc/build/lint clean, live-verified end-to-end (confirmed
zero jobs registered between upload and Save, confirmed a deselected row
is genuinely excluded from what gets written).
- `controlm_svc.import_jobs()` gained `dry_run` and `exclude_job_names`
  params -- dry_run parses/validates/defaults exactly like a real import
  but returns the parsed rows instead of writing; exclude_job_names drops
  specific rows before either the preview or the real apply. Same
  two-phase shape as `tables_svc.py::import_job_mapping`, which already
  had this pattern -- Bulk Upload CSV was the one CSV import in the app
  that skipped it.
- `POST /api/jobs/import` gained `dry_run` and `exclude` query params
  (comma-separated job names -- the endpoint is `multipart/form-data`, so
  a JSON body alongside the file isn't an option). No audit event is
  written for dry-run calls (nothing happened yet); the real apply's
  audit event is unchanged.
- `BulkUploadJobs` (`ui/src/pages/ControlMIntegration/components/
  JobRegistry.tsx`): upload now triggers a dry-run call, rendering the
  parsed rows in a `<Table>` with `rowSelection` (all rows selected by
  default) and an inline "N will be saved · M excluded" tag, matching
  Manual Bulk Apply's preview-modal wording. "Save N Job(s)" re-submits
  the same `File` with `dryRun: false` and the deselected rows'
  `job_name`s as `excludeJobNames` -- no file re-upload or client-side
  CSV re-parsing needed, the already-open file object is reused.
- No changes to engine core, orchestrator, gate logic, or vacuum.py.

2026-07-08 Manual Bulk Apply, fourth follow-up: the "✅ All N table(s)
selected" summary was found lingering after navigating away and back
(Sujith's report). Root cause: the *previous* follow-up deliberately
stopped resetting `confirmed`/`confirmedRows`/`selectedFqns` after a
successful apply (to stop a different complaint — the "select tables
first" hint flashing right after success). That traded one bug for
another: since AntD Tabs keeps inactive panes mounted, switching to
another Control-M Integration tab and back never remounted this
component, so the stale confirmed-selection summary survived
indefinitely, not just across a single render.
- **Fix**: `handleApply`'s `onSuccess` now fully resets Target Tables
  (domain/layer/database/pattern + confirmed/confirmedRows/selectedFqns)
  in addition to Job Details -- a completed apply starts the next one
  from a clean slate, so there's nothing stale left to survive a tab
  switch or navigation. To avoid reintroducing the earlier complaint,
  the "select tables first" hint also changed from a gold warning `Tag`
  to plain muted helper text ("Select target tables above to enable
  Apply.") — neutral guidance instead of an error state, so showing it
  again immediately after a successful save (which now happens, by
  design) doesn't read as something broke.
- Live-verified via Playwright specifically reproducing the reported
  repro: apply → confirm chip is gone immediately → switch to Control-M
  Job Registry tab and back to Manual Bulk Apply → chip still gone (this
  is the scenario a plain "did the bug reproduce once" check would have
  missed, since without the tab-switch step the reset alone looks
  sufficient).
- No changes to engine core, orchestrator, gate logic, or vacuum.py.

2026-07-08 Home page modal width + fleet-wide pagination size-changer audit.
Sujith reported the Home page's "Executions — Today" and "Failures — Last 7
Days" popups needed a horizontal scrollbar workaround, plus a broader
complaint that page-size selection ("15/page" etc.) "is not working... only
the select 50/page only possible" across the app. TypeScript/build/lint all
clean; frontend-only, no backend files touched.
- **Root cause of the pagination complaint**: `components/DataGrid.tsx` is
  the single shared pagination control for every grid in the app (confirmed
  via a repo-wide grep for `showSizeChanger`/`pageSizeOptions` — DataGrid is
  the only place either appears), but several call sites hardcoded
  `page`/`size` into the query params with no `onPageChange` wired back
  (or wired only `page`, not `size`) — same class of controlled-component
  bug as the Manual Bulk Apply entries above, just on read-only grids
  instead of a form. AntD's `<Table pagination={{...}}>` is fully
  controlled: since the parent's `size` prop never changed, selecting
  "15 / page" visually flashed then reverted to whatever was hardcoded
  (50 in most of these — hence "only 50/page possible"). Audited every
  `<DataGrid>` usage in the app (12 files); found broken: Home's Recent
  Activity grid + both KPI-card drill-down modals (Executions Today,
  Failures 7d — `useHealth.ts`'s `useRecentExecutions`/`useExecutionsToday`/
  `useFailures7d` took a `size` param but no `page`, and callers passed no
  `onPageChange` at all), Live Activity's Currently Running + Recent
  Operations grids (`hooks.ts` hardcoded `page: 1, size: 50/100` inline,
  no state), Health Dashboard's Governance section (the conflicts grid
  wired `onPageChange={setPage}` — page only, size still fixed at 25 — and
  the integrity-failures grid had no wiring at all, fixed size 50).
  Execution Log, Audit Log, Table Registration's Registered Tables tab, and
  Policy Configuration's View Configs tab were already correct (state
  lives in the page's `index.tsx`, `onPageChange={(p,s)=>{setPage(p);
  setSize(s);}}`, same pattern `ExecutionLog/index.tsx` established) — used
  as the template for every fix here.
- **Fixed** by giving each broken grid real page+size state and wiring
  `onPageChange`, matching the established pattern exactly: `useHealth.ts`'s
  three hooks now take `(page, size)`; `Home/hooks.ts` owns three
  independent page/size pairs (Recent Activity, Executions Today, Failures
  7d — independent because paging one modal must not move another's page
  underneath it) and returns an `onXPageChange` setter per grid, wired in
  `Home/index.tsx`; `LiveActivity/hooks.ts` likewise for its two grids,
  plus a `useEffect` resetting Recent Operations' page to 1 when the
  engine/status filter changes (a second latent bug in the same code path
  — paging to page 5 under one filter then switching filters would have
  landed on a now-out-of-range page); `GovernanceSection.tsx` added a
  `size` state for the conflicts grid and a full page/size pair for the
  failures grid, plus a domain-filter-change page reset (same
  filter-changes-should-reset-page reasoning).
- **Home page modal width fix** (the originally reported issue):
  `ExecutionsDetailModal.tsx`'s `Table` column had no explicit width, so
  AntD's `scroll:{x:'max-content'}` (set in DataGrid for every grid,
  needed elsewhere for wide tables) sized it to the longest unellipsized
  `glue_catalog.db.table` string in the result set, overflowing the
  modal's 800px width. Fixed with an explicit `width: 340` on the Table
  column (so `ellipsis: true` actually truncates instead of being
  overridden by max-content) and widened the modal itself from 800 to
  960px for breathing room. Verified via a headless DOM query for any
  element inside `.ant-modal-content` where `scrollWidth > clientWidth`
  — empty result (no overflow) after the fix, screenshot-confirmed
  alongside.
- Verified live via Playwright end-to-end, not just tsc/build: opened
  Executions Today → selected "15 / page" → pagination footer updated to
  "33 total · 1 2 3 · 15 / page" (was stuck at a single fixed page of 50);
  same for Failures — Last 7 Days ("12 total · 15 / page"); Home's inline
  Recent Activity grid re-paginated to 25/page; Live Activity's Recent
  Operations grid (Currently Running has 0 rows in this seed, so AntD
  correctly renders no pagination bar for it — not a bug) re-paginated to
  15/page; Health Dashboard's Conflicts grid (1 seeded conflicted table)
  size-changer confirmed functional the same way — its FAILED-integrity
  grid has 0 matching rows in the seed data so, like Live Activity's
  Currently Running, no pagination bar renders there either. Zero browser
  console errors across all of the above.
- **Found, not fixed** (pre-existing, unrelated, out of scope): Home's
  "Failures (7d)" KPI card shows 39 but the drill-down modal backing it
  (`useFailures7d`, `status=FAILURE` over the last 7 days) shows 12 total
  — the KPI card's count comes from `health_kpis()`'s server-side
  `failures_7d` field, which apparently uses different criteria than the
  modal's own `/executions?status=FAILURE&from=...&to=...` query. This
  predates this pass (neither number's computation was touched here) and
  wasn't part of what was reported — flagging for awareness, not fixing
  as a drive-by.
- No changes to engine core, orchestrator, gate logic, or vacuum.py — this
  pass is frontend-only.

2026-07-08 App-wide 15/page default + demo login page. Two asks: (1) make
every table's default page size 15 instead of the mix of 10/20/25/50/100
inherited from each page's original build, (2) add a login screen in front
of the dashboard. TypeScript/build/lint clean, live-verified via
Playwright; frontend-only.
- **Page-size default sweep**: every `useState` backing a DataGrid's
  page/size (`ExecutionLog`, `AuditLog`, `LiveActivity`'s two grids,
  `GovernanceSection`'s two grids, all three of Home's execution grids) and
  the corresponding default params on `useHealth.ts`'s three hooks now
  default to 15. Also swept the plain-`<Table>` client-paginated grids
  (`BrowseRegisterTab`, `JobRegistry`'s mapped-tables/CSV-preview tables,
  `ManualApply`'s preview modal, `UnhealthyTablesGrid`) from their
  assorted 10/20 defaults to 15, for one consistent number everywhere a
  table renders, not just the server-paginated ones. Left three
  deliberately-unpaginated spots alone (`TemplatesTab/ViewAll`'s
  `pagination={false}`, `DomainManagement`/`CostReport`/`LocksStrip`'s
  bare-array grids that were already 15) — `CostReport` and
  `DomainManagement` were already at 15 from earlier work, not touched.
- **Login page** (`ui/src/pages/Login/`, new) + `ui/src/auth.ts` (new): a
  client-side-only demo gate — `localStorage` flag, one static credential
  pair (`admin` / `Zamboni@2026`, shown openly on the login screen itself
  in a "Demo credentials" hint, since there's no real secret to protect
  here and hiding it would only make the demo harder to hand off). This is
  **not** real authentication and doesn't touch `api/deps.py::
  get_current_user()` (still the env-var actor stub audit events use,
  contracts.md D5's real OIDC seam for later) — it's trivially bypassed
  from devtools by design, purely to keep the dashboard from being wide
  open when demoed live.
  - `App.tsx` restructured: the previous single `App` component (Sider +
    Header + routed Content) is now `AppShell`; a new top-level `App`
    routes `/login` to `LoginPage` and everything else through a
    `RequireAuth` wrapper that redirects to `/login` (remembering the
    original target via router state) when `isAuthenticated()` is false.
  - `components/UserMenu.tsx`: "Log out" previously showed a
    `message.info` explaining real logout wasn't wired up (honest-stub
    convention, `.claude/decisions.md`) — now that a login gate exists,
    that message would be actively wrong, so it was replaced with a real
    `logout()` + redirect to `/login`. The displayed name is still
    `mode.user` (the backend actor), not the locally-entered login
    username — the two identities are deliberately kept separate rather
    than conflated, since only the backend one is what audit events
    actually record.
  - Design: full-viewport dark navy→teal gradient with two animated
    blurred "aurora" blobs (mint + teal) and a faint grid texture, a
    glassmorphic card (`backdrop-filter: blur`) with the existing
    `assets/zamboni-logo.png` in a soft glowing ring, AntD `Form` styled to
    match the dark surface (not the light-theme default), a gradient
    primary button, and a shake animation + `Alert` on a failed attempt.
    Deliberately restrained relative to a consumer-app login (no confetti/
    particle effects) to match the rest of the app's enterprise-governance
    tone.
  - Verified live via Playwright: visiting any in-app route unauthenticated
    redirects to `/login`; a wrong password shows the error alert (shake
    confirmed via the applied CSS class, not just the alert); the correct
    static credentials redirect back to the originally requested route
    (tested via `/health`, not just `/`); Execution Log's default page
    size reads "15 / page" on first load; Log out returns to `/login` and
    a subsequent visit to `/health` redirects again, confirming the gate
    re-locks rather than leaking a stale authenticated state.
- No changes to engine core, orchestrator, gate logic, vacuum.py, or any
  backend file — this pass is frontend-only, and the login gate has no
  server-side counterpart by design.

2026-07-08 Global top banner swapped: DryRunBanner → FleetHealthBanner.
Sujith: the always-on "DRY RUN mode — no writes will be executed" banner
(contracts.md §7, `.claude/prompts/03_react_foundation.md`'s original
Phase 3 spec) wasn't useful once you already know you're in dry-run —
wanted the top-of-every-page slot to show something actionable instead.
Frontend-only, tsc/build/lint clean, live-verified.
- `components/DryRunBanner.tsx` deleted outright (confirmed zero
  remaining references first) — not deprecated in place, since it had
  exactly one call site (`App.tsx`) and no reason to keep a dead
  component around per the repo's usual "delete, don't stub" convention.
- `components/FleetHealthBanner.tsx` (new): reuses `useHealthKpis()`
  (already the Home/Health Dashboard query, so this doesn't add a new
  endpoint) and its existing `fleet_health.{healthy,needs_attention,
  at_risk}` counts. Renders nothing while loading or if the fleet has zero
  registered tables (same null-while-empty convention the old banner
  used for `dry_run_default`). Tone (mint/amber/red) and exact hex values
  are pulled from `colors.ts`'s existing `statusTagStyles` AT_RISK/
  NEEDS_ATTENTION/SUCCESS entries rather than new ones, so it matches the
  Health Dashboard's own Fleet Health Scorecard colors exactly. Includes
  a "View report →" link to `/health`, same pattern as Home's Governance
  card.
  - **Not currently reflecting the DRY_RUN mode signal at all** — a
    deliberate scope call, not an oversight: Sujith's ask was to replace
    it, not fold dry-run status into it. If dry-run visibility turns out
    to still be wanted somewhere, `DryRunViewer`'s page already covers
    per-table dry-run status, and `useSystemMode().dry_run_default` is
    still fetched (env `DEV · LOCAL` tag in the header) — nothing about
    that signal was removed from the API layer, only the standalone
    global banner.
- `App.tsx`: one-line swap in `AppShell` (`<DryRunBanner />` →
  `<FleetHealthBanner />`), same position (directly under the Header, hides
  above `<Content>`) — this is a true content swap, not a new banner
  stacked alongside the old one.
- Verified live via Playwright against the seeded local DB (fleet_health:
  3 healthy / 8 needs_attention / 1 at_risk): banner renders red-toned
  ("1 at risk" wins the tone-priority check), reads "Fleet Health — 3
  healthy · 8 needs attention · 1 at risk · View report →", and is
  present identically on both Home and a non-Home page (Table
  Registration) confirming it's the shared App-level banner, not
  something Home-specific.
- `.claude/contracts.md` / `decisions.md` / `prompts/03_react_foundation.md`
  / `ui_design.md` deliberately left untouched — they're frozen records of
  the original Phase 3 spec this banner has now diverged from, same
  treatment as every other documented REALITY-note deviation in this
  file, not something to rewrite after the fact.
- No engine, API, or other backend changes.

2026-07-08 **UX Hardening wave CLOSED** — tagged `ui-ux-hardening-v1`.
This wave ran from the 2026-07-07 stream_id removal through today's Fleet
Health banner swap: all driven by Sujith's live testing of the pages Wave
1/2a already shipped, rather than new-page construction. 562 unit + 88 api
tests passing throughout (unchanged all wave — every change in this wave
was frontend-only), ruff/tsc/build/lint clean at every commit. Summary of
what shipped (full detail in each dated entry above, not repeated here):
- **Control-M Integration**: split out of Table Registration into its own
  page and rebuilt across five rounds of live feedback (Job Registry
  Edit/mapped-tables-popup/sample-CSV, Manual Bulk Apply's
  preview-modal + autofill + reset fixes, CSV Workflow's dry-run preview
  step, the job-mapping CSV blank-column bug, the jobs-not-registering
  bug).
- **`stream_id` removed fleet-wide** (org isn't adopting the
  pipeline-stream-grouping model) — engine, API, schema, and Streamlit
  legacy page all cleaned in one pass.
- **Policy Configuration**'s Edit Single Table restructured to an
  accordion + sticky save footer.
- **App-wide pagination**: audited and fixed every `<DataGrid>`/plain-
  `<Table>` instance that had a non-functional or inconsistent page-size
  changer (Home, Live Activity, Health Dashboard's Governance section),
  then swept every table's default down to a uniform 15/page.
- **Home page modals** (Executions Today, Failures 7d) widened to fix a
  horizontal-scroll bug.
- **Demo login page** (`ui/src/pages/Login/`, `ui/src/auth.ts`) — a
  client-side-only gate (static credentials, shown openly on the page
  itself) in front of the whole dashboard, using the real
  `zamboni-logo-badge.png` crop of the product logo. "Log out" in the
  sidebar now does something real (clears the gate) instead of the
  "not wired up" stub message it was before this wave.
- **Top banner**: the always-on DRY RUN disclaimer replaced with a Fleet
  Health status strip (`FleetHealthBanner`, reuses the existing
  `health_kpis` query).
- **`[[project_replatform_state]]`** and this session's memory are updated
  to match as of this close-out — see that file for the current
  route/page inventory rather than re-deriving it here.
- Nothing engine-, orchestrator-, gate-, or vacuum.py-related changed at
  any point in this wave — it is exclusively `ui/` (plus the one-time
  `stream_id` cleanup, which touched `engine/` and `api/` only to *remove*
  a field, not add behavior). Wave 2b (Non-Prod Lifecycle, Stale
  Resources, Settings — still `PlaceholderPage`) is the next open wave
  whenever Sujith picks it back up.

2026-07-08 Phase 5b: Wave 2b — Non-Prod Lifecycle, Stale Resources,
Settings shipped. **UI feature-complete; Streamlit = fallback.** All 13
contracts §7 routes are now real pages; `PlaceholderPage.tsx` deleted
outright (zero remaining call sites, confirmed via grep before deleting —
same "delete, don't stub" convention as `DryRunBanner`'s removal). 562
unit + 97 api tests passing (88 + 9 new), ruff/tsc/build/lint all clean.
Feature inventories for all three Streamlit twins produced and checked
before any code was written (see the phase's own chat transcript — not
persisted as a repo doc, per the "don't create docs unless asked"
convention; the per-tab parity notes below are the durable record).

- **Mandatory-first-step feature inventories** surfaced 3 real backend
  gaps, fixed as part of this wave (required for the tabs the phase brief
  explicitly asked for, not drive-by scope creep):
  1. `executions_svc.stale(kind="hk")` **ignored the `days` param
     entirely** and hardcoded `environment='prod'` — the twin's own
     threshold/env filters did nothing. Fixed to compute `days_since_hk`
     via `DATE_DIFF` and filter on it (mirrors
     `10_Stale_Resources.py`'s `stale_sql` exactly), plus an `environment`
     param.
  2. `stale(kind="orphan")` always returned `[]` — the twin's live
     `list_objects_v2` S3 scan was never implemented server-side. Added a
     real one-level scan (`prefix` param, `parse_s3_uri` + boto3
     `Delimiter='/'`), same "requires CloudTrail integration" caveat the
     twin itself carries for real orphan *detection* (this only lists
     sub-prefixes for manual cross-reference, same as the twin). Router
     failures surface as 400 via the existing `ValueError` → `HTTPException`
     path (S3 exceptions caught and re-raised as `ValueError`).
  3. `GET /api/settings` returned `teams_webhook_url` in the clear — the
     twin masks it before render (`mask_webhook_url`). Masked server-side
     in `settings_svc.get_settings()` now; `update_settings()`'s own
     `before`/audit-trail read still uses the raw unmasked accessor
     (unaffected, audit_log is an internal trail not exposed via GET). The
     Advanced tab's Teams-settings Save sends `teams_webhook_url` only
     when the user actually typed a new one (blank = omit the field
     entirely, relying on `update_settings`'s `{**before, **new}` merge to
     keep the real on-disk value) — resubmitting the masked placeholder
     back to the server was the failure mode this exists to avoid.
- `> ADDED (Phase 5b)`: `GET /api/lifecycle/config` (thresholds sourced
  live from `engine/engines/lifecycle_engine.py`'s
  `DEFAULT_STALE_DAYS`/`DEFAULT_GREENZONE_DAYS`/`DEFAULT_PENDING_DROP_DAYS`
  constants, not hardcoded client-side where they could drift) — same
  precedent as every other additive route in contracts.md §6.
  Contract-smoke route count 51→52.
- **NonProdLifecycle** (`ui/src/pages/NonProdLifecycle/`, 4 tabs): State
  Overview (per-state count KPIs via 4 parallel lightweight
  `GET /api/nonprod?state=X&size=1` calls read for `pagination.total` —
  more accurate than the twin's own `LIMIT 300`-then-`value_counts()`
  client-side approximation — explainer `Collapse` rendering real
  thresholds from the new config endpoint, filterable `<DataGrid>`), Bulk
  Exemption/Claim (3 actionable states fetched in parallel and merged
  client-side with the twin's exact priority sort — PENDING_DROP first —
  since `GET /api/nonprod` only filters one state at a time; rowSelection
  grid, shared reason, Exempt/Claim buttons, one audit-id toast per
  batch), Single Table Action (search-and-select over the full non-DROPPED
  list, client-filtered — matches the twin's own approach, which is also
  a full list feeding a plain `st.selectbox`, not a server search; **both**
  exempt and claim actions here per the phase brief, though the twin only
  had claim), Deletion History (90d grid, storage-reclaimed KPI, CSV
  export).
- **StaleResources** (`ui/src/pages/StaleResources/`, 4 tabs: Stale HK |
  S3 Orphans | Zero-Row | NonProd Stale — the phase brief's explicit tab
  list, not a 1:1 port of the twin's 4 tabs). **Not ported**: the twin's
  "Unregistered Tables" tab (live Glue scan + bulk-register flow) — no
  `kind=unregistered` in contracts, the phase brief's tab list omits it,
  and it duplicates Table Registration's Browse & Register (Wave 2a);
  documented here rather than silently dropped. The twin's inline
  "Non-Prod Stale" sub-section (originally nested inside its Stale Tables
  tab) is promoted to its own top-level tab, matching `kind=nonprod`.
  Each tab is a bare-array `<Table>` (not `<DataGrid>` — `GET /api/stale`
  has no pagination envelope, same documented exception as
  CostReport/DomainManagement) with domain/env/threshold filters mapped
  to the fixed gaps above.
- **Settings** (`ui/src/pages/Settings/`, 4 tabs): General/Enforcement/
  Advanced are `GET/PUT /api/settings` forms — each Save sends only the
  field subset its own tab owns (not a full fetch-then-merge round-trip),
  relying on `update_settings`'s server-side shallow merge; this is also
  what keeps the masked webhook URL from ever being resubmitted verbatim.
  Escalation Matrix: `<Table>` + `Drawer` add/edit (key disabled on edit —
  PK) + `Modal`-confirmed delete, full CRUD already existed
  (`api/services/settings_svc.py`, unchanged this phase) — TanStack
  Query's mutation `onSuccess` + query invalidation gives instant grid
  refresh for free, so the twin's `st.session_state["esc_flash"]` +
  `st.rerun()` flash-message dance has no equivalent bug surface here, not
  just a fix (confirmed live: 3 distinct audit IDs — add, edit, delete —
  stacked and visible in one screenshot). Advanced: backup-pattern
  patterns, Teams enable/webhook (masked, see above; **"Send Test
  Message" not ported** — a live notification-send action, not a settings
  field, no contract endpoint, same class of decision as DryRunViewer's
  "Promote to Live" in Wave 1), Cost Explorer config, SSO/LDAP as a static
  `Alert` (matches the twin — it's just a caption there too, no real
  control). **Active Maintenance Locks**: reused `LiveActivity`'s
  `LocksStrip` component directly rather than linking out or duplicating
  it — it already takes a decoupled `queryResult` prop, and
  Settings/Administration is a natural home for an admin force-release
  action; noting the choice here per the phase brief's "your call."
- `ui/PATTERN.md`: appended the "page inventory" table (all 13 routes →
  key endpoints), per the closure task.
- Swept `ui/src` for `any`/`TODO`/`console.log` — zero matches, nothing to
  clean up or justify.
- **Found, not fixed** (pre-existing, out of scope — confirmed via live
  verification, not guessed): `stale(kind="zero_row")`'s SQL selects
  `partition_date`, a column that does not exist in the local SQLite
  `execution_log` table (Phase 1b's Migration Progress entry already
  documents this exact table's local/Athena column mismatch). `local_db.py`
  catches the resulting SQL error and returns an empty DataFrame rather
  than raising, so the Zero-Row tab always renders "No low-row tables
  found" locally regardless of threshold — not a regression from this
  phase, and not something a new page should silently paper over by
  papering over the underlying schema drift as a drive-by.
- Verified live end-to-end against the seeded local DB (fresh `uvicorn`
  serving the rebuilt `ui/dist`, a stale orphaned worker from an earlier
  session killed first — see `context_hints.md`) via Playwright, per the
  phase's acceptance criteria: bulk-exempted 2 real GREENZONE/
  STALE_CANDIDATE preprod tables with a reason → toast
  `✅ Exempted 2 table(s) (audit: …)` → State Overview's ACTIVE count and
  grid both reflected the change immediately; escalation entry
  add→edit→delete round-trip produced 3 distinct audit IDs and the grid
  returned to its exact pre-add row count; Stale HK (env=prod, days=30 →
  accurately empty, since the seeded fleet's actual `days_since_hk` values
  are all single-digit) and NonProd Stale (3 real seeded rows, correct
  state badges/dates) both confirmed against real data. Zero console
  errors across all three pages. `config/zamboni_settings.json` picked up
  a content-identical CRLF→LF normalization from the live write path
  (`json.dump` writes `\n`) — reverted via `git checkout` so no spurious
  diff was left behind, same care as Phase 5a's template cleanup.
- No changes to `vacuum.py`, orchestrator, gate logic, or any Streamlit
  file this phase.

2026-07-08 Phase 6: Full CloudFormation, Demo Scripts, Smoke Test, Drop
Prep shipped — Workstream B is now demo-ready end to end. **Program
complete personal-side — demo-ready; org drop pending (Phase 7).** 568
unit + 99 api tests passing (unchanged this phase — Phase 6 is
infra/deploy/docs only, zero engine/api/ui source touched), ruff/tsc/
build/lint clean, `cfn-lint deploy/zamboni-cfn.yaml` → zero errors, zero
warnings.

- **Baseline correction, not a regression**: the 562/97 counts documented
  as of the Phase 5b close-out were stale — two commits landed directly
  (not through a Claude Code phase session, no Migration Progress entry)
  between that close-out and this phase starting: `584e8df`
  (`domain_registry.is_active` now actually gates HK/archival/lifecycle
  table selection, new `tests/unit/test_domain_kill_switch.py` — accounts
  for the 562→568 unit delta) and `e8db72e` (escalation drawer overlap,
  Advanced tab validation, dry-run ramp-up default, +32 lines to
  `tests/api/test_tables.py` — accounts for the 97→99 api delta). Actual
  pre-Phase-6 baseline was 568 unit + 99 api; Phase 6 added 0 new tests
  (pure infra, no engine/api/ui source touched), so post-Phase-6 is
  unchanged at 568/99.
- `deploy/zamboni-cfn.yaml` (new, ~450 lines): the complete standalone
  stack per contracts §10 R10.3 — DynamoDB `zamboni_maintenance_locks`
  (§3.1 verbatim, `DeletionPolicy: Retain`), `ZamboniInstanceRole` (Athena
  workgroups, Glue catalog read + `GetTableOptimizer`/
  `BatchGetTableOptimizer`/`ListTableOptimizerRuns` + non-prod-only
  `DeleteTable`, S3 on all 4 app buckets, SNS publish, DynamoDB on the
  lock table, CloudWatch Logs/Metrics, CodeDeploy-agent S3 read) +
  instance profile, security group (8000/8501/22, all three gated by one
  `SourceCidr` parameter — never `0.0.0.0/0`, confirmed by `cfn-lint`
  which would otherwise flag a literal open CIDR), one EC2 instance
  (AL2023 via the public SSM AMI parameter, encrypted gp3 root volume,
  UserData bootstraps CodeDeploy agent + Python 3.11), a CloudWatch log
  group, and a CodePipeline/CodeBuild/CodeDeploy skeleton (GitHub source
  via a `CodeStarSourceConnection` action — `GitHubConnectionArn` is a
  parameter since CFN cannot complete the OAuth handshake itself; the
  pipeline resource is behind a `HasGitHubConnection` condition so the
  rest of the stack still deploys cleanly with it blank). Parameters:
  `NamePrefix`, `VpcId`, `SubnetId`, `SourceCidr`, `KeyName` (optional,
  `HasKeyName` condition), `InstanceType`, `AmiId`, `RootVolumeSizeGiB`,
  `LogRetentionDays`, the 4 bucket names, 2 SNS ARNs, and the 4 GitHub
  params. Outputs: `InstanceId`, `InstancePrivateIp`, `SecurityGroupId`,
  `LockTableName`, `InstanceRoleArn`, `CodeDeployApplicationName`,
  `PipelineArtifactBucket`.
- **Decision, documented here since it diverges from the phase brief's
  literal example**: the phase brief's systemd-unit template used
  `/home/ec2-user/zamboni` as the working directory. This repo's entire
  existing CodeDeploy story (`deploy/appspec.yml`'s `destination:
  /opt/zamboni`, `deploy/iam_policy.json`, `deploy/setup_ec2.sh`,
  `docs/zamboni-direct-setup.md`) already standardizes on `/opt/zamboni`.
  Introducing a second, parallel app-root convention for just the new
  service would leave one EC2 instance with two different "where does
  Zamboni live" answers depending on which service you asked. Overrode
  the brief's literal path to `/opt/zamboni` for `deploy/systemd/
  zamboni-api.service` (and the new `zamboni-streamlit.service`
  alternative) to keep one convention stack-wide — same class of call as
  Phase 1b's "CRITICAL OVERRIDE applied" precedent. The brief's own
  wording ("Phase 7 adjusts if org differs") already treats the path as
  adjustable, not load-bearing.
- `deploy/systemd/zamboni-api.service` (new): uvicorn unit, `--workers 2`,
  `EnvironmentFile=/opt/zamboni/.env`, `Restart=on-failure`. Runs out of a
  **venv** (`/opt/zamboni/.venv/bin/uvicorn`), not the system-wide pip
  install the legacy Streamlit service uses — keeps the new API service's
  dependency set isolated from Streamlit's. `deploy/systemd/
  zamboni-streamlit.service` (new): a venv-based reference alternative to
  the heredoc-generated `zamboni-app.service` in `after_install.sh` (not
  wired in yet — provided for a future cutover that moves Streamlit onto
  the same venv convention as the API service).
- `deploy/scripts/before_install.sh`/`after_install.sh`/`app_start.sh`
  extended (additive — the existing Streamlit heredoc-service path is
  byte-for-byte untouched, contracts §8: "Streamlit unit untouched until
  cutover sign-off"): `before_install.sh` now also stops `zamboni-api` if
  running; `after_install.sh` creates/updates the `.venv` and installs
  `deploy/systemd/zamboni-api.service`; `app_start.sh` starts
  `zamboni-api` and polls `GET /api/system/mode` the same way it already
  polled Streamlit's `/_stcore/health`. `deploy/appspec.yml`'s
  `AfterInstall`/`ApplicationStart` timeouts bumped 300→600 / 120→180 to
  cover the added venv-install and second service start.
- `deploy/buildspec.yml`: `install` phase gained `nodejs: 20` alongside
  `python: 3.11` + `cd ui && npm ci`; `pre_build` gained `pytest tests/api/`
  (documented as its own invocation, matching the `.claude/CLAUDE.md`
  Phase 2 convention) and `npx tsc --noEmit`; `build` gained `npm run
  build`; `post_build` asserts `ui/dist/index.html` exists before calling
  the artifact packaged; `artifacts.exclude-paths` gained `ui/node_modules/**`
  and `node_modules/**` so the multi-hundred-MB dependency trees never
  ship in the deploy artifact (contracts §6 D6: "No Node in production" —
  only the built `ui/dist/` output travels, never Node itself).
- `scripts/aws_smoke_test.py` (new, closes the Phase 0-era backlog item):
  per-mode checks — `local` reports all 7 checks `SKIPPED` with one clear
  reason string (no AWS calls happen at all); `aws_local`/`aws_ec2` run
  real `sts:GetCallerIdentity`, `glue:GetDatabases`, an Athena `SELECT 1`
  in the `app` workgroup (hand-rolled polling loop, not
  `athena_client.run_query()`, since the smoke test needs to force the
  `aws_local` SSO-profile session via `get_boto3_session()` rather than
  `athena_client.py`'s module-level default-credential-chain client), an
  S3 put+delete against `ATHENA_RESULTS_BUCKET`, `sns:GetTopicAttributes`
  on `SNS_ALERT_TOPIC_ARN`, `dynamodb:DescribeTable` on `DDB_LOCK_TABLE`
  (creates it via `scripts/create_lock_table.py` if missing and
  `--create-lock-table` was passed), and one live `glue:GetTableOptimizer`
  probe (compaction/retention/orphan_file_deletion) against the first row
  of `STREAM_REGISTRY_TABLE` — `EntityNotFoundException` (optimizer never
  configured) counts as the API call succeeding, not a failure. `--json`
  for machine-readable output; exits non-zero iff any check hard-FAILs
  (SKIPPED never fails the run). Verified in `mode=local` — see the
  "Local full-stack proof" note below.
- `run_aws_local.ps1` (new): `aws sts get-caller-identity --profile
  $env:AWS_SSO_PROFILE` (default `prod-toolsgenai-sso`), `aws sso login`
  on failure, loads `.env.aws_local` into the process environment, builds
  `ui/dist` if missing, starts uvicorn on :8000, opens the browser via a
  2-second-delayed background job (so it doesn't race uvicorn's startup),
  `try/finally` cleans up that job on Ctrl+C. `run_ui_dev.ps1` (new):
  `-Mode local|aws_local` param, starts `uvicorn --reload` as a
  `Start-Job` and `npm run dev` in the foreground, `Stop-Job`/`Remove-Job`
  in `finally` on Ctrl+C. `run_local_api.bat` (new): Windows-batch parity
  with the existing `run_local.bat` (Streamlit) pattern, but for the
  replatformed stack — seeds the DB and builds `ui/dist` if either is
  missing, sets `ZAMBONI_MODE=local`, opens :8000.
- `.env.aws_local.example` (new): full contracts §2 template plus the
  bucket/SNS vars, `ZAMBONI_MODE=aws_local`, `AWS_SSO_PROFILE`,
  `DRY_RUN_DEFAULT=true`, `APP_ENV=dev`, and the 8 Workstream A safety
  constants (§2) shown explicitly with their defaults so a demo operator
  can see what's tunable without reading `config/settings.py`.
  `.gitignore` gained `.env.aws_local` (the `.example` file itself is
  intentionally NOT ignored — confirmed via `git status` that it tracks
  cleanly, same as the existing `.env.example`).
- `docs/demo/showcase_runbook.md` (new): the July-17 click path, 7 steps
  (Home → Health/Governance dual-optimizer report + incident narrative →
  Policy Config Gate 0 override → Dry Run Viewer → Live Activity locks
  strip → `recover_metadata.py --dry-run` transcript + the "72h floor =
  guaranteed rollback window" line → Control-M Integration CSV Workflow),
  each with its exact current route path (`/health`, `/policies`,
  `/dryrun`, `/activity`, `/controlm` — cross-checked against
  `ui/src/routes.tsx` rather than assumed, since Control-M Integration
  moved to its own top-level route during the UX Hardening wave and a
  runbook written against the old "Bulk Control-M tab inside Table
  Registration" location would have sent Sujith to a dead click on demo
  day). A `ZAMBONI_MODE=local` fallback variant sits directly under every
  step, plus a Streamlit-:8501 last-resort tier and a timing table
  (~21 min walkthrough).
- `docs/deployment/ec2_api_deploy.md` (new): explicit delta against
  `docs/zamboni-direct-setup.md` and `deploy/pipeline_config.md` (does not
  repeat their unchanged IAM/Athena/S3/SNS/EC2 sections) — what's new
  (API service, :8000, `ui/dist` build artifact, DynamoDB lock table, the
  2 new IAM statement blocks), a CloudFormation path (preferred,
  parameter list + `cfn-lint` gate) and a manual path (matches the
  existing docx's command-by-command style) side by side, post-deploy
  validation via `aws_smoke_test.py`, and the post-showcase cutover
  checklist the phase brief asked for (confirm 1-week parity → stop/
  disable `zamboni-app` → remove the :8501 SG rule → archive `app/` to
  `app_legacy_streamlit/` with a README pointer, deliberately phrased as
  a checklist of judgment calls rather than a script, since "does the
  React app actually match" isn't something a command can assert).
- `docs/ORG_DROP.md` extended (not rewritten — the existing 4-section
  1-pager from Phase 0 was accurate, just abstract; this phase's edit
  only adds a concrete "Phase 7 checklist" section that names the actual
  files Phase 6 produced): branch → adapt `zamboni-cfn.yaml` params
  (naming the exact parameters) → `cfn-lint` re-gate → deploy → `python
  scripts/aws_smoke_test.py --create-lock-table` as the "did the
  adaptation actually work" gate → no-merge reminder.
- **Local full-stack proof** (mode=local, fresh `uvicorn api.main:app`
  against the existing seeded `zamboni_local.db`, previous session's
  server confirmed stopped first): `GET /` → 200, `text/html` (React app
  shell, confirming `_SPAStaticFiles` serves `ui/dist/index.html`);
  `GET /health` → 200 (same shell via the SPA-fallback path, proving a
  direct hit on a client-side route doesn't 404 in prod-serve — the exact
  bug class Phase 3 found and fixed); `GET /api/system/mode` →
  `{"mode":"local","app_env":"dev","dry_run_default":true,"user":
  "local-dev","gate0_override_max_hours":24}`; `GET /api/health/kpis` →
  real seeded-fleet data (`total_registered:18`, `coverage_by_domain` all
  4 domains, `fleet_health: {healthy:3, needs_attention:9, at_risk:1}`
  with the same `fin_payment_master`/`AWS optimizer conflict` row Phase
  4/5 verifications also found, `storage_savings`, 3-row
  `dry_run_adoption`) — this is the data Home and Health Dashboard render
  from, confirming both pages have real content to show without needing a
  browser screenshot for this infra-focused phase.
- **`aws_smoke_test.py` mode=local output** (pasted verbatim, `EXIT: 0`):
  ```
  Zamboni AWS Smoke Test  (mode=local)
  Check                      Status     Detail
  sts_identity                SKIPPED   ZAMBONI_MODE=local -- no AWS calls are made in local mode
  glue_list_databases         SKIPPED   ZAMBONI_MODE=local -- no AWS calls are made in local mode
  athena_select_1             SKIPPED   ZAMBONI_MODE=local -- no AWS calls are made in local mode
  s3_put_delete                SKIPPED  ZAMBONI_MODE=local -- no AWS calls are made in local mode
  sns_get_topic_attributes     SKIPPED  ZAMBONI_MODE=local -- no AWS calls are made in local mode
  dynamodb_lock_table          SKIPPED  ZAMBONI_MODE=local -- no AWS calls are made in local mode
  glue_get_table_optimizer     SKIPPED  ZAMBONI_MODE=local -- no AWS calls are made in local mode
  All checks PASSED or SKIPPED cleanly.
  ```
  The `aws_local`/`aws_ec2` expected output (not runnable in this
  environment — no AWS credentials here) is documented in
  `docs/deployment/ec2_api_deploy.md`'s "Post-deploy validation" section:
  all 7 rows PASS with real account/bucket/topic/table detail instead of
  the SKIPPED reason string.
- Pre-existing uncommitted diffs on `config/policy_templates.json` and
  `zamboni_local.db` (present at this phase's start, unrelated to Phase
  6 — a leftover from Sujith's own local testing between sessions) were
  left untouched and excluded from this phase's commit; confirmed via
  `git diff --stat` that neither file's diff grew during this phase's own
  read-only verification calls.
- No changes to `engine/`, `api/` (source), or `ui/` (source) this
  phase — Phase 6 is deploy/scripts/docs only, per the phase brief's
  scope. `vacuum.py`, orchestrator, and gate logic untouched.
- **Program complete personal-side — demo-ready; org drop pending
  (Phase 7).**

2026-07-08 Ad-hoc commit: cleared the `config/policy_templates.json` diff
Phase 6 found pre-existing and deliberately left uncommitted (see the
bullet above). `STAGING_DEFAULT`'s `window_config` had been edited live
through the Policy Configuration → Templates tab at some point before
Phase 6 started (both the React and Streamlit Templates tabs write
straight to this tracked file on Save — there's no draft/staging step)
and never committed: `post_batch` (30min-after-upstream, 4h window,
blackout 6–9/18–21) → `scheduled` (fixed 02:00 start, 4h window, blackout
6–18, a 9-hour daytime window). Gate flags also normalized `0/1` → `false/
true` in the same edit (same values, JSON type only, no behavior change).
Confirmed no impact before committing: `tests/unit/test_config_templates.py`
and `tests/api/test_policies.py` (30 tests) only assert the template's
existence/description/apply-mechanics, never specific `window_config`/gate
field values, and both suites pass unchanged with the new file staged;
already-registered tables that had `STAGING_DEFAULT` applied in the past
are unaffected either way since `apply_template()` copies the template's
values into `hk_config` at apply-time — `hk_config` rows are a snapshot,
not a live reference back to `policy_templates.json`. Committed as
`11c628d`, pushed. `zamboni_local.db`'s own pre-existing diff (unrelated
binary SQLite content, not further investigated) is still sitting
uncommitted in the working tree as of this entry.

2026-07-08 Ad-hoc UX fixes: Dry Run Viewer's empty search dropdown +
Non-Prod Lifecycle KPI cards restyled to match Home. Sujith: the table
search dropdown showed nothing helpful before typing ("maybe the user
will get confused"), and asked for the Non-Prod Lifecycle State Overview
cards to become "home page style cards." `tsc`/build/lint clean,
live-verified via Playwright.
- **`DryRunViewer/index.tsx`**: the `Select`'s `notFoundContent` said `'No
  matching tables'` even on first load before any search — misleading,
  since it reads like a failed search rather than "you haven't searched
  yet." Two sibling pages (`TableRegistration/EditTableTab.tsx`,
  `PolicyConfig/EditTableTab.tsx`) already solve this correctly with
  `'Type to search'`; fixed here to distinguish the two states
  (`search ? 'No matching tables' : 'Type to search'`) rather than just
  copying their unconditional string, since theirs would say "Type to
  search" even after a real zero-result search too.
- **`KpiCard` promoted from page-local to shared**: moved
  `pages/Home/components/KpiCard.tsx` → `components/KpiCard.tsx` (`git mv`,
  history preserved) — it was already generically shaped (palette/value/
  suffix/loading/onClick props, no Home-specific state inside), just
  living in the wrong place for a second page to import it. `colors.ts`
  gained an exported `KpiPalette` interface (was an inline `typeof
  kpiCardPalette[number]`) so both `kpiCardPalette` and the new
  `nonprodStatePalette` share one type.
- **`nonprodStatePalette`** (new, `colors.ts`): 4 entries for ACTIVE/
  STALE_CANDIDATE/GREENZONE/PENDING_DROP, same coastal-gradient formula as
  `kpiCardPalette` but color-staged as an escalation (green → amber →
  orange → red) rather than Home's arbitrary per-metric variety — GREENZONE
  needed its own distinct orange tone since `statusTagStyles` gives it the
  identical amber `STALE_CANDIDATE` already uses, which would have made
  two of the four cards look the same side by side.
- **`StateOverviewTab.tsx`**: swapped the plain `Card`+`Statistic` grid for
  `<KpiCard>`, and — since `KpiCard`'s whole visual identity is "this is
  clickable" (hover lift + `CaretRight` accent) — wired `onClick` to set
  (or clear, if already selected) the same `stateFilter` state the
  existing Lifecycle State `Select` below already drives, resetting to
  page 1. Not asked for explicitly, but shipping a card that *looks*
  exactly like Home's interactive cards while doing nothing on click would
  have been a worse, newly-confusing outcome than the one being fixed;
  the `Select` filter is untouched as a second way to do the same thing.
  Dropped the `STATE_ICONS` emoji prefixes (Home's KPI labels are plain
  text, no emoji) and Title-Cased the labels (`ACTIVE` → "Active") to
  match Home's label style.
- Live-verified via Playwright (login → screenshot each page): Home's KPI
  row unaffected by the component move; Non-Prod Lifecycle's State
  Overview now shows 4 coastal-gradient cards (green/amber/orange/red)
  with the same hover-arrow affordance as Home; Dry Run Viewer's dropdown
  shows "Type to search" on click before any input, confirmed via
  screenshot not just reading the JSX.
- No backend/engine changes — this pass is `ui/src` only.

2026-07-08 Athena read/write cache for the API layer (post-Phase-6 ad-hoc
feature). Sujith reported real EC2 testing feels laggy — every API read/
write hits Athena directly, and Athena's multi-second per-query overhead
(query planning + S3 scan + poll) compounds badly across a dashboard that
fires a dozen queries per load. Asked for reads and writes to be served
from local SQLite with a periodic sync to Athena on a tunable interval.
586 unit + 99 api tests passing, ruff/tsc/build/lint clean, **live-
verified against the real FastAPI app** (not just unit tests) — which is
what actually caught the one real bug in this feature, documented below.

- **Design constraint that shaped everything**: the engine
  (`run_hk.py`/`run_archival.py`/`run_lifecycle_*.py`) runs as separate,
  short-lived processes triggered by EventBridge+SSM — never in-process
  with the FastAPI app (confirmed via `api/main.py`'s own docstring plus a
  repo-wide grep: no `api/` file imports `hk_engine`/`archival_engine`/
  `orchestrator`). This meant the cache could be built as a new module
  (`engine/core/athena_cache.py`) that the engine **never imports at
  all** — an architectural guarantee, not a flag — so Gate 0's AWS-
  optimizer-conflict check, Gate 1's upstream-job check, the lock
  service, and every scheduled-run decision keep reading/writing real
  Athena at full freshness, completely untouched by this feature.
  Explicitly ruled out: flipping `ZAMBONI_LOCAL_MODE=true` in production
  as a shortcut — confirmed via investigation that this flag doesn't just
  swap storage, it stubs `glue_client.get_table_optimizer()` (Gate 0
  always reports no conflict), stubs Gate 1's upstream check to always-
  complete, stubs `integrity_checker.capture_state()`, and leaves
  `engine/operations/archival.py` and SNS ungated by it entirely — using
  it in prod would have silently defeated Workstream A's whole purpose.
- **`engine/core/athena_cache.py`** (new): `read_sql_cached()` — drop-in
  signature-compatible replacement for `athena_client.read_sql()` that
  reads from a second, dedicated SQLite file (`ATHENA_CACHE_DB`, new
  `config/settings.py` constant, default `zamboni_athena_cache.db`,
  gitignored — deliberately not the git-tracked `zamboni_local.db` dev
  fixture) when the new `athena_cache_enabled` setting is on, falling
  through to real Athena on any cache miss/error. `write_columns()` — the
  write-side entry point: splits a column→value dict by
  `SAFETY_CRITICAL_COLUMNS` (`hk_enabled`, `archive_enabled`,
  `lifecycle_enabled`, `gate1_enabled`, `gate2_enabled`, `gate3_enabled`,
  `gate0_override_until/reason/by`, and `domain_registry.is_active` —
  the last one added after remembering `engine/core/registry.py::
  domain_active_filter_sql()` gates HK/archival/lifecycle table selection
  on it, fixed just two days earlier in `584e8df`). Safety-critical
  columns always write synchronously to real Athena, unchanged from
  today's latency — delaying a kill-switch by even a few minutes defeats
  its purpose. Everything else queues into a `_pending_writes` outbox
  table (one row per changed column, not per row) and updates the local
  mirror immediately for read-your-own-writes. `refresh_reads()` pulls a
  fresh `SELECT *` per mirrored table from real Athena and merges into
  the mirror column-by-column, preferring any unsynced pending value over
  the freshly-pulled one (so a refresh can never stomp an edit that
  hasn't flushed yet). `flush_writes()` pushes the outbox to real Athena
  oldest-first as **column-scoped** `UPDATE`s (never a full-row
  overwrite) — this is what guarantees the flush can't clobber an
  engine-owned column on the same row (`conflict_detector.py`'s
  `aws_opt_*` cache, `property_sync.py`'s sync timestamp,
  `idempotency.py`'s `last_execution_id`, `recovery.py`'s
  `metadata_location` — all confirmed via their own scoped `UPDATE`
  statements). Alerts via the existing `engine/core/notifier.py::
  send_alert()` if a write has been stuck past
  `athena_cache_write_flush_alert_after_minutes` (default 15) — a
  silently-stuck sync must never just lose data quietly.
- **Scope, deliberately narrow**: write-caching applies ONLY to
  single-row `UPDATE ... SET col=val WHERE key=val`-shaped mutations.
  Excluded, all documented inline in the touched files, all unchanged
  from pre-feature behavior: bulk/WHERE-clause multi-row updates
  (`bulk_controlm`, `import_job_mapping`, `apply_template_bulk` —
  less latency-sensitive per click, and don't fit the per-row outbox
  model); `INSERT OR REPLACE`/`INSERT OR IGNORE`/`DELETE` statements
  (`controlm_svc.py`'s `upsert_job`/`register_job_if_missing`/
  `delete_job` — a different shape the outbox doesn't cover, and
  `INSERT OR REPLACE` is SQLite-only syntax that was never valid real
  Athena SQL in the first place, a pre-existing latent gap not touched
  here); `lifecycle_svc.py`'s `exempt()`/`claim()` — safety-critical the
  same way HK enable/disable is, since a delayed exemption sync could let
  the Lifecycle Engine's next scheduled run hard-DELETE a PENDING_DROP
  table before it ever flushed; anything delegating to `engine/core/
  registry.py` or `engine/core/config.py` functions (`register_table`,
  `register_domain`, `apply_template`) — those are shared engine code
  this feature must never touch. Net write-caching surface ended up as
  exactly 3 call sites: `tables_svc.py::update_table()`, `domains_svc.py::
  update_domain()`, `policies_svc.py::update_policy()` (all three split
  safety-critical columns out via `write_columns()`; `gates_svc.py::
  update_gates()` needed zero changes since 100% of its columns are
  safety-critical). Read-caching, by contrast, applies broadly — every
  inline `read_sql()` call across all 8 `api/services/*.py` files got the
  one-line import swap (`from engine.core.athena_cache import
  read_sql_cached as read_sql`), since reads carry no correctness risk to
  cache.
- **Found, not fixed** (pre-existing, out of scope, confirmed not
  affected by this feature): `settings_svc.py::list_audit()` (delegates
  to `engine/core/audit.py::get_recent_events()`) and `system_svc.py::
  system_health()`'s Athena connectivity probe stay real-Athena-always —
  the former because it delegates to `engine/core/` (out of scope), the
  latter **by design**: caching a health check would make it report
  "healthy" even when real Athena is actually down, which is the one
  thing a health check must never do. `policies_svc.py::get_policy()`
  and `gates_svc.py::get_gates()` also stay uncached (both delegate to
  `engine/core/config.py::get_hk_config()`, which enforces a real
  `SNAPSHOT_MIN_FLOOR` safety clamp on the returned config — duplicating
  that clamp inline to enable caching risked drifting it out of sync with
  the one true copy, the exact "two similarly-named floor constants"
  class of risk this project's own Conflict List already warns about
  elsewhere; judged not worth it for a lower-traffic detail view).
- **`engine/utils/local_db.py`**: `get_connection()`'s module-level
  singleton (`_conn`) promoted to a dict keyed by resolved path (`_conns`)
  so it can serve two independent SQLite files in one process — the dev/
  demo fixture (`ZAMBONI_LOCAL_DB`) and the new cache mirror
  (`ATHENA_CACHE_DB`) — without colliding. `read_sql_local()`/
  `run_query_local()`/`reset_db()` all gained an optional `db_path` param
  (default unchanged, fully backward compatible). New public
  `translate_athena_sql()` wraps the existing `_translate()` so
  `athena_cache.py` reuses the exact same Athena-to-SQLite rewriting
  local/demo mode already relies on, instead of duplicating it.
  `tests/unit/test_safety_core.py`'s `lock_db` fixture updated for the
  new `_conns` dict shape (was directly monkeypatching the old `_conn`
  singleton in setup and teardown).
- **`scripts/athena_cache_sync.py`** (new) + **`deploy/systemd/
  zamboni-cache-sync.service`** (new): a long-running loop, not a systemd
  timer or EventBridge rule — it re-reads `athena_cache_read_interval_
  seconds`/`athena_cache_write_flush_interval_seconds` from
  `platform_settings` every cycle, so changing them via the Settings UI
  takes effect on the next tick with no redeploy. `deploy/scripts/
  {before_install,after_install,app_start}.sh` extended (additive, same
  pattern as Phase 6's `zamboni-api.service` rollout) to install/start/
  stop this third service alongside `zamboni-app`/`zamboni-api` — non-
  fatal if it fails to start, since `athena_cache_enabled` defaults to
  `false` and the loop is a no-op sleep until explicitly turned on.
- **Settings**: 4 new keys (`athena_cache_enabled` default `false`,
  `athena_cache_read_interval_seconds` default 300,
  `athena_cache_write_flush_interval_seconds` default 60,
  `athena_cache_write_flush_alert_after_minutes` default 15) added to
  `config/platform_settings.py::_safe_defaults()` and
  `config/zamboni_settings.json` — zero new infrastructure, same JSON
  file + `GET/PUT /api/settings` mechanism every other advanced setting
  already uses. Surfaced in Settings → Advanced (new section, same
  pattern as the existing Teams/Cost Explorer toggles) — `ui/src/api/
  types.ts`'s `PlatformSettings` interface gained the 4 matching fields.
- **Real bug found via live verification, not caught by unit tests**:
  the outbox stores `table_name` fully-qualified (`glue_catalog.
  zamboni_catalog.domain_registry`) since `flush_writes()` needs that
  exact form to write real Athena — but the local mirror table populated
  by `refresh_reads()` is only ever named bare (`domain_registry`, via
  `df.to_sql(bare_name, ...)`). The first version of `write_columns()`'s
  optimistic "read-your-own-write" mirror update ran directly against
  the fully-qualified name, which silently no-op'd against a mirror table
  that didn't exist under that name (caught by a bare `except
  OperationalError: pass`) — every unit test had (unrealistically) called
  `write_columns()` with a bare name directly, so none of them caught it;
  only curling the real running API (`PUT /api/domains/finance` then
  immediately `GET` it back) showed the write silently not reflected.
  Compounded by a second orphaned-uvicorn-process false start during the
  same verification (the exact documented `[[project_windows_dev_env_gotchas]]`
  pattern — a stale process from hours earlier was still bound to port
  8000 serving pre-fix code; `Get-CimInstance Win32_Process` was needed
  to find and kill the real PID, `netstat`'s PID column alone was
  insufficient). Fixed with a new `_bare_table_name()` helper used for
  every LOCAL sqlite operation (the optimistic mirror update, and
  `refresh_reads()`'s pending-value lookup, which had the identical
  fully-qualified-vs-bare mismatch on its query side) while
  `flush_writes()` keeps using the stored fully-qualified name for the
  real Athena write. Two new regression tests added
  (`test_write_columns_read_your_own_write_with_fully_qualified_table_name`,
  `test_refresh_reads_pending_write_wins_with_fully_qualified_table_name`)
  using a real fully-qualified name, closing the gap the rest of the
  suite's bare-name-only tests couldn't catch.
- **A second, related gap found and fixed in the same pass**:
  `domains_svc.py::get_domain()` and `tables_svc.py::get_table()`
  delegated to `engine/core/registry.py::get_domain()`/`get_table()` —
  correctly real-Athena-always by the "don't touch engine/core" rule,
  but this meant `GET /api/domains/{name}` (used to populate the edit
  form) showed stale data immediately after its own save, while `GET
  /api/domains` (the list/grid, which already had its own inline
  cache-eligible `read_sql` call) showed the fresh value — a real,
  user-visible inconsistency between two "read the same thing" endpoints,
  again only visible by comparing both live, not from unit tests. Fixed
  by inlining the same trivial single-row `SELECT` both `registry.py`
  helpers already run, routed through `read_sql_cached` instead of
  delegating — `registry.py` itself untouched, still used as before by
  `create_domain`/`update_domain`'s existence-check calls (validation
  gates don't need to be fast/cached) and by whatever engine code calls
  it directly.
- **Live-verified end-to-end against the running FastAPI app** (mode=
  local, `zamboni_local.db` standing in for "real Athena" — genuinely
  exercises the full `refresh_reads()`/`write_columns()`/`flush_writes()`
  code path since local mode's own Athena calls route through the same
  `local_db.py` machinery either way): enabled the cache via the real
  `PUT /api/settings`; confirmed `GET /api/domains` and `GET /api/
  domains/{name}` both serve from the mirror; edited a non-safety field
  (`display_name`) via `PUT /api/domains/finance` — confirmed instant
  success, confirmed the "real Athena" (`zamboni_local.db`) was
  genuinely UNCHANGED immediately after (still queued, not yet synced),
  confirmed both GET endpoints reflected the edit immediately
  (read-your-own-write) after the bug fix above; manually ran
  `flush_writes()` and confirmed the value then landed in the real table
  via a column-scoped `UPDATE`; toggled `gate1_enabled` via `PUT /api/
  gates/{fqn}` and confirmed it wrote to the real table **immediately**
  with zero outbox rows, regardless of the cache being enabled — the
  safety-critical bypass holding under real traffic, not just a mocked
  unit test. All test mutations reverted (`display_name` back to
  `Finance`, `gate1_enabled` back to `false`, `athena_cache_enabled`
  back to `false`) before finishing.
- Test count: 586 unit (568 baseline + 18 new in `tests/unit/
  test_athena_cache.py`) + 99 api (unchanged — this feature added zero
  new API routes, only changed what backs existing ones).
- No changes to `vacuum.py`, orchestrator, Gate 0-3 logic, lock service,
  or any engine-scheduled-run code path — confirmed both by construction
  (this module has no import edge into any of them) and by the live
  verification above (Gate 1 toggle still synchronous under load).

2026-07-09 SQLite control plane — supersedes the 2026-07-08 Athena
read/write cache entirely, not an extension of it. 593 unit (568 + 25 new)
+ 99 api tests passing, ruff/tsc/build/lint clean, plus a real end-to-end
smoke test (not mocked) proving the core claim below.
- **The reframe**: the 2026-07-08 cache treated Athena as ground truth and
  SQLite as a periodically-refreshed derivative, which needed a
  `SAFETY_CRITICAL_COLUMNS` synchronous-bypass and a `_pending_writes`
  outbox to avoid the engine seeing stale gating flags. Sujith reframed
  the problem: make SQLite the *primary* store for 5 low-write-volume
  config tables — `stream_registry`, `hk_config`, `domain_registry`,
  `nonprod_registry`, `controlm_jobs` — so the UI/API write there first
  (fast, synchronous, no queue) and the engine reads the *same* file
  directly. Because SQLite is now the first point of write, engine reads
  are fresh by construction — there is no staleness window to protect
  against, so the entire outbox/merge/bypass apparatus from the prior
  design is unnecessary and was deleted, not adapted.
  `execution_log`/`audit_log`/`vacuum_audit` stay 100% Athena-primary
  (real engine output from data-plane work, unchanged).
- **Engine-owned columns on `stream_registry` excluded from the SQLite
  mirror, stay Athena-direct via their existing dedicated functions**:
  `aws_opt_compaction/retention/orphan`/`aws_opt_checked_at`
  (`conflict_detector.py`, Gate 0's live conflict cache), `last_execution_id`
  (`idempotency.py`), `metadata_location` (`recovery.py`, local-sim-only),
  `properties_synced` (`property_sync.py`). Verified — not assumed — that
  excluding these is safe: `idempotency.check_already_executed()`/
  `mark_executed()` take `(execution_id, table_fqn)` and run their own
  fresh SELECT/UPDATE against real Athena, never consuming a pre-fetched
  row from `registry.get_table()`; same independence confirmed for
  `conflict_detector.get_cached()`. `registry.register_table()`'s INSERT
  (`registry.py:335-381`) had to be edited to drop these two columns from
  its column/VALUES lists, since it previously hardcoded them (`0`/`NULL`).
- **New module `engine/core/control_plane.py`** (replaces
  `engine/core/athena_cache.py`, deleted outright): `read_sql()`/
  `run_query()`/`update_row()`, signature-compatible with
  `engine.utils.athena_client`'s so every migrated caller is an import
  swap, not a rewrite. `_db_path()` resolves to `ZAMBONI_LOCAL_DB` when
  `ZAMBONI_LOCAL_MODE=true` (zero behavior change for local/demo — still
  the same file `scripts/seed_local_db.py` seeds) and the new
  `ZAMBONI_CONTROL_PLANE_DB` otherwise (used by both `aws_local` and
  `aws_ec2`). No outbox — every write is a plain SQL statement.
- **`engine/utils/local_db.py`**: `create_tables()`/`insert_rows()`/
  `reset_db()` gained the same optional `db_path` param `get_connection()`/
  `read_sql_local()`/`run_query_local()` already had from the prior
  session (kept — proven reusable groundwork). Added `synchronous=NORMAL`
  + `busy_timeout=5000` pragmas to `get_connection()`, alongside the
  existing `journal_mode=WAL`.
- **`config/control_plane_schema.py`** (new): the shared DDL/migrations
  source of truth for the 5 control-plane tables, imported by both
  `scripts/seed_local_db.py` (local demo — adds the engine-owned columns
  back on top via `LOCAL_ONLY_MIGRATIONS`-equivalent entries, since local
  mode simulates the *entire* Athena side through one file) and
  `scripts/init_control_plane_db.py` (new, idempotent — `CREATE TABLE IF
  NOT EXISTS` + guarded `ALTER TABLE`, run on every deploy, never seeds
  data). Prevents the demo schema and the real control-plane schema from
  drifting into two independently hand-maintained copies — exactly the
  kind of drift this phase's own research found between `sql/*.sql` and
  the actual runtime schema (see below).
- **Real bug found and fixed during schema research, not cosmetic**:
  `engine/core/config.py::_to_sql_array()` emitted a Presto/Athena
  `ARRAY['a','b']` literal for `hk_config.sort_order_cols` — but that
  column is plain `TEXT` (matching every other write path's convention,
  e.g. the Policy Config page's comma-separated text input), and SQLite
  has no `ARRAY[...]` syntax at all. `run_query_local()` catches and logs
  rather than raising, so `apply_template()` was silently failing to set
  sort columns any time it ran with `sort_columns` provided against
  SQLite — pre-existing under `ZAMBONI_LOCAL_MODE`, and would have stayed
  silently broken in production too once `hk_config` became SQLite-
  primary. Fixed to emit a comma-separated string instead; updated the
  two `tests/unit/test_config_templates.py` tests that asserted the old
  `ARRAY[...]` format.
- **Athena DDL drift closed** (`sql/alter_control_plane_columns.sql`,
  new): real columns the code has used for a while but which never had a
  committed Athena `ALTER TABLE` — `stream_registry`'s
  `controlm_pipeline_job`/`controlm_hk_job`/`controlm_job_start_time`/
  `controlm_expected_duration_min`/`owner_name`/`database_name`;
  `hk_config`'s `gate1/2/3_enabled`/`orphan_cleanup_cadence_days`/
  `sort_order_cols`; `nonprod_registry`'s `stale_threshold_days`/
  `is_backup`/`owner_email`/`database_name`. `sql/create_controlm_jobs.sql`
  (new) is the first real Athena DDL this table has ever had — it
  previously existed only in `scripts/seed_local_db.py`'s local fixture.
  Also fixed two unrelated pre-existing syntax bugs found while in these
  files: a missing comma in `sql/create_domain_registry.sql` (would have
  broken the `CREATE TABLE` if ever run) and a stray quote in
  `sql/create_stream_registry.sql`'s `partition_type` comment.
- **Migration path**: `engine/core/registry.py`, `engine/core/config.py`,
  `engine/engines/lifecycle_engine.py` (imports `read_sql`/`run_query`
  directly, not via `registry.py`) all swapped their import from
  `engine.utils.athena_client` to `engine.core.control_plane` — no other
  code changes needed beyond the `register_table()` fix above, since they
  already emit Athena-dialect SQL that `local_db._translate()` already
  handles. **`engine/core/governance.py` was evaluated and explicitly
  NOT migrated** — its `dual_optimizer_report()`/`fleet_conflict_summary()`
  read `stream_registry.aws_opt_*` directly, which is one of the excluded
  engine-owned columns; routing it through `control_plane.py` would have
  silently broken it against a schema missing those columns. Caught before
  landing, not after — a real save from a hasty migrate-everything pass.
- **`api/services/*.py`** (6 of 8 files): `tables_svc.py`, `domains_svc.py`,
  `policies_svc.py`, `gates_svc.py`, `lifecycle_svc.py`, `controlm_svc.py`
  all migrated to `control_plane.read_sql`/`run_query`/`update_row`,
  deleting `write_columns()`/`SAFETY_CRITICAL_COLUMNS` entirely. Two files
  previously special-cased to bypass the old cache for staleness reasons
  now simply write normally, since the reason no longer applies:
  `gates_svc.py` (Gate 0-3, was 100% synchronous with zero cache
  footprint) and `lifecycle_svc.py`'s `exempt()`/`claim()` (was
  deliberately uncached with an in-code safety comment about lifecycle
  transitions). Bulk operations (`tables_svc.bulk_controlm()`,
  `import_job_mapping()`) and `controlm_svc.py`'s `INSERT OR REPLACE`/
  `INSERT OR IGNORE`/`DELETE` also now just run directly — no more
  "doesn't fit the outbox" carve-out, since there's no more outbox.
  `domains_svc.get_domain()` was simplified to delegate straight to
  `registry.get_domain()` (the earlier inline duplicate existed purely to
  dodge a staleness bug in the old cache design; that reason is gone, so
  the duplicate is too). `executions_svc.py` was reverted back to plain
  `engine.utils.athena_client.read_sql` — it joins Athena-only tables
  (`execution_log`, `vacuum_audit`) against the config tables in single
  SQL statements (e.g. cost/health-KPI queries), which can only run
  against Athena once the config tables live in a physically different
  database; `settings_svc.py` was already untouched (JSON-file-only).
- **`scripts/control_plane_sync.py`** (new, replaces
  `scripts/athena_cache_sync.py`): same long-running-loop-daemon shape
  (re-reads its interval from `platform_settings` every cycle, no
  redeploy needed to retune), but the loop body is trivial since there's
  no outbox — per table, per cycle: `SELECT * FROM <table>` against the
  control-plane DB, then `wr.athena.to_iceberg(..., mode="overwrite")`
  against real Athena. Deliberately full-table overwrite, not `MERGE`:
  Athena/Trino `MERGE` has no "delete rows missing from the source"
  clause, so it can't express a real SQLite-side `DELETE` (e.g.
  `controlm_svc.delete_job()`) — an overwrite naturally reflects deletes,
  a `MERGE` would silently strand them in Athena forever. No-ops cleanly
  under `ZAMBONI_LOCAL_MODE` (nothing to push, no real Athena to push to).
- **`scripts/control_plane_backup.py`** (new): `VACUUM INTO` a temp file
  (never a raw copy of the live DB — transactionally consistent even
  under concurrent WAL writers) on a configurable interval, uploads to
  S3 under `control-plane-backups/`. `prune_backups()` implements the
  two-tier retention exactly as specified: newest backup per hour kept
  for `control_plane_backup_hourly_retention_hours` (default 24), newest
  per day kept for `control_plane_backup_daily_retention_days` (default
  30), everything else deleted — **no EBS snapshot tier**, ruled out
  explicitly by Sujith. `engine/utils/s3_client.py` gained `upload_file()`
  and `delete_keys()` (an explicit-key-list variant of the existing
  `delete_prefix()`) so this stays consistent with the module's
  "never construct a raw boto3 client outside `s3_client.py`" convention.
- **`scripts/control_plane_integrity_check.py`** (new) + `deploy/systemd/
  zamboni-control-plane-integrity.service`+`.timer` (`OnCalendar=daily`):
  `PRAGMA integrity_check`, alerts via `engine/core/notifier.send_alert()`
  on failure — corruption pages loudly, doesn't log-and-continue. The
  faster liveness check (`SELECT 1`) lives directly in `app_start.sh` and
  is fatal (`exit 1`) unlike the pre-existing Athena connectivity check,
  which stays a warning — this file is primary storage now, Athena isn't.
- **Deploy**: `deploy/scripts/{before_install,after_install,app_start}.sh`
  all updated — stop/install/start the two new long-running services
  (`zamboni-control-plane-sync`, `zamboni-control-plane-backup`) and the
  integrity timer, run `scripts/init_control_plane_db.py` on every
  deploy, `mkdir -p /data/zamboni` (idempotent, outside `/opt/zamboni`
  which CodeDeploy replaces wholesale every revision — this directory and
  the file inside it must never be touched by any other step in this
  pipeline). **`/data/zamboni` lives on the existing single root EBS
  volume** — `deploy/zamboni-cfn.yaml` provisions no separate data volume,
  and none was added this pass; a dedicated volume was considered and
  explicitly deferred, not forgotten. `.env.aws_local.example` gained
  `CONTROLM_JOBS_TABLE` and `ZAMBONI_CONTROL_PLANE_DB` (bare filename for
  laptop use; the EC2 `.env` overrides to `/data/zamboni/zamboni_control.db`).
- **Settings**: `config/zamboni_settings.json` + `config/
  platform_settings.py::_safe_defaults()` — the 4 `athena_cache_*` keys
  replaced with `control_plane_sync_interval_seconds` (300),
  `control_plane_backup_interval_seconds` (300),
  `control_plane_backup_hourly_retention_hours` (24),
  `control_plane_backup_daily_retention_days` (30). No more
  enabled/disabled toggle — this isn't an optional performance feature
  anymore, it's the architecture; Settings → Advanced's "Control Plane
  Sync & Backup" section (was "Athena Read/Write Cache") only exposes the
  four intervals now. `ui/src/api/types.ts`'s `PlatformSettings` updated
  to match.
- **Tests**: `tests/unit/test_control_plane.py` (15, replaces the deleted
  `test_athena_cache.py`), `test_control_plane_sync.py` (5 — including a
  regression guard that a row deleted from SQLite is genuinely absent
  from the DataFrame pushed to Athena, not silently retained via
  accidental upsert semantics), `test_control_plane_backup.py` (5 —
  real `VACUUM INTO` against a `tmp_path` SQLite file, no S3 needed; the
  two-tier retention logic against a synthetic S3 key list). **Found and
  fixed a real test-authoring bug while writing these**: the first version
  of `test_control_plane_backup.py` monkeypatched `config.settings.
  ZAMBONI_CONTROL_PLANE_DB`/`ZAMBONI_METADATA_BUCKET`, which silently did
  nothing — `control_plane_backup.py` imports both as module-level names
  (`from config.settings import X`), so the live values it reads are
  bound in its own module namespace, not `config.settings`'s (unlike
  `control_plane.py`'s `_db_path()`, which re-imports fresh inside the
  function body specifically so it stays patchable). Caught immediately
  by the test actually failing against a stray real/default DB path
  instead of the intended `tmp_path` fixture — fixed by patching
  `backup_mod.X` instead. No production code was wrong; only the test's
  patch target was.
- **Live-verified via a real (non-mocked) end-to-end smoke test**, not
  just unit tests: with `ZAMBONI_LOCAL_MODE=false` and a throwaway
  `ZAMBONI_CONTROL_PLANE_DB`, called `domains_svc.create_domain()` (the
  real API-layer function) to register a domain, then called
  `engine.core.registry.get_domain()` (the real engine-layer function)
  directly and confirmed it saw the new domain with zero lag; repeated for
  `tables_svc.register_table()` → `registry.get_table()`, and for flipping
  `hk_enabled` via `tables_svc.update_table()` → confirmed
  `registry.get_table()` reflected it immediately on the next call. This
  is the concrete proof of the redesign's central claim: UI/API writes
  and engine reads share one file, so there is no staleness window.
  Not tested in this environment (no real AWS credentials available):
  `control_plane_sync.py`'s actual push to a real Athena table — that
  logic is unit-tested with a mocked `wr.athena.to_iceberg` instead; a
  real run requires `aws_local` credentials per `docs/deployment/
  ec2_api_deploy.md`'s post-deploy validation section.
- No changes to `vacuum.py`, orchestrator, Gate 0-3 decision logic, or
  the lock service — confirmed by construction (none of them import
  `engine.core.control_plane`) and by the smoke test above (Gate-relevant
  reads/writes all still went through the expected paths).
