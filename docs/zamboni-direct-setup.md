# Zamboni Direct Deployment Guide

Direct Deployment -- No CodeDeploy Required

# Zamboni

Iceberg Table Governance Framework. This guide covers deployment via direct git clone + manual EC2 setup -- no AWS CodeDeploy or CodePipeline needed.


## What is Zamboni?

Zamboni is a self-regulating governance framework that keeps your Apache Iceberg table fleet healthy and cost-efficient. It is not a dumb batch script -- every table is evaluated through multiple intelligent gates before any operation runs.

| Engine | Purpose | Trigger |
| --- | --- | --- |
| **HK Engine** | Compaction, snapshot expiry, orphan file cleanup | EventBridge hourly (or Control-M post-batch) |
| **Archival Engine** | Export-then-Delete cold staging partitions to S3 Intelligent-Tiering | EventBridge weekly |
| **Lifecycle Engine** | Auto-discover and clean up stale non-production tables | EventBridge weekly |

## Architecture

```bash
EventBridge (hourly / weekly)
       | SSM Run Command
       v
EC2 t3.large  (zamboni-ec2-role)
  engine/scripts/run_hk.py          -- HK Engine
  engine/scripts/run_archival.py    -- Archival Engine
  engine/scripts/run_lifecycle_*.py -- Lifecycle Engine
  streamlit run app/Home.py         -- UI (systemd, always-on)
       |              |              |
    Athena           S3            Glue
```

A single EC2 instance runs both the engine processes (triggered by EventBridge) and the Streamlit app (always-on systemd service). A single IAM role `zamboni-ec2-role` provides all AWS permissions.

## Prerequisites

| Item | Required | Notes |
| --- | --- | --- |
| AWS Account | Yes | us-west-2 recommended |
| EC2 Instance | Yes | t3.large, Amazon Linux 2023 |
| Python | 3.11+ | Default on Amazon Linux 2023 |
| Streamlit | ≥1.57.0 | Installed via requirements.txt |
| GitHub access | Yes | SSH key or HTTPS token on EC2 |
| Athena workgroups | 5 required | zamboni-critical/standard/low/archival/app |
| S3 buckets | 4 required | staging, archive, metadata, athena-results |
| SNS topics | 2 required | zamboni-alerts, zamboni-greenzone |

## IAM Role & Policy

Create one EC2 instance profile: `zamboni-ec2-role`. Attach the policy from `deploy/iam_policy.json` (replace ACCOUNT_ID and bucket names first).

| Permission | Service | Resource Scope |
| --- | --- | --- |
| Athena query execution | Athena | zamboni-* workgroups only |
| S3 read/write | S3 | staging, archive, metadata, athena-results buckets |
| Glue read | Glue | All databases and tables |
| Glue DeleteTable | Glue | Non-prod databases only (*_preprod, *_dev, *_test) |
| SNS publish | SNS | zamboni-alerts, zamboni-greenzone topics |
| CloudWatch metrics/logs | CloudWatch | Zamboni namespace, /zamboni/* log groups |
| SSM parameters read | SSM | /zamboni/* parameters |

> **Restrict Glue DeleteTable**The IAM policy already restricts glue:DeleteTable to *_preprod, *_dev, *_test, *_uat database patterns. Never allow wildcard on production databases.

```bash
# Step 1 -- substitute your real AWS Account ID into iam_policy.json
# Also substitute your real bucket names for: your-athena-results, your-staging-bucket, your-archive-bucket, your-zamboni-metadata

# On Linux/EC2:
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed -i "s/ACCOUNT_ID/$ACCOUNT_ID/g" deploy/iam_policy.json
sed -i "s/your-athena-results/YOUR_ACTUAL_ATHENA_BUCKET/g" deploy/iam_policy.json
sed -i "s/your-staging-bucket/YOUR_ACTUAL_STAGING_BUCKET/g" deploy/iam_policy.json
sed -i "s/your-archive-bucket/YOUR_ACTUAL_ARCHIVE_BUCKET/g" deploy/iam_policy.json
sed -i "s/your-zamboni-metadata/YOUR_ACTUAL_METADATA_BUCKET/g" deploy/iam_policy.json

# Verify substitution worked (should show no ACCOUNT_ID or placeholder values)
grep -c "ACCOUNT_ID" deploy/iam_policy.json    # should return 0

# Step 2 -- create the IAM role with the EC2 trust policy
# (deploy/ec2-trust-policy.json is already in the repo -- no edits needed)
aws iam create-role \
  --role-name zamboni-ec2-role \
  --assume-role-policy-document file://deploy/ec2-trust-policy.json

# Step 3 -- attach the inline policy
aws iam put-role-policy \
  --role-name zamboni-ec2-role \
  --policy-name ZamboniPolicy \
  --policy-document file://deploy/iam_policy.json

# Step 4 -- create instance profile and attach role
aws iam create-instance-profile --instance-profile-name zamboni-ec2-profile
aws iam add-role-to-instance-profile \
  --instance-profile-name zamboni-ec2-profile \
  --role-name zamboni-ec2-role
```

> **Placeholders in iam_policy.json**The file ships with `ACCOUNT_ID` in 23 places and generic bucket name placeholders. The `sed` commands above replace all of them in one shot. Check the file afterwards with `grep ACCOUNT_ID deploy/iam_policy.json` -- it should return nothing.

## Athena Workgroups

Create five workgroups in us-west-2. All queries in Zamboni are routed to the correct workgroup by tier.

| Workgroup | Purpose | Scan Limit |
| --- | --- | --- |
| `zamboni-critical` | Critical tier HK operations | 500 GB |
| `zamboni-standard` | Standard tier HK operations | 200 GB |
| `zamboni-low` | Low priority HK operations | 100 GB |
| `zamboni-archival` | Archival Engine | 500 GB |
| `zamboni-app` | Streamlit UI + CLI queries | 50 GB |

```bash
# Create each workgroup (repeat for all 5, changing name and limit)
aws athena create-work-group \
  --name zamboni-standard \
  --configuration '{
    "ResultConfiguration": {
      "OutputLocation": "s3://your-athena-results/zamboni/standard/"
    },
    "BytesScannedCutoffPerQuery": 214748364800,
    "PublishCloudWatchMetricsEnabled": true,
    "EnforceWorkGroupConfiguration": true
  }' --region us-west-2
```

## S3 Buckets

| Bucket Role | .env Variable | Notes |
| --- | --- | --- |
| Staging data | `STAGING_BUCKET` | Where production staging Iceberg tables live |
| Archive destination | `ARCHIVE_BUCKET` | Enable S3 Intelligent-Tiering on this bucket |
| Zamboni metadata | `ZAMBONI_METADATA_BUCKET` | Stores stream_registry, execution_log, hk_config Iceberg tables |
| Athena results | `ATHENA_RESULTS_BUCKET` | Enable lifecycle to delete query results after 7 days |

## SNS Topics

| Topic Name | .env Variable | Used For |
| --- | --- | --- |
| `zamboni-alerts` | `SNS_ALERT_TOPIC_ARN` | Engine failures, circuit breaker trips |
| `zamboni-greenzone` | `SNS_GREENZONE_TOPIC_ARN` | GREENZONE and PENDING_DROP notifications to domain owners |

```bash
aws sns create-topic --name zamboni-alerts    --region us-west-2
aws sns create-topic --name zamboni-greenzone --region us-west-2
aws sns subscribe \
  --topic-arn arn:aws:sns:us-west-2:ACCOUNT_ID:zamboni-alerts \
  --protocol email --notification-endpoint da-team@company.com
```

## EventBridge Scheduling

> **Phase 1: EventBridge + SSM (default)**EventBridge triggers engines via SSM Run Command every hour. The HK engine self-regulates via `run_frequency` and safe-window evaluation -- zero per-pipeline configuration needed. Control-M is optional (Phase 2) for post-batch dependency chaining.

| Rule Name | Schedule | Command |
| --- | --- | --- |
| `zamboni-hk` | Every 1 hour | `python -m engine.scripts.run_hk` |
| `zamboni-archival` | cron(0 4 ? * SUN *) | `python -m engine.scripts.run_archival` |
| `zamboni-nonprod-scan` | cron(0 2 ? * SAT *) | `python -m engine.scripts.run_lifecycle_scan` |
| `zamboni-nonprod-lifecycle` | cron(0 3 ? * SAT *) | `python -m engine.scripts.run_lifecycle_cycle` |
| `zamboni-nonprod-cleanup` | cron(0 5 ? * SUN *) | `python -m engine.scripts.run_cleanup` |

## Launch EC2 Instance

| Property | Value |
| --- | --- |
| Instance type | t3.large |
| AMI | Amazon Linux 2023 (al2023-ami-*) |
| IAM Instance Profile | zamboni-ec2-profile |
| Name tag | zamboni-prod |
| Storage | 30 GB gp3 |
| Security Group | Port 8501 (Streamlit) from your VPN/office IP only |

## Install Dependencies on EC2

SSH into the EC2 instance and run:

```bash
# Update system
sudo dnf update -y

# Python 3.11 is default on AL2023 -- verify
python3 --version   # should show 3.11.x
pip3 --version

# Install git if not present
sudo dnf install -y git

# Create app directory
sudo mkdir -p /opt/zamboni
sudo chown ec2-user:ec2-user /opt/zamboni

# Create log directory
sudo mkdir -p /var/log/zamboni
sudo chown ec2-user:ec2-user /var/log/zamboni
```

## Clone Repository & Configure

### Clone

```bash
cd /opt/zamboni
git clone https://github.com/sujithmsasi/zamboni.git .
git checkout dev
```

> **SSH Key for Private Repo**If the repo is private, add an SSH deploy key: `ssh-keygen -t ed25519 -C "zamboni-ec2"` then add the public key to GitHub Settings → Deploy Keys.

### Install Python Packages

```bash
cd /opt/zamboni
pip3 install -r requirements.txt
```

### Configure .env

```bash
cp .env.example .env
chmod 600 .env
nano .env   # fill in all values -- see .env Configuration section below
```

## Create Athena Metadata Tables

Run once on first deployment. The script reads bucket paths from your `.env` file and creates all six Iceberg metadata tables.

```bash
cd /opt/zamboni
bash deploy/create_athena_tables.sh
```

This creates the following tables in `glue_catalog.zamboni_catalog`:

- domain_registry
- stream_registry
- hk_config
- execution_log
- nonprod_registry
- home_snapshot

> **Safe to re-run**All DDL uses `CREATE TABLE IF NOT EXISTS` -- running the script again on an existing deployment is safe and does nothing.

## Streamlit systemd Service

Register Zamboni as a systemd service so it starts automatically on reboot and restarts on failure.

```bash
sudo nano /etc/systemd/system/zamboni-app.service
```

```bash
[Unit]
Description=Zamboni Streamlit App
After=network.target

[Service]
User=ec2-user
WorkingDirectory=/opt/zamboni
EnvironmentFile=/opt/zamboni/.env
ExecStart=/usr/local/bin/streamlit run app/Home.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/zamboni/streamlit.log
StandardError=append:/var/log/zamboni/streamlit.log

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable zamboni-app
sudo systemctl start  zamboni-app

# Check status
sudo systemctl status zamboni-app

# View logs
tail -f /var/log/zamboni/streamlit.log
```

> **Telemetry Disabled**The `--browser.gatherUsageStats false` flag and `STREAMLIT_BROWSER_GATHER_USAGE_STATS=false` in `.env` prevent Streamlit from sending usage data to Snowflake.

## Validate Setup

```bash
cd /opt/zamboni

# 1. System health check (Athena, S3, Glue, SNS)
python -m engine.monitoring.health_check

# 2. Run unit tests
python -m pytest tests/unit/ -v --tb=short
# Expected: 275 passed

# 3. Lint check (should be 0 errors)
ruff check .

# 4. Test Streamlit is running
curl http://localhost:8501/_stcore/health
# Expected: {"status":"ok"}

# 5. HK Engine dry run (safe -- no writes)
python -m engine.scripts.run_hk --dry-run
```

## Updating the App (Direct Copy)

When a new zip is released, use this process to update the EC2 directly without CodeDeploy.

### Option A -- Pull from Git (Recommended)

```bash
cd /opt/zamboni

# Pull latest from dev branch
git pull origin dev

# Install any new dependencies
pip3 install -r requirements.txt

# Run tests to confirm nothing broken
python -m pytest tests/unit/ -v --tb=short

# Restart Streamlit to pick up changes
sudo systemctl restart zamboni-app
```

### Option B -- Copy from Local Machine via SCP

If you don't have git on EC2 or want to push a specific zip from your Windows machine:

```bash
# From your Windows machine (PowerShell)
# Step 1 -- Extract zip to C:\Users\mrsuj\Downloads\zamboni\

# Step 2 -- SCP the entire folder to EC2
scp -r "C:\Users\mrsuj\Downloads\zamboni\zamboni\*" ec2-user@YOUR_EC2_IP:/opt/zamboni/

# Step 3 -- SSH into EC2 and finish
ssh ec2-user@YOUR_EC2_IP

# On EC2:
cd /opt/zamboni
pip3 install -r requirements.txt
python -m pytest tests/unit/ -v --tb=short
sudo systemctl restart zamboni-app
```

### Option C -- Use the copy_zamboni.ps1 Script (Windows Local Only)

The `copy_zamboni.ps1` script copies all files to your local Windows repo and runs tests before committing. It does NOT deploy to EC2 directly -- after the git push, pull on EC2 using Option A above.

```bash
# Windows PowerShell -- local sync only
powershell -ExecutionPolicy Bypass -File "C:\Users\mrsuj\Downloads\copy_zamboni.ps1"

# Then on EC2 -- pull the commit just pushed
ssh ec2-user@YOUR_EC2_IP "cd /opt/zamboni && git pull origin dev && sudo systemctl restart zamboni-app"
```

## .env Configuration

Copy `.env.example` to `.env` on EC2 and fill in all values. Never commit `.env` to git.

```bash
# AWS
AWS_REGION=us-west-2
AWS_ACCOUNT_ID=123456789012

# Athena
ATHENA_CATALOG=glue_catalog
ATHENA_DATABASE=zamboni_catalog
ATHENA_RESULTS_BUCKET=s3://your-athena-results/zamboni/

# Athena Workgroups
ATHENA_WG_CRITICAL=zamboni-critical
ATHENA_WG_STANDARD=zamboni-standard
ATHENA_WG_LOW=zamboni-low
ATHENA_WG_ARCHIVAL=zamboni-archival
ATHENA_WG_APP=zamboni-app

# Metadata Tables
STREAM_REGISTRY_TABLE=glue_catalog.zamboni_catalog.stream_registry
HK_CONFIG_TABLE=glue_catalog.zamboni_catalog.hk_config
EXECUTION_LOG_TABLE=glue_catalog.zamboni_catalog.execution_log
NONPROD_REGISTRY_TABLE=glue_catalog.zamboni_catalog.nonprod_registry
DOMAIN_REGISTRY_TABLE=glue_catalog.zamboni_catalog.domain_registry

# S3 Buckets
STAGING_BUCKET=s3://your-staging-bucket
ARCHIVE_BUCKET=s3://your-archive-bucket
ZAMBONI_METADATA_BUCKET=s3://your-zamboni-metadata

# SNS
SNS_ALERT_TOPIC_ARN=arn:aws:sns:us-west-2:ACCOUNT_ID:zamboni-alerts
SNS_GREENZONE_TOPIC_ARN=arn:aws:sns:us-west-2:ACCOUNT_ID:zamboni-greenzone

# Execution Log Write Mode
# auto=Parquet+add_files with INSERT fallback | insert=INSERT only | parquet=Parquet only
EXECUTION_LOG_MODE=auto

# CloudTrail (optional -- enables accurate lifecycle stale detection)
CLOUDTRAIL_TABLE=                 # e.g. glue_catalog.logs_db.cloudtrail_events
CLOUDTRAIL_LOOKBACK_DAYS=90

# Engine behaviour
DRY_RUN_DEFAULT=true             # Set false when ready for live runs
LOG_LEVEL=INFO
CIRCUIT_BREAKER_THRESHOLD=3
MAX_CONCURRENT_PARTITIONS=10

# Glue compaction job
COMPACTION_GLUE_JOB_NAME=zamboni-compaction

# Streamlit telemetry -- always keep false
STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
APP_ENV=prod
APP_PORT=8501
```

## D&A Logo

Place your logo files in `app/assets/`. Gradient placeholders are shown until files are present.

| File | Size | Used In |
| --- | --- | --- |
| `app/assets/da_logo.png` | 400x80px | Page header (full size) |
| `app/assets/da_logo_small.png` | 120x40px | Sidebar (compact) |

```bash
# Copy from your machine to EC2
scp your_logo.png      ec2-user@YOUR_EC2_IP:/opt/zamboni/app/assets/da_logo.png
scp your_logo_small.png ec2-user@YOUR_EC2_IP:/opt/zamboni/app/assets/da_logo_small.png

# Restart to pick up
sudo systemctl restart zamboni-app
```

## Policy Templates

| Template | Layer | Strategy | Engine | Snap Retention | Frequency |
| --- | --- | --- | --- | --- | --- |
| `STAGING_DEFAULT` | staging | binpack | Athena | 3 days | daily |
| `DATALAKE_DEFAULT` | datalake | binpack | Athena | 7 days | daily |
| `BASE_SCD2` | base | sort | Glue | 14 days | weekly |
| `MASTER_DEFAULT` | master | zorder | Glue | 30 days | weekly |
| `CRITICAL_HIGH_VOL` | any | binpack | Athena | 5 days | every_trigger |
| `NON_PROD_DEFAULT` | any | binpack | Athena | 3 days | weekly |

Snapshot hard floor: **30 snapshots minimum** regardless of template setting.

#### run_frequency Values

| Value | Threshold | Behaviour |
| --- | --- | --- |
| `every_trigger` | 0h | Runs on every EventBridge trigger |
| `daily` | 20h | Skips with SKIP_NOT_DUE if last run < 20h ago |
| `weekly` | 160h | Skips with SKIP_NOT_DUE if last run < 160h ago |
| `monthly` | 700h | Skips with SKIP_NOT_DUE if last run < 700h ago |

## Domain Retention Policies

| Domain | Hot Retention | Rationale |
| --- | --- | --- |
| `ers` | 7 days | High volume -- propagates to datalake same day |
| `finance` | 30 days | Month-end reconciliation window |
| `financials` | 30 days | Regulatory buffer |
| `membership` | 14 days | Two-week buffer for pipeline re-runs |
| `claims` | 90 days | Claims can be reopened up to 90 days post-processing |
| `travel` | 7 days | Fast propagation |
| `default` | 30 days | Any domain not explicitly listed |

## Window Configuration Reference

#### Type: post_batch

```bash
{
  "type":           "post_batch",
  "timezone":       "America/Los_Angeles",
  "delay_minutes":  30,
  "duration_hours": 4,
  "blackout_hours": [6,7,8,9,18,19,20,21]
}
```

#### Type: scheduled

```bash
{
  "type":           "scheduled",
  "timezone":       "America/Los_Angeles",
  "days":           ["saturday"],
  "start_time":     "02:00",
  "duration_hours": 6
}
```

| Field | Type | Description |
| --- | --- | --- |
| `type` | string | post_batch or scheduled |
| `timezone` | string | IANA timezone e.g. America/Los_Angeles |
| `delay_minutes` | int | (post_batch) Minutes to wait before window opens |
| `duration_hours` | int | How long the window stays open |
| `blackout_hours` | int[] | Hours to never run (e.g. business hours) |
| `days` | string[] | (scheduled) Days of week |
| `start_time` | string | (scheduled) HH:MM start time local |

## HK Engine Reference

```bash
python -m engine.scripts.run_hk [OPTIONS]
```

| Argument | Default | Description |
| --- | --- | --- |
| `--table TEXT` | None | Single table FQN |
| `--domain TEXT` | None | All enabled tables in a domain |
| `--layer TEXT` | None | Filter by layer (staging/datalake/base/master) |
| `--tier TEXT` | None | Filter by tier (critical/standard/low) |
| `--environment TEXT` | prod | Target environment |
| `--dry-run / --no-dry-run` | true | Evaluate gates but do not execute operations |

```bash
# All enabled prod tables (dry run)
python -m engine.scripts.run_hk

# Critical tier only -- live run
python -m engine.scripts.run_hk --tier critical --no-dry-run

# Single table -- live run
python -m engine.scripts.run_hk \
  --table glue_catalog.finance_db.finance_staging --no-dry-run
```

## Archival Engine Reference

```bash
python -m engine.scripts.run_archival [OPTIONS]
```

| Argument | Default | Description |
| --- | --- | --- |
| `--domain TEXT` | None (all) | Limit archival to a specific domain |
| `--environment TEXT` | prod | Target environment |
| `--dry-run / --no-dry-run` | true | Validate pre-archive checks but do not export or delete |

> **archive_enabled = true required**Tables must have `archive_enabled = true` and `archive_retention_days` set in stream_registry before the Archival Engine processes them.

## Lifecycle Engine Reference

Runs as three sequential jobs. The state machine drives non-prod tables from ACTIVE through to DROPPED.

```bash
# Job 1 -- scan and refresh activity signals
python -m engine.scripts.run_lifecycle_scan --environment preprod

# Job 2 -- evaluate state transitions + send GREENZONE emails
python -m engine.scripts.run_lifecycle_cycle --environment preprod --no-dry-run

# Job 3 -- delete PENDING_DROP tables (destructive!)
python -m engine.scripts.run_cleanup --environment preprod --no-dry-run
```

```bash
State machine:
ACTIVE --(inactive > threshold)--> STALE_CANDIDATE
  --> GREENZONE (SNS email, 14-day window)
      |--> owner exempts --> ACTIVE
      |--> window expired --> PENDING_DROP (48h final notice)
          |--> owner exempts --> ACTIVE
          |--> window expired --> DROPPED (Glue DROP + S3 sweep)
```

## CLI: register

```bash
# Discover Iceberg tables in a Glue database
python -m engine.cli.register discover --db finance_db --out finance.yaml

# Bulk register from manifest (dry run first)
python -m engine.cli.register bulk --manifest finance.yaml --dry-run
python -m engine.cli.register bulk --manifest finance.yaml --no-dry-run

# Check registration status
python -m engine.cli.register status --db finance_db
```

## CLI: dry_run

```bash
# Simulate HK for a single table
python -m engine.cli.dry_run --table glue_catalog.finance_db.finance_staging

# Simulate HK for a domain + layer with verbose SQL output
python -m engine.cli.dry_run --domain finance --layer staging --verbose
```

## CLI: fleet_status

```bash
# HK coverage % by domain and layer (default)
python -m engine.cli.fleet_status

# Coverage for a specific domain
python -m engine.cli.fleet_status coverage --domain finance

# Tables not housekept in last 14 days
python -m engine.cli.fleet_status stale --days 14

# Execution health -- successes, failures, skips
python -m engine.cli.fleet_status health --days 7
```

## CLI: enable

```bash
# Enable with ramp-up period (recommended)
python -m engine.cli.enable \
  --domain finance --dry-run-until 2026-06-30 --no-dry-run

# Enable for real after ramp-up period passes
python -m engine.cli.enable --domain finance --layer staging --no-dry-run

# Disable (e.g. for circuit breaker recovery)
python -m engine.cli.enable \
  --table glue_catalog.finance_db.finance_staging --disable --no-dry-run
```

## CLI: cost_report

```bash
# All domains, last 30 days
python -m engine.cli.cost_report

# Specific domain, export to CSV
python -m engine.cli.cost_report --domain finance --days 90 --export report.csv
```

## Onboarding a New Domain

### Register the Domain

Use the Streamlit app (Domain Management page) or seed directly via SQL.

### Discover Tables

```bash
python -m engine.cli.register discover \
  --db my_domain_db --out my_domain.yaml --domain my_domain
```

### Edit the YAML Manifest

Set layer, tier, owner_email, ci_number, policy_template, and partition_column for each table.

### Register Tables

```bash
python -m engine.cli.register bulk --manifest my_domain.yaml --dry-run
python -m engine.cli.register bulk --manifest my_domain.yaml --no-dry-run
```

### Validate with Dry Run

```bash
python -m engine.cli.dry_run --domain my_domain --verbose
```

### Enable with Ramp-Up Period

```bash
# 2-week dry-run period -- HK evaluates but never writes
python -m engine.cli.enable \
  --domain my_domain --dry-run-until 2026-06-30 --no-dry-run

# After 2 weeks -- enable for real
python -m engine.cli.enable --domain my_domain --no-dry-run
```

### Monitor

```bash
python -m engine.cli.fleet_status coverage --domain my_domain
python -m engine.cli.fleet_status health   --domain my_domain --days 7
python -m engine.cli.cost_report           --domain my_domain
```

## CloudWatch Dashboards & Alarms

```bash
# Create all alarms
bash deploy/cloudwatch/create_alarms.sh

# Deploy dashboard
aws cloudwatch put-dashboard \
  --dashboard-name Zamboni \
  --dashboard-body file://deploy/cloudwatch/dashboard.json \
  --region us-west-2
```

| Alarm | Trigger |
| --- | --- |
| `Zamboni-HK-HighFailureRate` | > 5 failures in 1 hour |
| `Zamboni-HK-NoActivity` | Zero tables processed in 26 hours |
| `Zamboni-CircuitBreaker-Trips` | Any circuit breaker trip |
| `Zamboni-HKCoverage-Low` | Coverage < 80% |
| `Zamboni-Archival-Failures` | Any archival failure |
| `Zamboni-Lifecycle-Failures` | Any lifecycle failure |

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| ModuleNotFoundError: dotenv | pip install not run | `pip3 install -r requirements.txt` |
| KeyError: ATHENA_RESULTS_BUCKET | .env not found or not in project root | Run from `/opt/zamboni`; confirm `.env` exists |
| All tables SKIP_OUTSIDE_WINDOW | window_config timezone or hours wrong | Check timezone in hk_config; run `dry_run --verbose` |
| Circuit breaker tripped | 3+ consecutive failures | Fix root cause; run `python -m engine.cli.enable --table <fqn> --no-dry-run` |
| Streamlit not loading | systemd service stopped | `sudo systemctl status zamboni-app` then `journalctl -u zamboni-app -n 50` |
| Athena query FAILED | Workgroup permissions or timeout | Check Athena console Query history for error detail |
| Ruff lint errors in CI | Missing ruff.toml or unsorted imports | `ruff check . --fix` then commit |
| UnicodeDecodeError in tests | Windows cp1252 vs UTF-8 | All file opens in tests use `encoding='utf-8'`; conftest sets PYTHONUTF8=1 |

  Zamboni -- Iceberg Table Governance Framework | D&A Platform | Direct Deployment Guide | v1.0

