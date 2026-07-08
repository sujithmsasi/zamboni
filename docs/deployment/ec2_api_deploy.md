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
`InstanceRoleArn`, `CodeDeployApplicationName`, `PipelineArtifactBucket`.

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

## Post-deploy validation

```bash
python scripts/aws_smoke_test.py                    # every AWS check should PASS
python scripts/aws_smoke_test.py --create-lock-table # if the lock table check FAILs the first time
curl http://localhost:8000/api/system/mode           # FastAPI up
curl http://localhost:8501/_stcore/health            # Streamlit fallback still up
```

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
