# Deploy

CI/CD configuration files — generated in Phase 2.

## Files (Phase 2)

| File | Purpose |
|---|---|
| `buildspec.yml` | CodeBuild — install, test, package |
| `appspec.yml` | CodeDeploy — lifecycle hooks |
| `scripts/before_install.sh` | Stop Streamlit service, backup .env |
| `scripts/after_install.sh` | Restore .env, pip install, permissions |
| `scripts/app_start.sh` | Restart Streamlit, validate connectivity |
| `iam_policy.json` | EC2 instance role — hand to DO team |
| `pipeline_config.md` | DO team setup instructions |

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
