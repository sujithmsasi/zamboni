# Zamboni — Pipeline Setup Guide for DO Team

This document describes the one-time AWS infrastructure setup required to enable the Zamboni CI/CD pipeline.

---

## Overview

Zamboni uses the following AWS services for deployment:

| Service | Purpose |
|---|---|
| CodeStar Connection | Detects pushes to GitHub and triggers CodePipeline automatically |
| CodePipeline | Orchestrates the full deploy flow |
| CodeBuild | Runs integration tests and packages the artifact |
| CodeDeploy | Copies artifact to EC2 and runs lifecycle hooks |
| EC2 (t3.large) | Runs the Zamboni engine (scheduled) and the app (always-on service) |
| SSM Parameter Store | Stores environment config — dev and prod values separately |
| SNS | Sends approval notification email for prod deploys |

---

## Branch → Environment Mapping

| Branch | Environment | Deploy Method |
|---|---|---|
| `dev` | Dev EC2 | Automatic — no approval needed |
| `main` | Prod EC2 | Requires manual DO team approval in AWS Console |

---

## Step 1 — EC2 Setup

### 1a. Launch EC2 Instances
Launch two EC2 instances (or reuse existing):

| Property | Dev | Prod |
|---|---|---|
| Name tag | `zamboni-dev` | `zamboni-prod` |
| Instance type | t3.large | t3.large |
| OS | Amazon Linux 2023 | Amazon Linux 2023 |
| IAM Instance Profile | `zamboni-ec2-role` (created in Step 2) | `zamboni-ec2-role` |

**Important:** Both instances must have the tag `Name=zamboni-dev` and `Name=zamboni-prod` respectively. CodeDeploy uses these tags to target the correct instance.

### 1b. Install CodeDeploy Agent
Run on both EC2 instances:

```bash
# Amazon Linux 2023
sudo yum update -y
sudo yum install -y ruby wget

cd /home/ec2-user
wget https://aws-codedeploy-us-west-2.s3.us-west-2.amazonaws.com/latest/install
chmod +x install
sudo ./install auto

sudo systemctl start codedeploy-agent
sudo systemctl enable codedeploy-agent
sudo systemctl status codedeploy-agent
```

### 1c. Install Python 3.11
```bash
sudo yum install -y python3.11 python3.11-pip
python3.11 --version   # should print 3.11.x
```

### 1d. Create Deploy Directory
```bash
sudo mkdir -p /opt/zamboni
sudo chown ec2-user:ec2-user /opt/zamboni
```

### 1e. Create .env File (Dev EC2)
```bash
cp /opt/zamboni/.env.example /opt/zamboni/.env
# Edit with actual dev values
nano /opt/zamboni/.env
```

### 1f. Create .env File (Prod EC2)
Same as above with production values.

---

## Step 2 — IAM Role

### 2a. Create EC2 Instance Role
1. Go to IAM → Roles → Create Role
2. Trusted entity: EC2
3. Role name: `zamboni-ec2-role`
4. Attach an inline policy using the contents of `deploy/iam_policy.json`
5. **Replace all placeholders** in `iam_policy.json`:
   - `ACCOUNT_ID` → your AWS account ID
   - `your-athena-results-bucket` → actual bucket name
   - `your-staging-bucket` → actual staging bucket
   - `your-archive-bucket` → actual archive bucket
   - `your-zamboni-metadata-bucket` → actual metadata bucket

### 2b. Attach Role to Both EC2 Instances
EC2 → Actions → Security → Modify IAM Role → Select `zamboni-ec2-role`

---

## Step 3 — SSM Parameter Store

CodeBuild reads environment config from SSM (no hardcoded values in buildspec).
Create the following parameters under `/zamboni/dev/`:

```
/zamboni/dev/ATHENA_RESULTS_BUCKET    → s3://your-athena-results-bucket/zamboni/
/zamboni/dev/STAGING_BUCKET           → s3://your-staging-bucket
/zamboni/dev/ARCHIVE_BUCKET           → s3://your-archive-bucket
/zamboni/dev/ZAMBONI_METADATA_BUCKET  → s3://your-zamboni-metadata-bucket
/zamboni/dev/SNS_ALERT_TOPIC_ARN      → arn:aws:sns:us-west-2:ACCOUNT_ID:zamboni-alerts
/zamboni/dev/SNS_GREENZONE_TOPIC_ARN  → arn:aws:sns:us-west-2:ACCOUNT_ID:zamboni-greenzone
```

Use type `String` for bucket names/ARNs. Use `SecureString` for any passwords.

---

## Step 4 — SNS Topics

Create two SNS topics:

| Topic Name | Purpose |
|---|---|
| `zamboni-alerts` | Engine failure alerts, circuit breaker trips |
| `zamboni-greenzone` | Non-prod table GREENZONE notifications to domain owners |

Subscribe the team distribution list email to `zamboni-alerts`.

---

## Step 5 — CodeDeploy

### 5a. Create Application
- CodeDeploy → Applications → Create Application
- Application name: `zamboni`
- Compute platform: EC2/On-premises

### 5b. Create Deployment Group — Dev
- Name: `zamboni-dev`
- Service role: create a CodeDeploy service role with `AWSCodeDeployRole` managed policy
- Deployment type: In-place
- Environment: Amazon EC2 instances
- Tag key: `Name` / Value: `zamboni-dev`
- Deployment config: `CodeDeployDefault.AllAtOnce`
- Load balancer: Disable (no ALB in Phase 1)

### 5c. Create Deployment Group — Prod
- Name: `zamboni-prod`
- Same as above but tag value: `zamboni-prod`

---

## Step 6 — CodeBuild

### 6a. Create CodeBuild Project
- Project name: `zamboni-build`
- Source: CodePipeline (artifact passed from pipeline)
- Environment:
  - Managed image: Amazon Linux 2023
  - Runtime: Standard
  - Image: `aws/codebuild/amazonlinux2-x86_64-standard:5.0`
  - Privileged: No
- Buildspec: Use buildspec file → `deploy/buildspec.yml`
- Service role: create a role with these policies:
  - `AmazonSSMReadOnlyAccess` (for parameter-store in buildspec)
  - `AmazonS3ReadOnlyAccess` (for artifact bucket)
  - `AthenaFullAccess` (for integration tests)
  - `AWSGlueReadOnlyAccess`

---

## Step 7 — CodePipeline

### Pipeline 1 — Dev Pipeline (auto-deploy on dev branch)

**Name:** `zamboni-dev-pipeline`

| Stage | Action | Config |
|---|---|---|
| Source | GitHub (CodeStar) | Repo: `sujithmsasi/zamboni`, Branch: `dev` |
| Build | CodeBuild | Project: `zamboni-build` |
| Deploy | CodeDeploy | App: `zamboni`, Group: `zamboni-dev` |

### Pipeline 2 — Prod Pipeline (merge to main → approval → prod)

**Name:** `zamboni-prod-pipeline`

| Stage | Action | Config |
|---|---|---|
| Source | GitHub (CodeStar) | Repo: `sujithmsasi/zamboni`, Branch: `main` |
| Build | CodeBuild | Project: `zamboni-build` |
| Deploy Dev | CodeDeploy | App: `zamboni`, Group: `zamboni-dev` |
| Approval | Manual Approval | SNS topic: `zamboni-alerts`, message: "Review and approve Zamboni prod deploy" |
| Deploy Prod | CodeDeploy | App: `zamboni`, Group: `zamboni-prod` |

---

## Step 8 — Verify CodeStar Connection

Check that the existing CodeStar connection to GitHub is active:
- Developer Tools → Connections
- Find the connection to `sujithmsasi` GitHub account
- Status must be **Available**

If not available, re-authorize it.

---

## Step 9 — First Deploy Verification

After all setup is complete:

1. Make a trivial change to the `dev` branch (e.g. add a blank line to README)
2. Push to GitHub
3. Verify CodePipeline `zamboni-dev-pipeline` triggers automatically
4. Watch CodeBuild logs — confirm tests pass
5. Watch CodeDeploy — confirm deployment to dev EC2 succeeds
6. SSH to dev EC2 and verify:

```bash
# Check the app service
systemctl status zamboni-api

# Check deploy log
tail -50 /var/log/zamboni-deploy.log

# Check app is accessible
curl http://localhost:8000/api/system/mode
```

---

## Checklist

- [ ] Dev EC2 launched and tagged `Name=zamboni-dev`
- [ ] Prod EC2 launched and tagged `Name=zamboni-prod`
- [ ] CodeDeploy agent installed on both EC2s
- [ ] Python 3.11 installed on both EC2s
- [ ] `/opt/zamboni` directory created on both EC2s
- [ ] `.env` file created on both EC2s
- [ ] IAM role `zamboni-ec2-role` created and attached to both EC2s
- [ ] SSM parameters created under `/zamboni/dev/`
- [ ] SNS topics `zamboni-alerts` and `zamboni-greenzone` created
- [ ] CodeDeploy application `zamboni` created
- [ ] Deployment groups `zamboni-dev` and `zamboni-prod` created
- [ ] CodeBuild project `zamboni-build` created
- [ ] CodePipeline `zamboni-dev-pipeline` created
- [ ] CodePipeline `zamboni-prod-pipeline` created with approval gate
- [ ] CodeStar connection to GitHub verified as Available
- [ ] First test deploy completed successfully

---

## Contact

Any issues with this setup — contact the Zamboni dev team.
