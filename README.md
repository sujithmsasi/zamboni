# Zamboni

**Iceberg Table Governance Framework** — automated housekeeping, archival,
and lifecycle management for Apache Iceberg tables at enterprise scale.
FastAPI + React 18 UI (`api/` + `ui/`); Streamlit has been fully
decommissioned.

**Setting up? Start at [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md)** — it
picks the right mode (local / aws_local / aws_ec2) and links to the rest.
The sections below are a CLI/architecture reference, not a setup guide.

---

## Three Engines

| Engine | Purpose | Trigger |
|---|---|---|
| **HK Engine** | Compaction, snapshot expiry, orphan file cleanup | EventBridge (hourly) or Control-M post-batch |
| **Archival Engine** | Export-then-delete cold staging partitions to S3 Intelligent-Tiering | Weekly |
| **Lifecycle Engine** | Auto-discover and clean up stale non-prod tables | Weekly |

---

## Repo Structure

```
zamboni/
├── engine/
│   ├── core/        ← Registry, config, control plane, health, window evaluator, circuit breaker
│   ├── engines/     ← HK, Archival, Lifecycle engine classes
│   ├── operations/  ← Compaction, vacuum, archival, catalog cleanup
│   ├── strategies/  ← Binpack, sort, zorder
│   ├── utils/       ← Athena, S3, Glue clients + logger
│   ├── scripts/     ← Engine entry points (EventBridge / Control-M / manual)
│   └── cli/         ← Helper tools for engineers
├── api/             ← FastAPI app (routers/, services/) — the backend for ui/
├── ui/              ← React 18 + TS + Vite + Ant Design — the UI
├── glue_jobs/       ← PySpark Glue job for sort/zorder compaction
├── sql/             ← Athena DDL (all metadata tables)
├── config/          ← Settings, policy templates, domain retention, control-plane schema
├── tests/           ← Unit + api tests
├── deploy/          ← CI/CD, CodeDeploy hooks, CloudFormation, IAM policy
└── docs/            ← Setup guides, deployment/operations docs
```

---

## Environment

| Property | Value |
|---|---|
| AWS Region | us-west-2 |
| Athena Catalog | glue_catalog |
| Metadata Database | zamboni_catalog |
| Layers | staging · datalake · base · master |
| Python | 3.11 |
| Node | 20 (for `ui/`) |

## Athena Workgroups

| Workgroup | Purpose |
|---|---|
| `zamboni-critical` | Critical tier HK operations |
| `zamboni-standard` | Standard tier HK operations |
| `zamboni-low` | Low priority HK operations |
| `zamboni-archival` | Archival Engine |
| `zamboni-app` | API app + CLI queries |

---

## Quick Start

See **[`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md)** for the full walkthrough
of all three modes. Fastest path (no AWS account needed):

```bash
python scripts/seed_local_db.py   # one-time: create + seed the local database
run_local_api.bat                 # Windows: builds the UI if needed, serves on :8000
```

To just run the tests (no AWS needed):
```bash
python -m pytest tests/unit/ -v
python -m pytest tests/api/ -v    # separate invocation
```

---


## Scheduling

### Phase 1 — EventBridge + SSM (default, no Control-M HK jobs needed)

EventBridge triggers the engines via SSM Run Command on the EC2 instance.
The HK engine runs every hour but self-regulates — skipping tables outside
their safe window or not yet due per `run_frequency`. Zero per-pipeline
Control-M config required.

### Phase 2 (optional) — Control-M + EventBridge safety net

Control-M can trigger engines directly via SSH after batch jobs complete,
using `dependent_on_controlm_job` for upstream dependency chaining.
EventBridge continues as a safety-net (every 6h, `scope=all`).
The engine dedupes automatically — duplicate invocations produce SKIP_NOT_DUE.
No engine code changes required to switch trigger models.

| Rule | Schedule | Command |
|---|---|---|
| `zamboni-hk` | Every 1 hour | `python -m engine.scripts.run_hk` |
| `zamboni-archival` | Sun 04:00 UTC | `python -m engine.scripts.run_archival` |
| `zamboni-nonprod-scan` | Sat 02:00 UTC | `python -m engine.scripts.run_lifecycle_scan` |
| `zamboni-nonprod-lifecycle` | Sat 03:00 UTC | `python -m engine.scripts.run_lifecycle_cycle` |
| `zamboni-nonprod-cleanup` | Sun 05:00 UTC | `python -m engine.scripts.run_cleanup` |

## CLI Reference

All CLI tools are run from the project root. `--dry-run` is the default on all write operations.

### Table Registration

```bash
# Discover all Iceberg tables in a Glue database → generate YAML manifest
python -m engine.cli.register discover --db finance_db --out finance.yaml

# Review and edit finance.yaml, then bulk-register
python -m engine.cli.register bulk --manifest finance.yaml

# Bulk register with dry-run (no writes)
python -m engine.cli.register bulk --manifest finance.yaml --dry-run

# Register a single table
python -m engine.cli.register single \
    --table glue_catalog.finance_db.finance_staging \
    --domain finance \
    --layer staging \
    --tier standard \
    --owner da-finance@company.com

# Check registration status for a database
python -m engine.cli.register status --db finance_db
```

### Dry Run Viewer

```bash
# Simulate HK for a single table (safe — no writes)
python -m engine.cli.dry_run --table glue_catalog.finance_db.finance_staging

# Simulate HK for a whole domain + layer
python -m engine.cli.dry_run --domain finance --layer staging

# Verbose — also shows SQL and Glue job parameters
python -m engine.cli.dry_run --table glue_catalog.finance_db.finance_staging --verbose
```

### Fleet Status

```bash
# HK coverage % by domain and layer (default command)
python -m engine.cli.fleet_status

# Coverage for a specific domain
python -m engine.cli.fleet_status coverage --domain finance

# Execution health — successes, failures, skips (last 7 days)
python -m engine.cli.fleet_status health --days 7

# Tables not housekept in last 14 days
python -m engine.cli.fleet_status stale --days 14

# Stale tables for a specific domain
python -m engine.cli.fleet_status stale --domain finance --days 14
```

### Enable / Disable HK

```bash
# Enable HK for all staging tables in the finance domain
python -m engine.cli.enable --domain finance --layer staging --no-dry-run

# Enable HK for a single table
python -m engine.cli.enable \
    --table glue_catalog.finance_db.finance_staging \
    --no-dry-run

# Enable critical tier only across all domains
python -m engine.cli.enable --tier critical --no-dry-run

# Set dry-run period (HK evaluates but doesn't execute until date passes)
python -m engine.cli.enable \
    --table glue_catalog.finance_db.finance_staging \
    --dry-run-until 2026-06-01 \
    --no-dry-run

# Disable HK for a table (circuit breaker recovery)
python -m engine.cli.enable \
    --table glue_catalog.finance_db.finance_staging \
    --disable \
    --no-dry-run
```

### Cost Report

```bash
# Full cost report — all domains, last 30 days
python -m engine.cli.cost_report

# Specific domain, last 90 days
python -m engine.cli.cost_report --domain finance --days 90

# Export to CSV
python -m engine.cli.cost_report --days 30 --export cost_report.csv
```

### Engine Entry Points (EventBridge / Control-M / Manual)

```bash
# Run HK Engine — all enabled tables
python -m engine.scripts.run_hk

# Run HK Engine — specific domain (dry run)
python -m engine.scripts.run_hk --domain finance --dry-run

# Run HK Engine — single table
python -m engine.scripts.run_hk --table glue_catalog.finance_db.finance_staging

# Run Archival Engine
python -m engine.scripts.run_archival --domain finance --dry-run

# Run Lifecycle Engine — scan non-prod tables
python -m engine.scripts.run_lifecycle_scan --environment preprod

# Run Lifecycle Engine — evaluate states + send notifications
python -m engine.scripts.run_lifecycle_cycle --environment preprod --dry-run

# Run Lifecycle Cleanup — delete expired PENDING_DROP tables
python -m engine.scripts.run_cleanup --environment preprod --dry-run
```

---

## Typical Onboarding Workflow (New Domain)

```bash
# Step 1: Discover tables
python -m engine.cli.register discover --db finance_db --out finance.yaml

# Step 2: Edit finance.yaml
#   - Set layer, tier, owner_email, ci_number per table
#   - Set policy_template (STAGING_DEFAULT, BASE_SCD2, etc.)

# Step 3: Register (dry run first)
python -m engine.cli.register bulk --manifest finance.yaml --dry-run

# Step 4: Register for real
python -m engine.cli.register bulk --manifest finance.yaml --no-dry-run

# Step 5: Validate with dry run
python -m engine.cli.dry_run --domain finance

# Step 6: Enable with dry-run-until (safe period)
python -m engine.cli.enable --domain finance --dry-run-until 2026-06-14 --no-dry-run

# Step 7: After dry-run period — enable for real
python -m engine.cli.enable --domain finance --no-dry-run

# Step 8: Monitor
python -m engine.cli.fleet_status coverage --domain finance
python -m engine.cli.fleet_status health   --domain finance
```

---

## Logo

The React app's logo assets live under `ui/src/assets/` (see
`ui/src/components/` for where they're used — the login page and sidebar).

---

## Branch Strategy

| Branch | Purpose |
|---|---|
| `main` | Production only — PRs from dev with DO team approval |
| `dev` | Active development — all feature branches merge here |
| `feature/*` | One branch per phase/component |

---

## Build Status

| Phase | Description | Status |
|---|---|---|
| 1 | Foundation — SQL DDL, config, utils | ✅ |
| 2 | CI/CD — GitHub Actions + CodePipeline + CodeDeploy | ✅ |
| 3 | Core Layer — registry, health, window, circuit breaker | ✅ |
| 4 | HK Engine — compaction, vacuum, dynamic router, Glue job | ✅ |
| 5 | Archival Engine — export-then-delete | ✅ |
| 6 | Lifecycle Engine — state machine, GREENZONE, cleanup | ✅ |
| 7 | UI — FastAPI + React 18 (replaces the original Streamlit app, since decommissioned) | ✅ |
| 8 | CLI Tools — register, dry-run, fleet-status, enable, cost | ✅ |
| 9 | Hardening — CloudWatch dashboards, alarms, load testing | ⏳ |
