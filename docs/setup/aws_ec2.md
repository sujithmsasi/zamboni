# aws_ec2 Mode Setup (production deployment)

The real, deployed instance — either the CloudFormation path (preferred, one
command) or the manual path. **This page is only the shortest path to a
first working instance.** For the full installation walkthrough — IAM
specifics, security group rules, the CodePipeline/CodeBuild/CodeDeploy
wiring, the GitHub connection setup and its most common failure mode, the
Streamlit cutover checklist — see **`docs/deployment/ec2_api_deploy.md`**,
the dedicated deployment-operations reference. Once the instance is up,
**`docs/deployment/data_operations_guide.md`** is the next step — it covers
everything after "the app is running": scheduling the engines, registering
domains/tables, configuring policies, validating with a dry run, and going
live safely.

See `docs/SETUP_GUIDE.md` for how this fits alongside `local` / `aws_local`
mode if you haven't already picked one.

## 1. Lint the CFN template — zero errors required

```bash
cfn-lint deploy/zamboni-cfn.yaml
```

## 2. Deploy

```bash
aws cloudformation deploy \
  --template-file deploy/zamboni-cfn.yaml \
  --stack-name zamboni \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      NamePrefix=zamboni VpcId=vpc-xxxxxxxx SubnetId=subnet-xxxxxxxx \
      SourceCidr=10.0.0.0/8 \
      AthenaResultsBucketName=... StagingBucketName=... \
      ArchiveBucketName=... MetadataBucketName=... \
      SnsAlertTopicArn=... SnsGreenzoneTopicArn=...
```

This provisions the EC2 instance, the DynamoDB lock table, the dedicated
control-plane EBS volume, the security group, a CloudWatch log group, and
(if `GitHubConnectionArn` is supplied) the CodePipeline/CodeBuild/CodeDeploy
stack. Full parameter list and resource detail in `ec2_api_deploy.md`.

**Pin the AMI on every deploy after the first** — the `AmiId` SSM parameter
resolves to whatever AL2023 build is current *at deploy time*, and changing
it forces CloudFormation to replace the EC2 instance. Use the stack's
`ResolvedAmiId` output as an explicit `--parameter-overrides AmiId=...` on
every subsequent deploy to avoid an unplanned instance replacement. See
`ec2_api_deploy.md`'s "CloudFormation vs. ongoing code deploys" section for
why this matters and what an unplanned replacement costs you (control-plane
data, if the dedicated EBS volume's own protection somehow doesn't apply —
see below).

## 3. Set up `.env` (first deploy only)

`deploy/scripts/after_install.sh` copies `.env.example` as a starting
template if no backed-up `.env` exists yet — **you must edit
`/opt/zamboni/.env` with real values before the app will work correctly.**

**The one setting that matters most and is easiest to get wrong:**
`ZAMBONI_CONTROL_PLANE_DB` in `.env` must be
`/data/zamboni/zamboni_control.db` — an absolute path, outside
`/opt/zamboni`. `deploy/scripts/after_install.sh` mounts a dedicated,
retained EBS volume at `/data/zamboni` on every deploy specifically so the
control-plane database (every registered domain, table, and policy)
survives across deploys — but nothing forces `.env` to actually point
there. Get this wrong and the first deploy looks fine; the *second* deploy
silently starts against an empty database, because CodeDeploy wipes
`/opt/zamboni` on every revision.

## 4. Validate

```bash
python scripts/aws_smoke_test.py --create-lock-table --init-control-plane-db
```

Every check should PASS. If `control_plane_db` fails with a message about
`/opt/zamboni`, fix `.env` and redeploy before doing anything else.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Second deploy starts with an empty control plane (no domains/tables/policies) | `.env`'s `ZAMBONI_CONTROL_PLANE_DB` resolves inside `/opt/zamboni`, which CodeDeploy wipes every revision | Set it to `/data/zamboni/zamboni_control.db`; `aws_smoke_test.py`'s `control_plane_db` check catches this specifically |
| EC2 instance replaced unexpectedly on an unrelated stack update | `AmiId`'s SSM parameter resolved a newer AMI than last deploy, forcing a replacement | Always pass `AmiId=<ResolvedAmiId output>` from the previous deploy on every subsequent one |
| CodePipeline never triggers / GitHub connection stuck `PENDING` | GitHub org restricting third-party App installs, needs an org owner's approval — CloudFormation cannot complete this handshake itself | See `ec2_api_deploy.md`'s "GitHub connection setup & troubleshooting" section; the base infra + a manual first deploy work independently of this being resolved |
| CodeDeploy `BeforeInstall` fails with "agent was not able to receive the lifecycle event" | UserData bootstrap failed silently on a replaced instance (pre-2026-07-10) | Fixed — UserData now fails loud with a `Zamboni/BootstrapSuccess` CloudWatch metric; check `/var/log/zamboni/bootstrap.log` on the instance |

**Pulling this into an org's own AWS account/repo?** See
`docs/ORG_DROP.md`'s Phase 7 checklist — it's the same steps above, plus the
org-specific parameter adaptation and the no-merge branch strategy.

---

Other modes: [local](local.md) · [aws_local](aws_local.md) · back to
[the setup index](../SETUP_GUIDE.md)
