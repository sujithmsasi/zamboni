# Zamboni — Setup Guide

Read this first. It's the entry point for getting Zamboni running in any of
its three modes — pick the one that matches what you're trying to do, then
follow the linked guide for that mode. Each mode now has its own
self-contained page (split out 2026-07-11, after this used to be one long
file cramming all three together) — this page is just the picker plus
pointers to what comes after first-run setup.

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
plane, all of it — pre-seeded with realistic demo data. No AWS account,
no profile, no infra. Start here if you're new to the repo.

**→ Full guide: [`docs/setup/local.md`](setup/local.md)**

---

## 2. aws_local Mode (laptop demo against real AWS)

Runs from your own machine but talks to a real AWS account via a named
profile — no EC2 instance needed. Requires Zamboni's AWS infra to already
exist in the target account (see `docs/ORG_DROP.md` if it doesn't yet). Six
steps end to end: AWS profile → both env files (`.env.aws_local` and
`.env` — yes, both matter, the guide explains why) → dependencies → smoke
test (with a description of every check) → start the app.

**→ Full guide: [`docs/setup/aws_local.md`](setup/aws_local.md)**

---

## 3. aws_ec2 Mode (production deployment)

The real, deployed instance — CloudFormation (preferred) or manual. Once
the instance is up, `docs/deployment/data_operations_guide.md` covers
everything after "the app is running": scheduling the engines, registering
domains/tables, policy config, dry-run validation, going live.

**→ Full guide: [`docs/setup/aws_ec2.md`](setup/aws_ec2.md)** (the
shortest path to a first working instance) **→ Installation / CodePipeline
internals: [`docs/deployment/ec2_api_deploy.md`](deployment/ec2_api_deploy.md)**
(IAM specifics, security groups, the full CodeBuild/CodeDeploy/CodePipeline
wiring, the GitHub connection setup — this is the deployment-operations
reference, not just a first-deploy shortcut)

---

## Where to go next

| Doc | What it covers |
|---|---|
| `docs/setup/local.md` | Local mode: quick start, dev mode, reset/troubleshooting |
| `docs/setup/aws_local.md` | aws_local mode: profile → both env files → dependencies → smoke test (per-check descriptions) → start app, troubleshooting |
| `docs/setup/aws_ec2.md` | aws_ec2 mode: shortest path to a first deployed instance |
| `docs/deployment/data_operations_guide.md` | End-to-end: infra deploy → EventBridge engine scheduling → registering domains/tables → policy config → dry-run validation → going live → monitoring → incident response |
| `docs/deployment/ec2_api_deploy.md` | Full EC2/CFN deploy detail, IAM specifics, the control plane's systemd services |
| `docs/ORG_DROP.md` | Adapting this repo into a different org's AWS account — parameter list, no-merge branch strategy |
| `docs/demo/showcase_runbook.md` | A guided click-path through the app for demos, with a local-mode fallback for every step |
| The in-app **App Guide** (sidebar → Administration → App Guide, `/help`) | What every page in the running app actually does — the day-to-day reference once it's up |
| `.claude/CLAUDE.md` | The full, dated build history — every phase, every real bug found, every design decision and why |
