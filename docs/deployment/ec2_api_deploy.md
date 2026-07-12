# EC2 API Deploy — Delta Guide (Phase 6)

This is a **delta** against `docs/zamboni-direct-setup.md` (the pre-replatform,
Streamlit-only EC2 guide) and `deploy/pipeline_config.md` (the manual
CodePipeline/CodeDeploy setup instructions) — it does not repeat anything
that hasn't changed. Read one of those two first for the base EC2/IAM/Athena/
S3/SNS setup; this document only covers what Workstream B (the FastAPI +
React replatform) adds on top.

Two ways to provision the new pieces described here:

- **Preferred**: `deploy/zamboni-cfn.yaml` — a complete, standalone
  CloudFormation stack (contracts.md §10 R10.3) that provisions everything
  in this document as CFN resources in one shot. See "CloudFormation path"
  below.
- **Manual**, matching the existing docx's style exactly, for orgs not
  ready to adopt the CFN yet. See "Manual path" below.

---

## What's new

| Area | Before (Streamlit-only) | After (Phase 6) |
|---|---|---|
| App process | `zamboni-app` (Streamlit, :8501) only | `zamboni-app` (:8501, fallback) **+** `zamboni-api` (FastAPI/uvicorn, :8000, primary) — Streamlit has since been fully decommissioned (2026-07-11), see "Streamlit decommission" below |
| Build artifact | Python source only | Python source **+** `ui/dist/` (React production build) |
| CI (`buildspec.yml`) | Python 3.11 only | Python 3.11 **+** Node 20 (`cd ui && npm ci && npm run build`) |
| IAM | `deploy/iam_policy.json` — no DynamoDB, no `GetTableOptimizer` | + DynamoDB (`PutItem`/`GetItem`/`UpdateItem`/`DeleteItem`/`DescribeTable`) on the lock table, + `glue:GetTableOptimizer`/`BatchGetTableOptimizer`/`ListTableOptimizerRuns` |
| New AWS resource | — | DynamoDB table `zamboni_maintenance_locks` (contracts.md §3.1) |
| Security group | :8501 only | + :8000 (FastAPI) — same SourceCidr restriction, never 0.0.0.0/0 |
| Deploy stack | Manual (pipeline_config.md) or direct (zamboni-direct-setup.md) — no CFN existed | `deploy/zamboni-cfn.yaml` (fresh, standalone, parameterized) |

---

## CloudFormation path (preferred)

```bash
cfn-lint deploy/zamboni-cfn.yaml   # zero errors required before every deploy

aws cloudformation deploy \
  --template-file deploy/zamboni-cfn.yaml \
  --stack-name zamboni \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      NamePrefix=zamboni \
      VpcId=vpc-xxxxxxxx \
      SubnetId=subnet-xxxxxxxx \
      SourceCidr=10.0.0.0/8 \
      AthenaResultsBucketName=your-athena-results-bucket \
      StagingBucketName=your-staging-bucket \
      ArchiveBucketName=your-archive-bucket \
      MetadataBucketName=your-zamboni-metadata-bucket \
      SnsAlertTopicArn=arn:aws:sns:us-west-2:ACCOUNT_ID:zamboni-alerts \
      SnsGreenzoneTopicArn=arn:aws:sns:us-west-2:ACCOUNT_ID:zamboni-greenzone
```

This provisions: the EC2 instance (AL2023, encrypted gp3 root volume, IAM
instance profile), the DynamoDB lock table, the security group (:8000/
:8501/:22, `SourceCidr`-gated), a CloudWatch log group, and a CodePipeline/
CodeBuild/CodeDeploy skeleton (the pipeline stage only activates once
`GitHubConnectionArn` is supplied — see the template's parameter
description; a CodeStar/CodeConnections authorization is a manual,
one-time, console-only step CFN cannot complete).

After the stack is up, CodeDeploy still needs `deploy/appspec.yml` +
`deploy/scripts/*.sh` (unchanged locations, extended in Phase 6 — see
below) to actually install and start both services on first deploy.

Outputs worth noting: `InstanceId`, `SecurityGroupId`, `LockTableName`,
`InstanceRoleArn`, `CodeDeployApplicationName`, `PipelineArtifactBucket`,
`ResolvedAmiId` (see the AMI-pinning warning below).

### CloudFormation vs. ongoing code deploys — don't confuse the two

`aws cloudformation deploy` above is a **one-time, deliberate** action —
it provisions the EC2 instance and its surrounding infra exactly once.
Ongoing code pushes never touch CloudFormation at all: the CodeStar
connection watches the GitHub repo directly and triggers `ZamboniPipeline`
(Source → CodeBuild → CodeDeploy) automatically, and CodeDeploy does an
in-place file copy + service restart onto the *already-running* EC2
instance via `deploy/appspec.yml`'s hooks — the instance is never
terminated/recreated by a normal push. `.github/workflows/deploy.yml`
only runs lint/tests as a pre-deploy gate; it doesn't call CloudFormation
or CodePipeline either. If your org's previous setup had a single
pipeline that re-created the EC2 instance (and lost local state/config)
on every push, that problem doesn't exist in this template's design —
CFN and the code-deploy pipeline are architecturally separate here.

**The one way to still trigger an unintended EC2 replacement**: `AmiId`
resolves the SSM path `.../al2023-ami-kernel-default-x86_64` fresh on
every `aws cloudformation deploy` call, and that path's target drifts as
AWS publishes newer patched AMIs. Changing an EC2 instance's `ImageId`
forces CloudFormation to replace it. So the SSM-path default is only
safe for the *first* deploy — after that, always pass the exact value
from the `ResolvedAmiId` output explicitly:
```bash
aws cloudformation deploy ... --parameter-overrides AmiId=ami-xxxxxxxx ...
```
Skipping this is the one remaining way a routine, unrelated stack update
(or just re-running the same deploy command later) could silently pick
up a newer AMI and tear down/respawn the instance — the same symptom as
a bad push-triggered pipeline, just from a different cause.

### GitHub connection setup & troubleshooting (2026-07-09)

`GitHubConnectionArn` is the single most common source of "the pipeline
won't deploy/won't even trigger" confusion — not because the pipeline
resources are misconfigured, but because CodeStar/CodeConnections has an
unavoidable, CFN-cannot-complete manual step baked into it. `deploy/
pipeline_config.md`'s own legacy checklist has a step for this
("verify Available, if not re-authorize it") with zero detail on *why*
it gets stuck — this section is that detail.

**Why it gets stuck**: a connection to GitHub has two states:
- `PENDING` — the connection resource exists (console, CLI, or a future
  CFN `AWS::CodeStarConnections::Connection`), but nothing has authorized
  it against GitHub yet.
- `AVAILABLE` — a human completed the GitHub OAuth handshake: installed/
  authorized the "AWS Connector for GitHub" app, and explicitly granted
  it access to the target repository.

CloudFormation cannot get you from `PENDING` to `AVAILABLE` — this isn't
a bug, it's explicit in this template's own `GitHubConnectionArn`
parameter description ("CFN cannot complete the OAuth handshake"). A
pipeline wired to a still-`PENDING` connection fails its Source stage (or
never triggers on push at all) — this is almost certainly what happened
in a prior deploy attempt if the connection step was left half-done.

**The exact steps, in order**:
1. Console: Developer Tools → Settings → Connections → Create connection
   → GitHub → name it.
2. **Complete the authorization immediately, don't leave it half-done**:
   click through to install (or select an existing) "AWS Connector for
   GitHub" app, pick the GitHub org/account, and explicitly grant it
   access to the `zamboni` repo (either directly or via "All
   repositories," per your org's convention).
   - **If your GitHub org restricts third-party GitHub App installs**,
     this step needs a GitHub org owner's approval — this is the single
     most common reason it gets stuck for more than a few minutes. If
     you're not an org owner yourself, this becomes an external
     dependency to chase down, not something retryable from the AWS side.
3. **Verify before touching CloudFormation at all**:
   ```bash
   aws codestar-connections list-connections --region <region>
   aws codestar-connections get-connection --connection-arn <arn> --region <region>
   ```
   Confirm `"ConnectionStatus": "AVAILABLE"`. If it still says
   `"PENDING"`, go back to step 2 — nothing on the CFN/pipeline side can
   fix a pending connection.
4. Only then pass the ARN as `GitHubConnectionArn=<arn>` in the stack
   deploy.

**The safe sequencing this template already supports**:
`GitHubConnectionArn` defaults to blank, and the entire `ZamboniPipeline`
resource is conditional on it (`HasGitHubConnection`) — so:
- Deploy everything else first (EC2, IAM, security group, lock table,
  CodeBuild project, CodeDeploy application) with `GitHubConnectionArn`
  left blank, and get the app running via a manual first deploy (`scp`
  the repo + `deploy/setup_ec2.sh`, or a manual CodeDeploy
  `create-deployment` CLI call against an S3-uploaded revision).
- Sort out the GitHub connection as its own, isolated step, verified via
  the CLI check above, with zero time pressure from a stalled app deploy.
- Only then re-run `aws cloudformation deploy` with `GitHubConnectionArn`
  set — a plain stack update that adds the pipeline resource; it doesn't
  touch or replace the already-running EC2 instance (same "additive, no
  replacement" category as everything in this template except the AmiId
  drift case above).

This decouples "is my app running" from "is the GitHub connection
authorized" — a stuck connection then costs you nothing but the CI/CD
convenience layer, not the whole deploy, which is the opposite of what
happened last time.

## Manual path (no CFN, matches the existing docx's conventions)

Everything from `docs/zamboni-direct-setup.md`'s IAM/Athena/S3/SNS/EC2
sections still applies. Only the additions below are new:

### 1. IAM — add DynamoDB + GetTableOptimizer

Add to `deploy/iam_policy.json` (or attach as a second inline policy):

```json
{
  "Sid": "DynamoDbLockTable",
  "Effect": "Allow",
  "Action": ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem",
             "dynamodb:DeleteItem", "dynamodb:DescribeTable"],
  "Resource": "arn:aws:dynamodb:us-west-2:ACCOUNT_ID:table/zamboni_maintenance_locks"
},
{
  "Sid": "GlueTableOptimizer",
  "Effect": "Allow",
  "Action": ["glue:GetTableOptimizer", "glue:BatchGetTableOptimizer",
             "glue:ListTableOptimizerRuns"],
  "Resource": "*"
}
```

### 2. DynamoDB lock table

```bash
python scripts/create_lock_table.py
# or: aws dynamodb create-table --table-name zamboni_maintenance_locks ...
#     (see contracts.md §3.1 for the full KeySchema/TTL spec)
```

### 3. Security group — open :8000

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxx --protocol tcp --port 8000 \
  --cidr YOUR_VPN_OR_OFFICE_CIDR
```

### 4. Build the React UI into the deploy artifact

The `ui/dist/` production build must exist alongside the Python source
before `zamboni-api.service` starts (contracts.md D6: "no Node in
production" — the build happens in CI/CodeBuild or on a dev machine, never
on the EC2 instance itself):

```bash
cd ui && npm ci && npm run build && cd ..
# ui/dist/index.html must now exist
```

`deploy/buildspec.yml` does this automatically as part of the CodeBuild
`install`/`build` phases if you're using CodePipeline.

### 5. Python venv + zamboni-api systemd service

```bash
cd /opt/zamboni
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

sudo cp deploy/systemd/zamboni-api.service /etc/systemd/system/zamboni-api.service
sudo systemctl daemon-reload
sudo systemctl enable zamboni-api
sudo systemctl start zamboni-api

# validate
curl http://localhost:8000/api/system/mode
# {"data":{"mode":"aws_ec2","app_env":"prod","dry_run_default":true},"pagination":null,"error":null}
```

`deploy/scripts/before_install.sh`/`after_install.sh`/`app_start.sh` (used
by the CodeDeploy path) already do steps 5 automatically as of Phase 6 —
the manual commands above are for orgs not using CodeDeploy at all.

### 6. Log rotation + CloudWatch Agent

No CFN stack here, so there's no `LogGroupName` output to copy from —
create the log group yourself (any name; the CFN path names it
`/${NamePrefix}/app` automatically, but a manual deploy can use anything):

```bash
aws logs create-log-group --log-group-name /zamboni/app

# ZAMBONI_LOG_GROUP must be on the SAME command as `sudo`, not exported on
# a separate line first -- sudo starts a clean environment and drops
# anything merely exported beforehand.
sudo ZAMBONI_LOG_GROUP=/zamboni/app DEPLOY_DIR=/opt/zamboni LOG=/var/log/zamboni-deploy.log \
  bash deploy/scripts/configure_logging.sh
```

`deploy/scripts/after_install.sh` (the CodeDeploy path) already runs this on
every deployment and treats a rotation failure as fatal (see "Logging"
below). Orgs not using CodeDeploy must run it by hand on every release (or
wire it into whatever deploy mechanism replaces `after_install.sh`) —
otherwise `zamboni-api`/`zamboni-control-plane-sync`/
`zamboni-control-plane-backup`'s file-based logs will never rotate and can
grow unbounded, since they no longer fall back to the journal's own
retention.

---

## Control Plane (added post-Phase-6, SQLite-primary for config tables)

`stream_registry`/`hk_config`/`domain_registry`/`nonprod_registry`/
`controlm_jobs` are SQLite-primary in production now
(`engine/core/control_plane.py`) — the UI/API and engine both read/write
one SQLite file directly instead of round-tripping to Athena for every
config change. `execution_log`/`audit_log`/`vacuum_audit` are unaffected,
still 100% Athena.

**This needs no CFN/IAM changes** — the existing `MetadataBucketName` S3
grant and the existing Athena/Glue permissions already cover the
sync/backup daemons below. What it DOES need, on every EC2 instance:

- `/data/zamboni/` — the persistent home for the control-plane SQLite file,
  **outside** `/opt/zamboni` (which CodeDeploy wipes on every revision).
  `deploy/scripts/after_install.sh` creates this unconditionally on every
  deploy (`mkdir -p /data/zamboni`), then runs
  `scripts/init_control_plane_db.py` (idempotent — creates the 5 tables if
  missing, applies any pending column migrations).
- `.env`'s `ZAMBONI_CONTROL_PLANE_DB` **must** be an absolute path under
  `/data/zamboni/` (i.e. `/data/zamboni/zamboni_control.db`) — see the
  warning in `.env.example`. A bare/relative filename would resolve
  inside `/opt/zamboni` and silently lose every registered domain/table/
  policy on the next deploy. There's no code-level default that protects
  against this — it depends entirely on `.env` being set correctly, which
  is exactly what the smoke test's `control_plane_db` check now verifies
  (see below).
- Three systemd units, all installed/enabled by `after_install.sh`, no
  manual step needed on the CFN/CodeDeploy path:
  `zamboni-control-plane-sync` (pushes the SQLite DB to real Athena on an
  interval, one-way, full-table overwrite — no MERGE, since Trino MERGE
  can't express a SQLite-side DELETE), `zamboni-control-plane-backup`
  (`VACUUM INTO` → S3 under `control-plane-backups/`, hourly/daily
  retention), `zamboni-control-plane-integrity` (daily `PRAGMA
  integrity_check` timer, alerts via SNS on failure). All three intervals
  are tunable live via Settings → Advanced, no redeploy needed.
- `app_start.sh`'s control-plane DB liveness check (`SELECT 1`) is
  **fatal** — unlike the pre-existing Athena connectivity check, which
  stays a warning. This file is primary storage now, not a fallback.

## Post-deploy validation

```bash
python scripts/aws_smoke_test.py                        # every check should PASS
python scripts/aws_smoke_test.py --create-lock-table     # if dynamodb_lock_table FAILs the first time
python scripts/aws_smoke_test.py --init-control-plane-db # if control_plane_db FAILs because the DB/tables don't exist yet
curl http://localhost:8000/api/system/mode               # FastAPI up
```

Pay particular attention to the `control_plane_db` check specifically —
it FAILs if `ZAMBONI_CONTROL_PLANE_DB` resolves inside `/opt/zamboni`,
which is the exact silent-data-loss trap described above. A PASS here
means the control-plane DB is both reachable and correctly placed outside
CodeDeploy's wipe zone, not just "the file happens to exist right now."

---

## Logging (added 2026-07-12, corrected same day after review)

All Zamboni logs on the instance live under `/var/log/zamboni/` (plus one
outlier, `/var/log/zamboni-deploy.log` — the CodeDeploy hook log, kept at
that path for historical reasons rather than moved into the directory):

| File | Written by |
|---|---|
| `bootstrap.log` | EC2 UserData (one-time, at instance launch/replacement) |
| `hk.log`, `archival.log`, `lifecycle_scan.log`, `lifecycle.log`, `cleanup.log` | The 5 EventBridge → SSM Run Command engine entry points |
| `api.log`, `control-plane-sync.log`, `control-plane-backup.log`, `control-plane-integrity.log` | The 4 systemd services/timer (`StandardOutput`/`StandardError` = `append:...`, not `journal`) |
| `/var/log/zamboni-deploy.log` | `before_install.sh`/`after_install.sh`/`app_start.sh` |

`journalctl -u zamboni-api` (etc.) still shows unit lifecycle events
(start/stop/restart, exit codes) but no longer the app's own stdout —
that's only in the file now. The directory itself (`/var/log/zamboni`) is
root-owned, not `ec2-user`-owned, even though the services above run as
`ec2-user` — systemd (running as root) opens each `append:` file before
dropping to `User=ec2-user`, so the service process never needs write
permission on the directory itself; keeping it root-owned means a
compromised app process can't unlink/replace another service's log file
(Unix directory write permission, not file ownership, governs delete/rename).

**Setup lives in `deploy/scripts/configure_logging.sh`, called from
`after_install.sh` — not EC2 UserData.** UserData only executes once, at
first boot; an ordinary CodeDeploy push to an already-running instance (the
normal, every-day deploy path — see "CloudFormation vs. ongoing code
deploys" above) never re-runs it, so a setup step living only in UserData
would never reach an instance that's already up. `after_install.sh` runs on
*every* deployment, the first one on a fresh instance included, so this is
what actually lets the setup reach existing instances on their next
ordinary deploy, no instance replacement required.

**CloudWatch Agent**: `configure_logging.sh` installs
`amazon-cloudwatch-agent` and writes its config
(`/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json`) —
tails every file above into the CloudWatch Logs group named by the `.env`
variable `ZAMBONI_LOG_GROUP` (copy this from the stack's `LogGroupName`
output — not auto-injected, same manual-copy convention as the bucket/SNS
values above), one stream per file named `<instance-id>/<name>`. Retention
is the stack's `LogRetentionDays` parameter (default 30 days). The instance
role has `logs:CreateLogGroup`/`CreateLogStream`/`PutLogEvents`/
`DescribeLogStreams`/`DescribeLogGroups` (the last one closes a real gap —
AWS's own `CloudWatchAgentServerPolicy` includes it and its absence can
cause silent partial failures the agent's own exit code won't reveal).
Skips CloudWatch Agent configuration (logging a clear warning, but still
completing the logrotate setup below) if `ZAMBONI_LOG_GROUP` isn't set.
Configures the agent with `-a append-config`, not `fetch-config` —
`fetch-config` would REPLACE the agent's entire running configuration with
only what Zamboni writes, silently erasing any org-managed metrics/log
sources already configured on the instance; `append-config` merges
Zamboni's `collect_list` into whatever's already there. Non-fatal end to
end — check `sudo systemctl status amazon-cloudwatch-agent` and
`/var/log/zamboni-deploy.log` if a log group/stream doesn't appear in
CloudWatch after a deploy.

**Local rotation**: `/etc/logrotate.d/zamboni` (also written by
`configure_logging.sh`, driven by AL2023's own `logrotate.timer` — enabled
directly rather than installing `cronie` and relying on
`/etc/cron.daily/logrotate`, which AL2023 doesn't reliably ship) rotates
every file above on the earlier of `weekly` or `maxsize 200M` (the size
trigger matters because `logrotate.timer` checks daily by default — without
it, a crash loop writing continuously could fill the root volume well
before the next weekly rotation), keeps 8 rotations compressed, and uses
`copytruncate` for the 3 *continuously-running* systemd daemons
specifically — `api.log`, `control-plane-sync.log`,
`control-plane-backup.log` (they hold their log fd open for the life of the
process — a plain rename+recreate rotation would leave them writing to the
old, now-renamed inode forever; `copytruncate` truncates in place instead,
no service restart needed). `control-plane-integrity.log` does *not* need
`copytruncate` despite also being systemd-managed — it's a `Type=oneshot`
unit fired by a daily `.timer`, so (like the 5 engine-script logs and
`bootstrap.log`/`zamboni-deploy.log`) it opens its log file fresh on every
invocation. CloudWatch Logs is the centralized, off-instance copy that
survives even if this instance is replaced/lost -- that's the reason it
matters, not necessarily a longer retention window (8 weekly rotations
kept locally can span longer than CloudWatch's 30-day default retention).
Verify with `sudo logrotate -d /etc/logrotate.d/zamboni` (dry run) and
`systemctl status logrotate.timer`.

**Rotation setup is FATAL, unlike the CloudWatch Agent above** — corrected
2026-07-12 after review. `zamboni-api`/`zamboni-control-plane-sync`/
`zamboni-control-plane-backup` log only to a file now (no journal
fallback), so an unconfigured logrotate would let those files grow
unbounded and risk filling the root volume. `configure_logging.sh` exits 1
if installing `logrotate`, writing `/etc/logrotate.d/zamboni`, or enabling
`logrotate.timer` fails, and `after_install.sh` no longer swallows that
with `|| echo WARNING (non-fatal)` — a rotation failure now fails the
whole `AfterInstall` hook and the CodeDeploy deployment with it.

---

## Streamlit decommission (complete, 2026-07-11)

The cutover this section used to describe as a pending, sign-off-gated
checklist has happened: Streamlit is fully decommissioned, not just
disabled. `app/`, `.streamlit/`, `deploy/systemd/zamboni-streamlit.service`,
the `zamboni-app` systemd unit creation in `after_install.sh`, the `:8501`
security group rule in `zamboni-cfn.yaml`, and the `streamlit`/`plotly`/
`itables` entries in `requirements.txt` were all removed outright (not
archived) — the React/FastAPI stack is the only UI, on any deployed
instance going forward.

If you're deploying an EC2 instance from before this change and need to
retire an existing `zamboni-app` service by hand:
```bash
sudo systemctl stop zamboni-app
sudo systemctl disable zamboni-app
sudo rm /etc/systemd/system/zamboni-app.service
sudo systemctl daemon-reload
```
Then redeploy from this commit onward so `zamboni-cfn.yaml`'s security
group no longer opens `:8501` at all.
