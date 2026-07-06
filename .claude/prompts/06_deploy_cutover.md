# PHASE 6 — Full CloudFormation, Demo Scripts, Smoke Test, Drop Prep

Context: engine hardened, API complete, all 13 React pages live. This phase
makes the stack runnable for the demo AND authors the complete deployment
stack fresh (contracts §10 R10.3) — the delivery to org is a clean new-branch
drop, so the CFN is written from scratch here, parameterized for org-side
adaptation. No org files are needed or referenced.

## Read first
.claude/contracts.md §8 + §10, scripts/create_lock_table.py, api/main.py
static mount, docs/runbooks/*, .claude/org_divergence.md, config/settings.py
(every env var the stack must supply).

## Tasks

### 1. Author the COMPLETE deploy stack (fresh, standalone)
Create `deploy/`:
- `zamboni-cfn.yaml` — complete template: EC2 (AL2023, t3.large param,
  encrypted gp3, instance profile), IAM role (Athena/Glue incl.
  GetTableOptimizer/S3/SNS/CloudWatch Logs + DynamoDB on the lock table per
  contracts §3.4), DynamoDB lock table (§3.1 verbatim, DeletionPolicy:
  Retain), security group (:8000 + :8501 from a SourceCidr parameter — never
  0.0.0.0/0), CloudWatch log group (retention param), and a
  CodePipeline/CodeDeploy skeleton (GitHub source param, CodeBuild project,
  deploy group by EC2 tag). Parameters block for: VpcId, SubnetId, SourceCidr,
  KeyName(optional), InstanceType, GitHubRepo/Branch, all bucket names,
  SNS topic ARNs, NamePrefix (org naming adaptation point). Outputs: instance
  id, SG id, lock table name. Comment header: "Org-side (Phase 7): set
  NamePrefix/tags/VPC params to org conventions before deploy."
- `buildspec.yml` — Python 3.11 deps + Node 20 + `cd ui && npm ci && npm run
  build` + pytest gate + artifact incl. ui/dist.
- `appspec.yml` + `deploy/hooks/` scripts — install venv, install/start
  zamboni-api.service (and zamboni-streamlit.service as optional fallback).

### 2. systemd units (deploy/systemd/zamboni-api.service; optional
### zamboni-streamlit.service fallback unit alongside)
```
[Unit]  Description=Zamboni API (FastAPI)  After=network.target
[Service] Type=simple User=ec2-user
WorkingDirectory=/home/ec2-user/zamboni
EnvironmentFile=/home/ec2-user/zamboni/.env
Environment=PYTHONPATH=/home/ec2-user/zamboni
ExecStart=/home/ec2-user/zamboni/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=on-failure RestartSec=10
[Install] WantedBy=multi-user.target
```
Paths use /home/ec2-user/zamboni conventions; Phase 7 adjusts if org differs.

### 3. Laptop demo scripts (contracts §8.4) — Windows PowerShell
- run_aws_local.ps1: check `aws sso login --profile $env:AWS_SSO_PROFILE`
  (default prod-toolsgenai-sso; login if get-caller-identity fails); load
  .env.aws_local into process env; if ui/dist missing → `npm ci; npm run build`;
  start uvicorn :8000; Start-Process http://localhost:8000. Ctrl-C clean.
- run_ui_dev.ps1: uvicorn :8000 (local OR aws_local by param) + `npm run dev`
  in parallel jobs, opens :5173.
- .env.aws_local.example: full template per contracts §2 + required bucket/SNS
  vars + ZAMBONI_MODE=aws_local + AWS_SSO_PROFILE + DRY_RUN_DEFAULT=true +
  APP_ENV=dev; ensure .gitignore covers .env.aws_local.
- run_local.bat parity: extend or add run_local_api.bat for pure-SQLite full
  stack (mode=local).

### 4. Smoke + demo collateral
- scripts/aws_smoke_test.py (closing the backlog item): checks per mode —
  sts identity, Glue list-databases, Athena SELECT 1 in ATHENA_WG_APP, S3 put+
  delete on results bucket, SNS get-topic-attributes, DynamoDB DescribeTable
  lock table, GetTableOptimizer on one registered fqn. Flags: --create-lock-table
  (delegates to create_lock_table.py), --json. Clear PASS/FAIL table output.
- docs/demo/showcase_runbook.md — the July-17 click path:
  1) run_aws_local.ps1 → Home (fleet numbers) →
  2) Health/Governance: Dual-Optimizer Risk Report + the incident narrative
     ("two uncoordinated systems; here's the proof") →
  3) Policy Config: Gate 0 override on one table (time-boxed, reasoned, audited) →
  4) Dry Run Viewer: gate summary incl. Gate 0 →
  5) LiveActivity: locks strip →
  6) Recovery story: `recover_metadata.py --dry-run` transcript slide +
     "72h floor = guaranteed rollback window" line →
  7) Bulk Control-M CSV import (domain-team workflow) →
  8) Fallback plan: ZAMBONI_MODE=local variant of every step (offline-safe),
     Streamlit :8501 as last resort.
- docs/deployment/ec2_api_deploy.md: delta guide vs the existing docx — the
  api service, :8000, build artifact, DynamoDB/IAM CFN changes, cutover
  checklist (post-showcase: confirm parity 1 week → stop/disable streamlit
  unit → remove :8501 SG rule → archive app/pages with a README pointer).

## Acceptance criteria
- pytest unit+api green; ruff clean; `npm run build` clean;
  `pip install cfn-lint --break-system-packages && cfn-lint deploy/zamboni-cfn.yaml`
  → zero errors (warnings reported).
- Local full-stack proof: run_local_api.bat path — uvicorn serves built UI at
  :8000 in mode=local; Home + Health render (describe).
- aws_smoke_test.py runs in mode=local (AWS checks reported SKIPPED cleanly) —
  paste output table; document the aws_local expected output.
- Demo runbook + deploy delta doc complete; .env.aws_local.example present and
  gitignored.
- Migration Progress final entry: "Program complete — demo-ready; cutover
  checklist pending post-showcase sign-off."

Also: write docs/ORG_DROP.md — the 1-page Phase 7 checklist (clean drop
protocol, see contracts §10 R10.4).

## Do NOT
- Reference or assume any org-repo file. This stack is standalone.
- Make :8000/:8501 public — SourceCidr parameter only.

Suggested commit: `feat(deploy): complete CFN stack, buildspec/appspec, aws_local demo scripts, smoke test, showcase runbook, org drop checklist`
