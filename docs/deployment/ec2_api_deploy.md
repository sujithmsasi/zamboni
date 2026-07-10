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
| App process | `zamboni-app` (Streamlit, :8501) only | `zamboni-app` (:8501, fallback) **+** `zamboni-api` (FastAPI/uvicorn, :8000, primary) |
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

`zamboni-app` (Streamlit, :8501) is **untouched** — both services run side
by side until the cutover checklist below is signed off (contracts.md §8).

`deploy/scripts/before_install.sh`/`after_install.sh`/`app_start.sh` (used
by the CodeDeploy path) already do steps 5 automatically as of Phase 6 —
the manual commands above are for orgs not using CodeDeploy at all.

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
curl http://localhost:8501/_stcore/health                # Streamlit fallback still up
```

Pay particular attention to the `control_plane_db` check specifically —
it FAILs if `ZAMBONI_CONTROL_PLANE_DB` resolves inside `/opt/zamboni`,
which is the exact silent-data-loss trap described above. A PASS here
means the control-plane DB is both reachable and correctly placed outside
CodeDeploy's wipe zone, not just "the file happens to exist right now."

---

## Cutover checklist (post-showcase — do NOT execute before sign-off)

Per contracts.md §8: "Streamlit unit untouched until cutover sign-off."
This is intentionally a checklist, not a script — each step is a judgment
call that needs a human to confirm the previous step actually held.

- [ ] **Confirm parity for 1 week.** Run both `zamboni-app` (:8501) and
      `zamboni-api` (:8000) side by side against the same production data.
      Watch for any workflow, report, or number that the React app doesn't
      match — not just "does it load," but "does every number agree."
- [ ] **Stop and disable the Streamlit service.**
      ```bash
      sudo systemctl stop zamboni-app
      sudo systemctl disable zamboni-app
      ```
- [ ] **Remove the :8501 security group rule** (CFN: remove the ingress
      block from `deploy/zamboni-cfn.yaml` and redeploy the stack; manual:
      `aws ec2 revoke-security-group-ingress --port 8501 ...`).
- [ ] **Archive `app/pages/` with a README pointer** — don't delete the
      Streamlit source outright (it's the documented fallback in the
      showcase runbook's "last resort" step until this checklist is
      signed off). Once cutover is confirmed, move `app/` to
      `app_legacy_streamlit/` (or similar) and leave a one-line
      `app/README.md` pointing at the React app's routes and this
      checklist as the reason it moved.
- [ ] Update `.claude/CLAUDE.md`'s Repo Structure table and this file to
      reflect the archived location.
