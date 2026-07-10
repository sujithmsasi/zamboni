# Zamboni — Setup Guide

Read this first. It's the entry point for getting Zamboni running in any of
its three modes — pick the one that matches what you're trying to do, then
follow that section. Each section links out to the deeper existing doc for
anything beyond first-run setup (deploy internals, cutover, org-drop
adaptation) rather than repeating it here.

## Which mode do I want?

Zamboni resolves its runtime mode via `config/settings.py::get_mode()`,
either from `ZAMBONI_MODE` directly or (for backward compatibility) from
`ZAMBONI_LOCAL_MODE=true`. There are exactly three:

| Mode | `ZAMBONI_MODE` | What it talks to | When to use it |
|---|---|---|---|
| **Local** | `local` (or `ZAMBONI_LOCAL_MODE=true`) | Nothing — a seeded SQLite file stands in for both Athena and the control plane | First-time setup, offline development, trying out the UI with realistic demo data, no AWS account needed |
| **aws_local** | `aws_local` | Real AWS, from your own laptop, via an SSO profile | Demoing against real data without deploying anything; testing a change against real Athena/Glue before pushing |
| **aws_ec2** | `aws_ec2` (also the default when neither env var is set) | Real AWS, from a deployed EC2 instance, via an IAM instance role | Production / the org's actual running deployment |

All three run the exact same code — nothing is mode-specific except which
credentials/storage backend `config/settings.py` and
`engine/core/control_plane.py` resolve to.

---

## 1. Local Mode (no AWS required)

The fastest way to see the whole app. A local SQLite file
(`zamboni_local.db`) stands in for everything — Athena tables, the control
plane, all of it — pre-seeded with realistic demo data (17 tables across 4
domains, execution history, cost data, the works).

```bash
# One-time: create + seed the local database
python scripts/seed_local_db.py

# Windows: build + serve FastAPI + the React UI in one step
run_local_api.bat

# Or manually, any OS:
cd ui && npm ci && npm run build && cd ..
set ZAMBONI_MODE=local
set ZAMBONI_LOCAL_MODE=true
set ZAMBONI_LOCAL_DB=zamboni_local.db
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` — login with the demo credentials shown on the
login screen itself (there's no real secret here to protect, see
`ui/src/pages/Login/`).

**Dev mode** (hot-reload, editing the React source): run the backend with
`--reload` on :8000 and `cd ui && npm run dev` separately on :5173 — the
Vite dev server proxies API calls to :8000 via CORS
(`api/main.py`'s allowed-origins list already includes it).

**Streamlit fallback** (legacy, being phased out — see the cutover
checklist in `docs/deployment/ec2_api_deploy.md`):
```bash
streamlit run app/Home.py
```

If you ever need to wipe and re-seed: `python scripts/seed_local_db.py --reset`.

---

## 2. aws_local Mode (laptop demo against real AWS)

Runs from your own machine, but talks to a real AWS account via a named
profile — no EC2 instance needed. Useful for demos where the data needs to
be real, or for validating a change against real Athena/Glue before it goes
anywhere near a deployed instance. Despite the name, `AWS_SSO_PROFILE` works
with any named profile — SSO, role-chaining (`role_arn`/`source_profile`),
or static/session credentials — not only real SSO.

**Prerequisites:**
1. A named AWS CLI profile already configured and pointed at in
   `.env.aws_local`'s `AWS_SSO_PROFILE`. Easiest path:
   `powershell -ExecutionPolicy Bypass -File setup_aws_local_profile.ps1` —
   interactively creates/updates the profile (either from pasted
   credentials, or by chaining to an existing long-lived profile that can
   assume a role — recommended, since it survives session-token expiry
   across a multi-day window) and points `.env.aws_local` at it.
   Manual alternative: `aws configure sso --profile <name>`.
2. `cp .env.aws_local.example .env.aws_local` (skipped automatically by
   `setup_aws_local_profile.ps1` if it doesn't exist yet) and fill in the
   real bucket names, SNS topic ARNs, and table names for your AWS account
   (every other value in the template is a placeholder —
   `your-athena-results-bucket`, `123456789012`, etc.). **Never set this to
   `prod-toolsgenai-sso`** unless that specific cross-team profile is
   genuinely what you use — it's a Bedrock-only profile in some
   environments, not a Zamboni AWS account.

```powershell
# Windows — handles SSO login, loads .env.aws_local, builds the UI if
# missing, starts uvicorn on :8000, opens the browser
powershell -ExecutionPolicy Bypass -File run_aws_local.ps1

# Dev mode (hot-reload) — starts uvicorn --reload + npm run dev together
powershell -ExecutionPolicy Bypass -File run_ui_dev.ps1 -Mode aws_local
```

`ZAMBONI_CONTROL_PLANE_DB` in `.env.aws_local` can stay a bare filename
(`zamboni_control.db`) here — a laptop run never goes through CodeDeploy,
so there's no wipe-on-deploy risk. It gets created fresh in your working
directory on first write; delete it any time to start over.

Once running, verify connectivity end-to-end:
```bash
python scripts/aws_smoke_test.py
```
Every check should PASS (not SKIPPED — SKIPPED means it's actually still
running in local mode; check your env vars).

---

## 3. aws_ec2 Mode (production deployment)

The real, deployed instance — either the CloudFormation path (preferred,
one command) or the manual path. Full detail lives in
**`docs/deployment/ec2_api_deploy.md`** — this section is just the
shortest path to a working instance; read that doc for anything beyond
first deploy (IAM specifics, security group rules, the cutover checklist
for retiring the Streamlit fallback).

```bash
# 1. Lint the CFN template — zero errors required
cfn-lint deploy/zamboni-cfn.yaml

# 2. Deploy
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

# 3. On the instance: set up .env (first deploy only)
#    after_install.sh copies .env.example as a starting template if no
#    backed-up .env exists yet — YOU MUST edit /opt/zamboni/.env with real
#    values before the app will work correctly.
```

**The one setting that matters most and is easiest to get wrong:**
`ZAMBONI_CONTROL_PLANE_DB` in `.env` must be
`/data/zamboni/zamboni_control.db` — an absolute path, outside
`/opt/zamboni`. `deploy/scripts/after_install.sh` creates `/data/zamboni`
on every deploy specifically so the control-plane database (every
registered domain, table, and policy) survives across deploys — but
nothing forces `.env` to actually point there. Get this wrong and the
first deploy looks fine; the *second* deploy silently starts against an
empty database, because CodeDeploy wipes `/opt/zamboni` on every revision.

```bash
# 4. Validate — this is the step that actually catches the mistake above
python scripts/aws_smoke_test.py --create-lock-table --init-control-plane-db
```

Every check should PASS. If `control_plane_db` fails with a message about
`/opt/zamboni`, fix `.env` and redeploy before doing anything else.

**Pulling this into an org's own AWS account/repo?** See
`docs/ORG_DROP.md`'s Phase 7 checklist — it's the same steps above, plus
the org-specific parameter adaptation and the no-merge branch strategy.

---

## Where to go next

| Doc | What it covers |
|---|---|
| `docs/deployment/ec2_api_deploy.md` | Full EC2/CFN deploy detail, IAM specifics, the control plane's systemd services, the Streamlit cutover checklist |
| `docs/ORG_DROP.md` | Adapting this repo into a different org's AWS account — parameter list, no-merge branch strategy |
| `docs/demo/showcase_runbook.md` | A guided click-path through the app for demos, with a local-mode fallback for every step |
| The in-app **App Guide** (sidebar → Administration → App Guide, `/help`) | What every page in the running app actually does — the day-to-day reference once it's up |
| `.claude/CLAUDE.md` | The full, dated build history — every phase, every real bug found, every design decision and why |
