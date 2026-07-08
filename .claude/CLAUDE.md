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
ui/                  React 18 + TS + Vite + Ant Design v5 (Phase 3-5a) — Home,
                     Health Dashboard, Domain Management, Live Activity,
                     Execution Log, Cost Report, Dry Run Viewer, Audit Log,
                     Table Registration, and Policy Configuration complete;
                     3 routes still PlaceholderPage (Non-Prod Lifecycle,
                     Stale Resources, Settings — Wave 2b); see ui/PATTERN.md
                     for the canonical page structure Wave 2 replicates
tests/unit/          562 tests, all passing
tests/api/           80 tests — run as its own `pytest tests/api`
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

## Test Baseline (2026-07-06, updated through 2026-07-08 Control-M Integration)
```
python -m pytest tests/unit -q   → 562 passed
python -m pytest tests/api -q    → 86 passed   (separate invocation — see Phase 2 entry)
ruff check .                     → All checks passed!
cd ui && npx tsc --noEmit        → clean
cd ui && npm run build           → clean
cd ui && npm run lint            → clean (oxlint)
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
