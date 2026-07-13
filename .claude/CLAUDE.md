# Zamboni — Iceberg Table Governance Framework

Automated housekeeping (HK), archival, and lifecycle management for Apache
Iceberg tables at enterprise scale. FastAPI + React 18 UI (`api/` + `ui/`).
Streamlit has been fully decommissioned — do not suggest it as a fallback,
and do not re-add it.

This file is a **living technical reference**, not a changelog. It exists
so a fresh session can work on this codebase correctly without re-deriving
hard-won, non-obvious facts about how the engines actually behave. When you
finish a piece of work, append a short dated entry under "Recent Work" at
the bottom — keep entries factual and terse (what changed, why, anything
deferred), not a narrative. Prune or condense old entries there once
they're no longer decision-relevant; this file should stay a reference,
not accumulate indefinitely.

## Three Engines

| Engine | Purpose | Trigger |
|---|---|---|
| HK Engine (`engine/engines/hk_engine.py`) | Compaction, snapshot expiry, orphan cleanup | EventBridge (hourly) or Control-M post-batch |
| Archival Engine (`engine/engines/archival_engine.py`) | Export-then-delete cold staging partitions to S3 Intelligent-Tiering | Weekly |
| Lifecycle Engine (`engine/engines/lifecycle_engine.py`) | Discover/clean up stale non-prod tables | Weekly |

## Repo Structure

```
engine/core/        Registry, config, control plane, health, window evaluator,
                     circuit breaker, idempotency, property_sync, backpressure,
                     commit_frequency, cost_explorer, escalation, digest,
                     execution_log(+_parquet), audit, notifier, lock_service,
                     integrity_checker, maintenance_ops, orchestrator, recovery,
                     governance, conflict_detector
engine/engines/      hk_engine.py, archival_engine.py, lifecycle_engine.py, base.py
engine/operations/   compaction.py, vacuum.py, archival.py, catalog_cleanup.py,
                     dynamic_router.py
engine/strategies/   binpack, sort, zorder
engine/utils/        athena_client, s3_client, glue_client, local_db (SQLite
                     shim + Athena-dialect SQL translation), logger
engine/scripts/      run_hk.py, run_archival.py, run_cleanup.py,
                     run_lifecycle_cycle.py, run_lifecycle_scan.py
engine/cli/          register, enable, dry_run, cost_report, fleet_status
api/                 FastAPI app — main.py, deps.py, models.py, routers/ (8),
                     services/ (8, one per router)
ui/                  React 18 + TS + Vite + Ant Design v5 — 14 routes (13
                     original + /help App Guide), see ui/PATTERN.md for the
                     canonical page structure
config/              settings.py (central; nothing reads os.environ
                     elsewhere), platform_settings.py, control_plane_schema.py
scripts/             seed_local_db.py (SQLite schema + demo data),
                     init_control_plane_db.py, control_plane_sync.py,
                     control_plane_backup.py, control_plane_restore.py,
                     control_plane_integrity_check.py, aws_smoke_test.py
deploy/              zamboni-cfn.yaml (complete standalone CFN stack) +
                     CodeDeploy/CodeBuild pieces (buildspec.yml, appspec.yml,
                     scripts/, systemd units)
sql/                 Athena DDL for tables that stay Athena-primary
                     (execution_log, audit_log, vacuum_audit) plus the
                     control-plane tables' Athena mirror DDL
tests/unit/          660 tests
tests/api/           100 tests — run as its own `pytest tests/api` invocation,
                     not combined with tests/unit (see "Test Baseline" below)
```

## Engine Architecture Facts (cite before assuming)

- **Vacuum model**: Athena engine v3 uses a single bare `VACUUM db.table;`
  call that does BOTH snapshot expiry and orphan file removal — there is
  no separate orphan-only delete call and no `older_than` parameter passed
  at call time. Retention is controlled entirely via TBLPROPERTIES
  (`vacuum_max_snapshot_age_seconds`, `vacuum_min_snapshots_to_keep`,
  `vacuum_max_metadata_files_to_keep`, `write_target_data_file_size_bytes`)
  set ahead of time by `engine/core/property_sync.py::apply_vacuum_properties`.
  `VACUUM`/`OPTIMIZE` both take `[db_name.]table_name` only — never a
  catalog-qualified 3-part name, and `OPTIMIZE` takes no `TABLE` keyword
  and no inline sizing clause (`engine/strategies/binpack.py::build_optimize_sql`).
  See `engine/operations/vacuum.py:1-20` for the hard-rule comment block.
- **Commit-frequency tiers** (`engine/core/commit_frequency.py`):
  HIGH >48 commits/day → 7d retention; MEDIUM 12-48 → 14d; LOW <12 → 30d.
  All three floors exceed the 72h orphan floor (`ORPHAN_MIN_AGE_HOURS_FLOOR`,
  `config/settings.py`), so there's no numeric clamp conflict.
- **Execution log** has two writer paths: `engine/core/execution_log.py`
  (per-row Athena INSERT, always available) and
  `engine/core/execution_log_parquet.py::ParquetLogBuffer` (batches to a
  single Parquet file on S3 then registers via `CALL system.add_files`).
  Mode selected by `EXECUTION_LOG_MODE` env var (`parquet|insert|both|auto`,
  default `auto`). **New columns must be added to BOTH**
  `execution_log.write()`'s positional INSERT and
  `ParquetLogBuffer._entry_to_dict()` — position is the only contract with
  the Athena table (no column list in the INSERT), so removing a column
  requires the same care at the exact same position in both the Python and
  `sql/create_execution_log.sql`.
  `execution_log.get_running()` excludes rows superseded by a later
  terminal-status row with the same `run_id`+`operation` (correlated
  subquery) and bounds staleness by `LOCK_TTL_MINUTES` — without both, a
  table that completed one orchestrated run would show as permanently
  "already running" and Gate 0's in-flight check would never clear.
- **Backpressure**: `engine/core/backpressure.py::wait_for_capacity` /
  `can_dispatch` check Athena `list_query_executions` +
  `batch_get_query_execution` against per-workgroup concurrency ceilings in
  `_DEFAULT_LIMITS`. Fails open on any check failure. Workgroup routing
  lives in `engine/operations/dynamic_router.py::route()` — despite the
  name this selects Glue **worker type** (size/files → G.1X..G.4X + worker
  count) and **execution class** (tier → STANDARD/FLEX), not the Athena
  workgroup itself; Athena workgroup names live in
  `config/settings.py::ATHENA_WORKGROUPS` (`zamboni-critical/standard/low/
  archival/app`).
- **Idempotency** (`engine/core/idempotency.py`): deterministic
  `execution_id = sha1(table_fqn|operation|window_id)` — `run_id` is
  deliberately excluded from the hash so two different triggers
  (EventBridge safety-net + Control-M) for the same table+window dedupe to
  the same ID.
- **Mode / session factory** (`config/settings.py`): `get_mode()` returns
  one of `local` / `aws_local` / `aws_ec2`; `get_boto3_session()` is the
  one function every AWS call in `aws_local` mode should route through —
  it explicitly pops `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
  `AWS_SESSION_TOKEN`/`AWS_SECURITY_TOKEN` from the environment before
  building the session, since boto3's default credential chain checks
  those env vars *before* a named profile's own credentials even when
  `profile_name=` is passed explicitly — a stale export from an earlier
  session silently wins over a fresh SSO login otherwise. Only
  `athena_client.py`/`glue_client.py`/`s3_client.py`/`notifier.py` build
  plain `boto3.client(...)` clients relying on the ambient default
  credential chain (not `get_boto3_session()` directly) — this is why
  `AWS_PROFILE` (not just `AWS_SSO_PROFILE`) must also be exported into
  the process environment for those calls to pick up the right identity.
- **Lock service** (`engine/core/lock_service.py`): `LockService` with a
  SQLite backend (local/demo) and a DynamoDB backend (`aws_local`/`aws_ec2`),
  transactional delete-expired-then-insert semantics. `LockHeartbeat` is a
  background thread that renews a held lock on an interval and exposes
  `.lost` / `.assert_held()` — a lease is marked lost immediately on a
  rejected heartbeat (another owner stole it) or after
  `MAX_CONSECUTIVE_FAILURES` heartbeat errors in a row. Wired into all four
  lock-holding call sites: `orchestrator.py`, `archival_engine.py`,
  `lifecycle_engine.py::run_cleanup()`, and the legacy (non-orchestrated)
  `hk_engine.py` path. Long-running Athena/Glue polling loops
  (`athena_client.py::_poll()`, `compaction.py::_wait_for_glue_job()`,
  `vacuum.py`) accept a `cancel_check` so a lost lease actually halts
  in-flight work, not just future work.
- **Safe VACUUM** (`engine/core/maintenance_ops.py::run_safe_vacuum`):
  property-clamp (raise `vacuum_max_snapshot_age_seconds` to at least the
  72h floor) → verify the clamp actually took effect via a Glue readback →
  pre-flight sanity check (estimate `would_expire_pct` via
  `"$snapshots"`/`"$files"`, abort with no delete if above
  `MAX_ORPHAN_DELETE_PCT`) → the bare VACUUM call → post-audit. Every step
  either succeeds or raises `SafetyCheckError`, converted into a
  `SafeVacuumResult.aborted` — there are no "log a warning and proceed
  anyway" paths in this function. `engine/core/integrity_checker.py`
  verifies before/after state (`TableState.metadata_capture_ok` /
  `snapshot_capture_ok` are tracked independently — a capture failure on
  either side fails the whole verification closed, never silently treated
  as "unchanged").
- **Gates**: Gate 0 (AWS-Glue-optimizer conflict check, automatic per
  table, not user-configured) → Gate 1 (Control-M upstream dependency) →
  Gate 2 (window/blackout) → Gate 3 (circuit breaker). Gate 0 override
  fields live on `hk_config` (`gate0_override_until/reason/by`), capped by
  `GATE0_OVERRIDE_MAX_HOURS`.

## Control Plane (SQLite-primary config, Athena-primary results)

`stream_registry`, `hk_config`, `domain_registry`, `nonprod_registry`, and
`controlm_jobs` are SQLite-primary **in all modes**, not just local/demo —
the API/UI writes go directly to SQLite (fast, synchronous, no queue) and
the engine reads the same file, so there is no staleness window and no
outbox mechanism (`engine/core/control_plane.py`). `execution_log`,
`audit_log`, and `vacuum_audit` stay 100% Athena-primary
(`engine/utils/athena_client.py`) — real engine output from data-plane
work, not config. A handful of engine-owned columns on `stream_registry`
(`aws_opt_*`, `last_execution_id`, `metadata_location`, `properties_synced`)
are excluded from the control-plane schema and stay Athena-direct via their
own dedicated functions (`conflict_detector.py`, `idempotency.py`,
`recovery.py`, `property_sync.py`) — routing them through
`control_plane.py` would silently break against a schema missing those
columns.

`scripts/control_plane_sync.py` (long-running daemon, `aws_local`/`aws_ec2`
only) pushes SQLite → Athena on an interval via full-table overwrite (not
`MERGE` — Athena/Trino `MERGE` can't express a source-side `DELETE`).
Guards against pushing an accidental empty overwrite when the SQLite side
is empty but the real Athena table still has rows
(`SuspiciousEmptyOverwrite`, override via
`CONTROL_PLANE_SYNC_ALLOW_EMPTY_OVERWRITE`).
`scripts/control_plane_backup.py` takes periodic `VACUUM INTO` snapshots
to S3 (two-tier retention: hourly for a day, daily for a month) — this is
the actual durability story for the control-plane DB, since the EC2 root
volume has `DeleteOnTermination: true`. Restore validates the downloaded
backup (`PRAGMA integrity_check` + confirms all 5 tables present) and
swaps it in atomically (same-filesystem `os.replace`) — never overwrites
the live DB with an unvalidated download.

## Test Baseline

```
python -m pytest tests/unit -q   → 660 passed
python -m pytest tests/api -q    → 100 passed   (separate invocation — see below)
ruff check .                     → All checks passed!
cd ui && npx tsc --noEmit        → clean
cd ui && npm run build           → clean
cd ui && npm run lint            → clean (oxlint)
cfn-lint deploy/zamboni-cfn.yaml → zero errors, zero warnings
```

**Why `tests/api` must run as its own `pytest` invocation**: its
`conftest.py` sets `ZAMBONI_LOCAL_MODE`/`ZAMBONI_MODE`/`ZAMBONI_LOCAL_DB`/
`ZAMBONI_USER` at import time — but `config/settings.py` reads
`ZAMBONI_LOCAL_MODE` once at first import, and several modules
(`athena_client.py`, `glue_client.py`) bind it as a module-level constant
then. If `tests/unit` imports first in the same process, `tests/api`
would run against stale settings.

## Working Conventions

- **No comments unless the WHY is non-obvious.** Don't describe what code
  does — well-named identifiers already do that. A hidden constraint, a
  subtle invariant, or a workaround for a specific bug is worth a comment;
  restating the code is not.
- **Don't build fake functionality.** If something isn't wired up yet (no
  real session, no live endpoint), say so and build an honest stub — don't
  fabricate data or a session that doesn't exist to make a demo look
  complete.
- **Verify live, not just via tests.** Type checks and unit tests verify
  code correctness, not feature correctness. For UI changes, actually
  drive the feature in a browser (or via Playwright) before calling it
  done — this repo's history has repeatedly found real bugs this way that
  tests alone missed (route-registration order, stale query-key caching,
  form-reset-on-success gaps, etc.).
- **`engine/core/` is shared, load-bearing code.** Changes there ripple
  into all three engines and the orchestrator — check call sites before
  changing a function's contract.
- **Local-mode SQL translation** (`engine/utils/local_db.py`) hand-rewrites
  Athena-dialect SQL (`DATE_DIFF`, etc.) into SQLite equivalents. Local
  mode's `"$snapshots"`/`"$files"` metadata queries are stubs (no Iceberg
  metadata tables in SQLite) — treat any local-mode integrity/sanity
  numbers as documented approximations, not real signal.
- **`insert_rows()` hazard** (`engine/utils/local_db.py`): derives its
  INSERT column list from `rows[0].keys()` alone. If you're adding rows to
  `scripts/seed_local_db.py`, keep every dict in one `insert_rows()` batch
  on the *same* key set — a batch mixing dicts with different keys
  silently drops the extra keys for every row, not just the odd one out.
  This has caused two real, previously-silent data-loss bugs in this repo
  (`seed_home_snapshot()`, `seed_domains()`).

## Windows Dev Environment Gotchas

- A stale `uvicorn` process from an earlier session can hold
  `zamboni_local.db` open, causing `PermissionError: [WinError 32]` on
  `python scripts/seed_local_db.py --reset`. Find and kill it first:
  `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like
  '*uvicorn*' } | Select-Object ProcessId, CommandLine` then
  `Stop-Process -Id <PID> -Force`. `netstat`'s PID column alone is
  sometimes insufficient to find the real holder.
- PowerShell 5.1 (not 7+) is the default here. `&&`/`||` chaining doesn't
  work; use one command per line or `;`. `` `n `` in a double-quoted
  string is an actual newline character, not the two-character text `\n`
  — if a script needs to write the literal two characters backslash-n
  into a generated file (e.g. building another program's config/rule
  file), embed them in a single-quoted string, not via `` `n ``.
- A **BOM-less `.ps1` source file** is read by Windows PowerShell 5.1
  using the system ANSI codepage, not UTF-8 — any literal non-ASCII
  character (em-dash, section sign, etc.) typed directly into a script's
  source can get misread and silently corrupt downstream string output.
  Build such characters from `[char]0xXXXX` instead of typing them
  literally if a script needs to emit them.

## Setup

See `docs/SETUP_GUIDE.md` for the three modes (local / aws_local / aws_ec2)
and `README.md` for the CLI reference and architecture overview — this
file intentionally doesn't duplicate either.

---

## Recent Work

_(Append a short dated entry here when you complete non-trivial work —
what changed, why, anything deferred. Keep it factual; this is a
reference for future sessions, not a running commentary.)_
