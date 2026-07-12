# Deploy

CI/CD configuration files — generated in Phase 2, extended in Phase 6 with
the FastAPI/React service and a complete standalone CloudFormation stack.

## Files (Phase 2)

| File | Purpose |
|---|---|
| `buildspec.yml` | CodeBuild — install, test, build UI, package |
| `appspec.yml` | CodeDeploy — lifecycle hooks |
| `scripts/before_install.sh` | Stop the API + control-plane services, backup .env |
| `scripts/after_install.sh` | Restore .env, pip install (+ venv for the API service), install systemd units, permissions |
| `scripts/app_start.sh` | Start the API + control-plane services, validate connectivity |
| `iam_policy.json` | EC2 instance role — superseded by `zamboni-cfn.yaml`'s inline role for new deploys |
| `pipeline_config.md` | Manual DO-team setup instructions (superseded by `zamboni-cfn.yaml` — kept for reference/comparison; predates the FastAPI/React replatform, describes a different pipeline topology) |

## Files (Phase 6 — contracts.md §10 R10.3)

| File | Purpose |
|---|---|
| `zamboni-cfn.yaml` | Complete standalone CFN stack: EC2 + IAM + DynamoDB lock table + security group + CodePipeline/CodeBuild/CodeDeploy skeleton. Parameterized for org adaptation. |
| `systemd/zamboni-api.service` | FastAPI/uvicorn unit (:8000), installed by `after_install.sh` |

Run `cfn-lint deploy/zamboni-cfn.yaml` before any deploy — CI gate, zero
errors required (warnings reported).

## SQL Setup (one-time)

Run these in order in Athena after first deploy:

```
1. sql/create_domain_registry.sql
2. sql/create_stream_registry.sql
3. sql/create_hk_config.sql
4. sql/create_execution_log.sql
5. sql/create_nonprod_registry.sql
```

Remember to replace `s3://your-zamboni-metadata-bucket/` in each SQL file
with your actual S3 bucket path before running.
