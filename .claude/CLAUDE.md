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
api/                 FastAPI app (Phase 2) — main.py, deps.py, models.py,
                     routers/ (8, one per contracts §6 section), services/
                     (8, lift SQL from the matching Streamlit page)
ui/                  React 18 + TS + Vite + Ant Design v5 (Phase 3) — Home
                     and Health Dashboard complete, 11 routes still
                     PlaceholderPage; see ui/PATTERN.md for the canonical
                     page structure Waves 1-2 replicate
tests/unit/          562 tests, all passing
tests/api/           61 tests — run as its own `pytest tests/api`
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

## Test Baseline (2026-07-06, updated through Phase 3)
```
python -m pytest tests/unit -q   → 562 passed
python -m pytest tests/api -q    → 61 passed   (separate invocation — see Phase 2 entry)
ruff check .                     → All checks passed!
cd ui && npx tsc --noEmit        → clean
cd ui && npm run build           → clean
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
