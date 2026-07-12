# ORG_DROP.md — Clean Drop Delivery Protocol (contracts §10)
# Decided 2026-07-05: no merging with the old org branch. Time-optimal path.
# Updated Phase 6 (2026-07-08): the CFN, smoke test, and systemd units this
# checklist references now actually exist in deploy/ and scripts/ — see the
# "Phase 6 checklist" section below for the concrete adaptation steps.
# Updated 2026-07-09: SQLite control plane migration added a required
# manual .env step (4a) and a new smoke-test check -- see "Control Plane"
# in docs/deployment/ec2_api_deploy.md for the full detail.

## Delivery
1. Finish Phases 0–6 on the personal repo; final commit pushed to public dev.
2. Transfer the codebase to the org laptop (approved channel; it is
   program-generated code — no org IP round-trip involved).
3. Run 07_org_drop.md with Bedrock Sonnet (5 if available, else 4.6):
   new branch `new-phase1` → drop → adapt CFN params → deploy → smoke test.

## What is intentionally left behind (re-port backlog, post-showcase, org-side)
See .claude/org_divergence.md — vacuum 12-gap-type hardening, Parquet log
writer, CloudTrail signals, old CFN conventions. Bedrock Claude on the org
laptop can read BOTH branches to re-port these into new-phase1 later.

## Rollback story
The old org branch remains untouched and deployable — it IS the rollback.

## Phase 7 checklist (R10.4) — concrete steps against what Phase 6 shipped

1. **New branch.** `git checkout -b new-phase1` on the org repo. No merge,
   no rebase against the retired org branch (R10.2).
2. **Drop the code.** Copy the full personal-repo tree into `new-phase1`
   as-is — nothing here is org-specific yet.
3. **Adapt `deploy/zamboni-cfn.yaml`** (the one file this whole checklist
   exists for): set `NamePrefix`, `VpcId`, `SubnetId`, `SourceCidr` to org
   conventions; supply the org's real `AthenaResultsBucketName`/
   `StagingBucketName`/`ArchiveBucketName`/`MetadataBucketName`/
   `SnsAlertTopicArn`/`SnsGreenzoneTopicArn`; create the org's
   CodeStar/CodeConnections GitHub authorization manually (console-only,
   CFN can't do this step) and pass its ARN as `GitHubConnectionArn`.
   Run `cfn-lint deploy/zamboni-cfn.yaml` again after editing — zero
   errors required before deploy, same gate Phase 6 used.
4. **Deploy.** `aws cloudformation deploy --template-file
   deploy/zamboni-cfn.yaml --stack-name <org-name> --capabilities
   CAPABILITY_NAMED_IAM --parameter-overrides ...` (see
   `docs/deployment/ec2_api_deploy.md`'s "CloudFormation path" for the
   full parameter list). First CodeDeploy run installs `zamboni-api`
   (FastAPI, the only app process — Streamlit was decommissioned
   2026-07-11) — `deploy/scripts/after_install.sh` handles this, plus
   `/data/zamboni` provisioning and the 3 `zamboni-control-plane-*`
   services (see `docs/deployment/ec2_api_deploy.md`'s "Control Plane"
   section).
4a. **Set `ZAMBONI_CONTROL_PLANE_DB` in the instance's `.env`** to
   `/data/zamboni/zamboni_control.db` (absolute path, must stay outside
   `/opt/zamboni`) — this is a manual `.env` edit, not something the CFN
   or CodeDeploy scripts set for you. Getting this wrong doesn't fail
   loudly on first deploy; it silently loses every registered domain/
   table/policy on the *second* deploy, once CodeDeploy wipes `/opt/
   zamboni` again. Step 5's smoke test is what actually catches this if
   missed.
5. **Smoke test.** `python scripts/aws_smoke_test.py --create-lock-table
   --init-control-plane-db` — every check (STS, Glue, Athena, S3, SNS,
   DynamoDB lock table, one live `GetTableOptimizer` call, and the
   SQLite control-plane DB) should PASS against the org's real AWS
   account. This is the "did the adaptation actually work" gate, not just
   "did the stack finish creating" — and it's the gate that specifically
   catches step 4a if it was missed or set wrong.
6. **No merge, ever.** If something in `new-phase1` needs a fix, fix it on
   `new-phase1` directly. The old org branch stays untouched as the
   rollback (see above) for the duration of the parity-confirmation
   window described in `docs/deployment/ec2_api_deploy.md`'s cutover
   checklist.
