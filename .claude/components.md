# Zamboni — Component Map

First-written 2026-07-05 (Phase 0 audit; no prior version existed).

## engine/core/
| File | Role |
|---|---|
| `registry.py` | Read/write `stream_registry` + `domain_registry`. All queries via `athena_client` — no direct boto3. |
| `config.py` | hk_config reads (gate flags live here too, alongside hk_engine.py). |
| `execution_log.py` | Per-row Athena INSERT writer + query helpers (`get_recent_runs`, `get_failure_count`, `get_last_run`, `get_domain_summary`). |
| `execution_log_parquet.py` | `ParquetLogBuffer` — batch Parquet-to-S3 + `CALL system.add_files` writer, mode-selected via `EXECUTION_LOG_MODE`. |
| `idempotency.py` | Deterministic `execution_id` (sha1 of table+op+window, run_id excluded), `check_already_executed`, `mark_executed`. |
| `property_sync.py` | One-time `ALTER TABLE SET TBLPROPERTIES` for vacuum retention, tier-driven via `commit_frequency.py`. |
| `commit_frequency.py` | Classifies HIGH/MEDIUM/LOW commit tier from `$snapshots`; defines `TIER_PROPERTIES`. |
| `backpressure.py` | `wait_for_capacity` / `can_dispatch` — Athena workgroup concurrency check, fail-open. |
| `circuit_breaker.py` | Per-table failure tracking; `trip(table_fqn, failure_count, dry_run)` disables HK + alerts. |
| `health_checker.py` | Builds `HealthResult` (snapshot_count, expired_snapshots, pipeline_anomaly, etc.) consumed by vacuum/compaction. |
| `window_evaluator.py` | Blackout window / maintenance window checks (Gate 2). |
| `cost_explorer.py` | AWS Cost Explorer integration for cost reporting page. |
| `escalation.py` | Escalation contact management (edit/delete/rerun — see recent commits). |
| `digest.py` | Summary digest generation. |
| `notifier.py` / `teams_notifier.py` | SNS + Teams alerting. |
| `audit.py` | Audit log writes. |

## engine/engines/
| File | Role |
|---|---|
| `base.py` | Shared engine base class. |
| `hk_engine.py` | Housekeeping orchestration: gate1 (Control-M dependency, `:268`), gate2 (blackout window, `:294`), gate3 (circuit breaker, `:329`). No Gate 0 yet. |
| `archival_engine.py` | Export-then-delete cold partitions to S3 Intelligent-Tiering. |
| `lifecycle_engine.py` | Non-prod table discovery + cleanup. |

## engine/operations/
| File | Role |
|---|---|
| `vacuum.py` | `run_expire_snapshots` / `run_orphan_cleanup` — both issue the same bare `VACUUM db.table`. Gaps 1,2,3,9,10 only. |
| `compaction.py` | Compaction operation (binpack/sort/zorder strategies). |
| `archival.py` | Archival file operations. |
| `catalog_cleanup.py` | Glue catalog cleanup helpers. |
| `dynamic_router.py` | `route()` — worker type/count from size+files, execution class from tier. |

## engine/utils/
`athena_client.py` (run_query/read_sql/get_query_stats), `glue_client.py`,
`s3_client.py`, `local_db.py` (SQLite shim + SQL translation for
`ZAMBONI_LOCAL_MODE`), `partition_utils.py` (`parse_table_fqn`), `logger.py`.

## engine/strategies/
`binpack.py`, `sort.py`, `zorder.py` — compaction file-grouping strategies.

## engine/cli/
`register.py`, `enable.py`, `dry_run.py`, `cost_report.py`, `fleet_status.py`
— operator command-line entry points, separate from `engine/scripts/`
(which are the EventBridge/Control-M invocation entry points).

## app/ (Streamlit — current UI, being replaced by FastAPI+React)
`Home.py` + 12 pages under `app/pages/`: Domain Management, Table
Registration (5 tabs incl. bulk Control-M), Policy Configuration, Health
Dashboard, Live Activity, Dry Run Viewer, Execution Log, Cost Report,
NonProd Lifecycle, Stale Resources, Settings, Audit Log.
`app/components/`: `athena_runner.py`, `auth.py`, `ctrlm_helper.py`,
`filters.py`, `grid_utils.py`, `header.py`, `home_snapshot.py`,
`kpi_cards.py`, `reason_form.py`, `sidebar.py`, `status_badge.py`,
`table_selector.py`.

## config/
`settings.py` — the single source of truth; nothing else reads
`os.environ` directly. `platform_settings.py` — platform-level settings
(separate from engine settings).

## scripts/
`seed_local_db.py` — SQLite DDL + an explicit migrations list (used to add
columns incrementally to the local dev DB); `seed_scale_test.py` — scale
testing data generator.

## deploy/
No CloudFormation template. `buildspec.yml` (CodeBuild: lint → unit tests →
integration tests → package), `appspec.yml` + `scripts/{before,after}_install.sh`
+ `app_start.sh` (CodeDeploy hooks: stop Streamlit → copy → pip install →
restart), `iam_policy.json` (EC2 instance role — Athena/S3/Glue/SNS/CW/SSM/
CostExplorer), `ec2-trust-policy.json`, `cloudwatch/{alarms,dashboard}.json`,
`setup_ec2.sh`, `pipeline_config.md`.

## tests/
`tests/unit/` — 494 tests across 22 files (archival, circuit_breaker, cli,
compaction, config_templates, gap_closure_final, hardening_sprint7,
lifecycle_states, monitoring, partition_utils, phase1_enterprise,
phase1_page_features, phase2_p1, settings, sprint1-3/5, v2_alignment,
vacuum, vacuum_hardening, window_evaluator). `tests/integration/` — present
but empty of collected tests as of this audit (buildspec.yml expects it to
possibly have none yet: `|| echo "[build] WARNING: No integration tests
found yet"`). `tests/load/load_test.py` exists separately.
