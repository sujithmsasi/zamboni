# Zamboni — Data Operations Deployment Guide (End to End)

This is the guide for taking Zamboni from "nothing deployed" to "the HK,
Archival, and Lifecycle engines are safely running scheduled governance
work against real tables, with monitoring and a recovery path in place."
It assumes `aws_ec2` (production) deployment — for a laptop demo against
real AWS instead, see `docs/setup/aws_local.md`.

Each step links out to the deeper existing doc for full detail rather
than repeating it — this guide's job is the *order* and the *why*, not
re-deriving content that already lives elsewhere.

---

## 0. Architecture, in one paragraph

Three engines do the actual governance work: **HK** (compaction, snapshot
expiry, orphan cleanup — hourly), **Archival** (export-then-delete cold
staging partitions to S3 — weekly), **Lifecycle** (discover/clean up
stale non-prod tables — weekly, 3 sub-jobs: scan, evaluate, cleanup).
They read/write two separate stores: a SQLite **control plane**
(`stream_registry`/`hk_config`/`domain_registry`/`nonprod_registry`/
`controlm_jobs` — what the UI/API write to directly, so there's no
staleness window between a config change and the engine seeing it) and
**Athena** (real `execution_log`/`audit_log`/`vacuum_audit`, plus the
actual Iceberg tables being governed). A FastAPI+React app gives you the
UI; EventBridge is what actually triggers the three engines on schedule.
See `.claude/CLAUDE.md` for the full build history behind any of this.

---

## 1. Prerequisites

Confirm these exist in the target AWS account before touching CFN — none
of them are created by the template (they're referenced as parameters,
not provisioned):

| Item | Notes |
|---|---|
| VPC + subnet | Where the EC2 instance launches |
| 4 S3 buckets | Athena results, staging, archive, Zamboni metadata |
| 5 Athena workgroups | `zamboni-critical/standard/low/archival/app` |
| Glue database + 8 metadata tables | `zamboni_catalog`'s `domain_registry`/`stream_registry`/`hk_config`/`execution_log`/`nonprod_registry`/`audit_log`/`vacuum_audit`/`controlm_jobs` — DDL in `sql/create_*.sql` + `sql/alter_*.sql` |
| 2 SNS topics | `zamboni-alerts`, `zamboni-greenzone` |
| A CodeStar/CodeConnections GitHub authorization | Console-only, one-time — CFN can't complete the OAuth handshake (`GitHubConnectionArn` parameter) |

If any of these don't exist yet, provision them now — there's no
automation for this step in the repo today (a real gap; the CFN template
starts from "these already exist").

---

## 2. Deploy the infrastructure (CloudFormation)

```bash
cfn-lint deploy/zamboni-cfn.yaml   # zero errors required, every time

aws cloudformation deploy \
  --template-file deploy/zamboni-cfn.yaml \
  --stack-name zamboni \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      NamePrefix=zamboni VpcId=vpc-xxxxxxxx SubnetId=subnet-xxxxxxxx \
      SourceCidr=10.0.0.0/8 \
      AthenaResultsBucketName=... StagingBucketName=... \
      ArchiveBucketName=... MetadataBucketName=... \
      SnsAlertTopicArn=... SnsGreenzoneTopicArn=... \
      EnableEngineScheduling=false
```

Notes:
- **`EnableEngineScheduling=false` on the first deploy, deliberately.**
  The template creates 5 EventBridge rules (`zamboni-hk` hourly,
  `zamboni-archival`/`zamboni-nonprod-*` weekly — full schedule table in
  §6 below) that trigger the engines via SSM Run Command. Leaving this
  `false` creates the rules in `DISABLED` state so nothing fires while
  you're still registering domains/tables and validating dry runs (§3-5).
  Flipping it to `true` later is a plain stack update — the rules aren't
  recreated, just enabled.
- **Pin the AMI after the first deploy.** `AmiId` resolves a dynamic SSM
  parameter that drifts as AWS publishes newer AMIs — re-deploying this
  stack without pinning it can silently replace the running EC2 instance.
  Take the `ResolvedAmiId` output from this first deploy and pass it
  explicitly (`AmiId=ami-xxxxxxxx`) on every deploy after this one.
- **`GitHubConnectionArn` can be left blank on the first deploy.** The
  pipeline resource is entirely conditional on it — if a prior attempt
  got stuck on a GitHub connection stuck in `PENDING` (a manual OAuth
  step CFN can't complete), deploy everything else first, sort the
  connection out separately and verify it's `AVAILABLE`, then re-deploy
  with the ARN set. Full troubleshooting steps in
  `docs/deployment/ec2_api_deploy.md`'s "GitHub connection setup &
  troubleshooting" section.
- Full parameter list, IAM specifics, and the CFN-vs-CodeDeploy separation
  (why an ordinary code push never touches the EC2 instance) are in
  `docs/deployment/ec2_api_deploy.md`.

---

## 3. First boot on the instance

CodeDeploy's first run installs `zamboni-api` (FastAPI, the only app
process — Streamlit was decommissioned 2026-07-11) — see
`deploy/scripts/after_install.sh`. Before anything works end to end:

```bash
# On the instance, /opt/zamboni/.env (after_install.sh copies .env.example
# as a starting template on first deploy only)
vi /opt/zamboni/.env
```

**The one setting most likely to bite you**: `ZAMBONI_CONTROL_PLANE_DB`
must be `/data/zamboni/zamboni_control.db` — an absolute path *outside*
`/opt/zamboni`, which CodeDeploy wipes on every revision. Get this wrong
and the first deploy looks fine; the second deploy silently starts
against an empty control plane, having lost every registered
domain/table/policy.

```bash
python scripts/init_control_plane_db.py          # idempotent, creates the 5 control-plane tables
python scripts/aws_smoke_test.py --create-lock-table --init-control-plane-db
```

Every check should PASS (not SKIPPED). If `control_plane_db` fails
mentioning `/opt/zamboni`, fix `.env` and redeploy before doing anything
else — this is the gate that catches the mistake above.

---

## 4. Register what gets governed

This is the actual data-operations content, done through the UI
(`https://<instance>:8000`) or the CLI (`engine/cli/register.py`):

1. **Domain Management** — register each domain (owner, retention
   defaults, escalation email). A domain must be registered here *and*
   active before its tables can be scanned, candidate-marked, or dropped
   by the Lifecycle Engine — see §7's domain-gating note.
2. **Table Registration → Browse & Register** — select Glue
   database(s)/tables, register them; a policy template auto-applies
   based on inferred layer/tier.
3. **Table Registration → Engine Flags** — confirm `hk_enabled`/
   `archive_enabled`/`lifecycle_enabled` are set as intended per table
   (single-table or bulk).
4. **Control-M Integration** (if this org uses Control-M as the trigger
   instead of/alongside EventBridge) — map `controlm_pipeline_job`/
   `controlm_hk_job`/Gate 1 dependency jobs via the CSV workflow or manual
   bulk apply.

---

## 5. Configure policies and gates

**Policy Configuration** page: per-table or bulk-by-template — compaction
strategy (binpack/sort/zorder), snapshot/orphan retention, window/
blackout schedule (`post_batch` or `scheduled`), and Gates 1-3 (Control-M
dependency / blackout window / circuit breaker). Gate 0 (the AWS-Glue-
optimizer conflict check) is automatic, not configured per table — see
`.claude/CLAUDE.md`'s Phase 1a entry for the full gate mechanics.

**Templates** tab covers applying one config to many tables at once
(`skip_overridden` protects any table someone already hand-edited).

---

## 6. Validate with a dry run before anything fires

Every engine defaults to `dry_run=true` (`DRY_RUN_DEFAULT` in `.env`) —
this is independent of whether EventBridge scheduling is enabled, and is
the second of two layers gating real writes (see §2's note).

- **Dry Run Viewer** page — search a table, see the full gate decision
  (Gate 0-3 + window evaluation) and, for `binpack` tables, the exact
  compaction SQL that would run.
- **CLI**, for a specific table/domain before trusting the schedule:
  ```bash
  python -m engine.scripts.run_hk --domain finance --dry-run
  python -m engine.scripts.run_archival --dry-run
  python -m engine.scripts.run_lifecycle_scan
  python -m engine.scripts.run_lifecycle_cycle --dry-run
  ```
  (`run_lifecycle_scan` has no dry-run flag — it only discovers/refreshes
  `nonprod_registry` rows, it never mutates lifecycle state or drops
  anything.)

**EventBridge schedule** (once `EnableEngineScheduling=true`):

| Rule | Schedule | Command | Log |
|---|---|---|---|
| `zamboni-hk` | rate(1 hour) | `run_hk` | `/var/log/zamboni/hk.log` |
| `zamboni-archival` | Sun 04:00 UTC | `run_archival` | `/var/log/zamboni/archival.log` |
| `zamboni-nonprod-scan` | Sat 02:00 UTC | `run_lifecycle_scan` | `/var/log/zamboni/lifecycle_scan.log` |
| `zamboni-nonprod-lifecycle` | Sat 03:00 UTC | `run_lifecycle_cycle` | `/var/log/zamboni/lifecycle.log` |
| `zamboni-nonprod-cleanup` | Sun 05:00 UTC | `run_cleanup` | `/var/log/zamboni/cleanup.log` |

Full CLI arg reference (`--table`/`--domain`/`--layer`/`--tier`/
`--environment`/`--dry-run`) is in each script's own docstring
(`engine/scripts/run_*.py`).

---

## 7. Go live

Two independent switches, both must be set deliberately — don't flip
either as a reflex:

1. **`EnableEngineScheduling=true`** (CFN stack update) — the rules start
   firing on schedule.
2. **`DRY_RUN_DEFAULT=true` → `false`** in `.env` (restart `zamboni-api`
   and re-source `.env` for any already-scheduled SSM commands) — the
   fired runs start performing real writes, still gated by Gate 0-3, the
   lock service, and (for Lifecycle specifically) the domain-registered-
   and-active check added 2026-07-09: only a table under a *registered,
   active* domain can be scanned, candidate-marked, or dropped — a
   disabled domain blocks the drop even if the table already reached
   `PENDING_DROP`, re-checked immediately before every delete.

Recommendation: flip domain-by-domain or table-by-table (via
`hk_enabled`/`archive_enabled`/`lifecycle_enabled` and `domain_registry.
is_active`) rather than all at once, so the first real runs are against
a small, deliberately-chosen set.

---

## 8. Monitor

- **Live Activity** — currently-running executions + active locks
  (10s polling), force-release if one's genuinely stuck (TTL
  `LOCK_TTL_MINUTES`, default 120).
- **Health Dashboard** — fleet health scorecard, storage reclaimed,
  cost trend, the Dual-Optimizer Risk Report (Gate 0's conflict detector),
  recent integrity failures.
- **Execution Log** / **Audit Log** — every step's before/after
  metadata + snapshot ids, every config change with actor + reason.
- **CloudWatch** — `/${NamePrefix}/app` log group; the 5 EventBridge
  rules' own invocation history is visible in the EventBridge console
  and via SSM Run Command's command history (useful when a scheduled run
  never appears in `execution_log` at all — check SSM first, that means
  the command itself failed before the Python process even started).
- **`Zamboni/BootstrapSuccess` metric** (namespace `Zamboni`, dimension
  `InstanceId`) — published once by UserData at the end of every EC2
  boot, `1` if the CodeDeploy agent came up and is active, `0` if any
  fatal step failed (package install, agent download, `./install auto`,
  or the agent not actually running at the end). This is what catches a
  silently-failed bootstrap *before* it costs you a failed deployment —
  build a CloudWatch alarm on this metric (`< 1` for one datapoint) so a
  bad instance pages someone right after boot instead of surfacing hours
  later as an unexplained CodeDeploy failure. Full step-by-step log is on
  the instance at `/var/log/zamboni/bootstrap.log` (retry attempts,
  which step failed, timestamps).

---

## 9. When something goes wrong

- **A stuck lock**: `docs/runbooks/lock_operations.md` — viewing/
  force-releasing via DynamoDB console, CLI, or the Live Activity page.
- **Bad metadata that needs rolling back**: `docs/runbooks/
  metadata_recovery.md` — `scripts/recover_metadata.py`, and the "72h
  floor = guaranteed rollback window" guarantee (vacuum is contractually
  forbidden from deleting anything younger than
  `ORPHAN_MIN_AGE_HOURS_FLOOR`).
- **An integrity check failed mid-run**: the circuit breaker already
  tripped and halted remaining steps for that table — check
  `execution_log.integrity_status`, the Health Dashboard's Recent
  Integrity Failures grid names the table and operation.
- **A deployment fails with "CodeDeploy agent was not able to receive
  the lifecycle event"**, especially against a recently-replaced
  instance: this is the signature of a silently-failed UserData
  bootstrap (root-caused 2026-07-10 from a real incident — a transient
  `dnf`/download hiccup aborted the whole boot script before the agent
  install ran, leaving an instance that passed EC2 status checks but had
  no agent). Check the `Zamboni/BootstrapSuccess` metric for the
  instance first (§8) — if it's `0` or missing entirely, SSH/Session
  Manager in and read `/var/log/zamboni/bootstrap.log` for the specific
  step that failed, fix the underlying cause (usually transient — retry
  logic already covers most of these), then re-run the UserData manually
  or replace the instance.

---

## 10. Streamlit decommission (complete)

The React/FastAPI stack ran in parity with Streamlit through the agreed
window; Streamlit was then fully decommissioned (2026-07-11) — `app/`,
`.streamlit/`, the `zamboni-app` systemd unit, and the `:8501`
security-group rule are all removed, not archived. See
`docs/deployment/ec2_api_deploy.md`'s "Streamlit decommission" section
for the by-hand retirement steps if you're upgrading an older instance
that still has the service installed.

---

## Reference

| Doc | Covers |
|---|---|
| `docs/SETUP_GUIDE.md` | Which of the 3 modes (local/aws_local/aws_ec2) to use, and first-run setup for each |
| `docs/deployment/ec2_api_deploy.md` | Full CFN/manual deploy detail, the CFN-vs-CodeDeploy separation, control-plane systemd services, cutover checklist |
| `docs/ORG_DROP.md` | Adapting this repo into a different org's AWS account |
| `docs/runbooks/lock_operations.md` | Lock viewing/force-release |
| `docs/runbooks/metadata_recovery.md` | Metadata rollback, the 72h window |
| `docs/demo/showcase_runbook.md` | A guided click-path for demos, not production operation |
| In-app **App Guide** (`/help`) | What every page in the running app does, day to day |
| `.claude/CLAUDE.md` | The full, dated build history — every phase, bug found, and design decision |
