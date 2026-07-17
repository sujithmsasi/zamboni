# aws_local Mode Setup (laptop demo against real AWS)

Runs from your own machine, but talks to a real AWS account via a named
profile — no EC2 instance needed. Useful for demos where the data needs to
be real, or for validating a change against real Athena/Glue before it goes
anywhere near a deployed instance.

See `docs/SETUP_GUIDE.md` for how this fits alongside `local` / `aws_ec2`
mode if you haven't already picked one.

## The six steps

1. [Prerequisites](#1-prerequisites) — tools + AWS infra that must already exist
2. [Set up the AWS profile](#2-set-up-the-aws-profile) — `setup_aws_local_profile.ps1`
3. [Set up both env files](#3-set-up-both-env-files) — `.env.aws_local` **and** `.env`
4. [Install dependencies](#4-install-python--node-dependencies) — Python venv + Node
5. [Run the smoke test](#5-run-the-smoke-test) — confirm every AWS call actually works
6. [Start the app](#6-start-the-app) — `run_aws_local.ps1` / `run_ui_dev.ps1`

---

## 1. Prerequisites

- **Python 3.11+, Node 20+, AWS CLI v2**, all on `PATH`. Python runs the
  FastAPI backend and engine CLI; Node builds the React UI; the AWS CLI is
  what `setup_aws_local_profile.ps1`/`run_aws_local.ps1` shell out to for
  `aws sso login` / `aws sts get-caller-identity` / `aws configure`.
- **An AWS account with Zamboni's infra already provisioned** — Athena
  workgroups, the Glue `zamboni_catalog` database and its metadata tables,
  SNS topics, a DynamoDB lock table. This mode does not create AWS
  infrastructure, it only points the app at an account that already has it.

**One naming gotcha worth knowing before you touch any config**:
`glue_catalog` and `zamboni_catalog` are two different things, and only one
of them is an Athena concept. `zamboni_catalog` is the real Glue *database*
Zamboni's own metadata tables live in — every Athena query this app issues
addresses it as plain `zamboni_catalog.<table>` (2-part, resolved against
Athena's own default catalog, no catalog qualifier at all). `glue_catalog`
is **not** an Athena Data Catalog registration — it's the Spark/Iceberg
catalog name used only *inside* AWS Glue ETL jobs, when a table's
`compaction_engine='glue'` triggers a real Glue job. You will never need to
register anything called `glue_catalog` in Athena to make this app work.

## 2. Set up the AWS profile

```powershell
powershell -ExecutionPolicy Bypass -File setup_aws_local_profile.ps1
```

Interactive, one-time (re-run any time to refresh credentials or point at a
different account). It:

1. Asks for a profile name (default `zamboni-dev`) — convention is
   `zamboni-{env}` so the name says which AWS account it points at.
2. Asks which environment that account represents (`dev`/`preprod`/`prod`/
   `test`) — this becomes `APP_ENV`.
3. Asks how to authenticate, two options:
   - **Paste credentials** (Access Key ID / Secret / Session Token from the
     AWS Console's "Command line or programmatic access" panel) — simplest,
     but an assumed-role session token expires (often ~1h), so you'd
     re-run this script with fresh values each time.
   - **Chain to an existing long-lived profile** (`role_arn` +
     `source_profile`, e.g. a dev SSO profile you already use day-to-day
     that can assume the target role) — **recommended** for anything
     spanning more than one sitting, since AWS CLI/boto3 auto-refreshes the
     assumed session with no manual re-entry.
4. Validates the profile via `aws sts get-caller-identity`.
5. Creates `.env.aws_local` from `.env.aws_local.example` if it doesn't
   exist yet, and writes `AWS_SSO_PROFILE=<name>` / `APP_ENV=<env>` into it.

**Never point `AWS_SSO_PROFILE` at `prod-toolsgenai-sso`** unless that
specific cross-team profile is genuinely what you use — in at least one
org's environment it's a Bedrock-only profile with no access to Zamboni's
own AWS account, and pointing there will fail outright or, worse, quietly
authenticate as the wrong identity.

This step only touches `.env.aws_local`. The next step covers the rest —
including a file this script does **not** write to, which matters for the
smoke test in step 5.

## 3. Set up both env files

There genuinely are two separate files, and both matter:

| File | Read by | When it matters |
|---|---|---|
| `.env.aws_local` | `run_aws_local.ps1` / `run_ui_dev.ps1 -Mode aws_local` **only** — these scripts parse it and inject every value into the process environment before starting Python | Whenever you launch the app through either launcher |
| `.env` | `config/settings.py` automatically, via `python-dotenv`, for **any** Python process — the launchers, `aws_smoke_test.py` run directly, `init_control_plane_db.py` run directly, any CLI tool | Whenever you run a Python script *without* going through a launcher — most importantly, the smoke test in the next step |

Concretely: if you only fill in `.env.aws_local` and then run
`python scripts\aws_smoke_test.py` in its own terminal (not through
`run_aws_local.ps1`), Python never sees `.env.aws_local`'s values —
`ZAMBONI_MODE`/`AWS_SSO_PROFILE` fall through their defaults
(`get_mode()` resolves to `aws_ec2`, and `get_boto3_session()` then uses the
ambient default credential chain instead of your named profile), and the
smoke test silently checks the wrong thing.

**The fix is simple: fill in both files with the same real values.**

```powershell
# .env.aws_local was created for you by setup_aws_local_profile.ps1 in
# step 2 (with AWS_SSO_PROFILE/APP_ENV already filled in) -- open it and
# fill in the remaining placeholders: bucket names, Athena workgroup names,
# SNS topic ARNs, AWS_ACCOUNT_ID.
notepad .env.aws_local

# Copy .env.example to .env if you don't have one yet, then copy the same
# real values across -- simplest is to literally copy .env.aws_local over
# it, since every key .env needs is a subset of what .env.aws_local has:
Copy-Item .env.aws_local .env
```

A few keys are worth calling out specifically:

- **`AWS_SSO_PROFILE` and `ZAMBONI_MODE=aws_local`** — the two keys that
  actually cause the "wrong file read" gap above. Without them in `.env`,
  any direct script invocation silently behaves as if it were `aws_ec2`
  mode.
- **`ZAMBONI_CONTROL_PLANE_DB`** — can stay a bare filename
  (`zamboni_control.db`) in both files here; a laptop run never goes
  through CodeDeploy, so there's no wipe-on-deploy risk the way there is on
  EC2 (see `docs/setup/aws_ec2.md`). It's created fresh in your working
  directory on first write — delete it any time to start over.
- **`ZAMBONI_CONTROL_PLANE_FIRST_INSTALL`** — leave `false` normally.
  `scripts/init_control_plane_db.py` refuses to start against a control-plane
  DB that's empty with no S3 backup to restore from (protects against
  silently starting with a blank production control plane). If this is
  genuinely a first-ever run against a brand-new account with no prior data
  anywhere, set this to `true` in **`.env`** specifically (not just
  `.env.aws_local` — `init_control_plane_db.py` is exactly the kind of
  script that's often run directly, so `.env` is the file that needs it),
  run once, then set it back to `false`.

**Never commit either file** — both are gitignored; only the `.example`
templates are tracked.

## 4. Install Python + Node dependencies

Same as `local` mode (see `docs/setup/local.md`) — this only affects which
AWS account the app talks to, not what needs to be installed first:

```powershell
python -m venv .venv                          # one-time: create a virtual environment
.\.venv\Scripts\Activate.ps1                  # activate it (needed in each new shell)
python -m pip install -r requirements.txt     # one-time: install Python dependencies
```

`run_aws_local.ps1` also builds the React UI for you (`npm ci` +
`npm run build`) if `ui\dist` is missing; `run_ui_dev.ps1` does not — run
`run_aws_local.ps1` at least once (or `npm ci` by hand in `ui\`) before your
first `run_ui_dev.ps1`, so `ui\node_modules` exists.

## 5. Run the smoke test

```powershell
python scripts\aws_smoke_test.py --create-lock-table
```

Exercises every AWS service Zamboni touches, one command, before you rely on
any of it. Each check reports `PASS`, `FAIL`, or `SKIPPED` (`SKIPPED` means
it's actually still running in `local` mode — check `.env`'s `ZAMBONI_MODE`
if you see this unexpectedly):

| Check | What it verifies |
|---|---|
| `sts_identity` | Your AWS credentials resolve at all — returns the account ID and caller ARN, confirming the named profile (or whatever's ambient) is actually live |
| `glue_list_databases` | The Glue Data Catalog is reachable and readable — lists a few real database names |
| `athena_workgroup` | `ATHENA_WG_APP` exists and is `ENABLED` in this account/region — checked independently of running a query, so a missing/misnamed workgroup gets its own clear failure instead of being buried in a query error |
| `athena_select_1` | A real `SELECT 1` actually executes through Athena in the app workgroup, using the exact same `QueryExecutionContext` shape the real app uses (plain database addressing, no catalog override) |
| `s3_put_delete` | Write access to `ATHENA_RESULTS_BUCKET` — puts and deletes a small probe object |
| `sns_get_topic_attributes` | `SNS_ALERT_TOPIC_ARN` is reachable |
| `dynamodb_lock_table` | The `zamboni_maintenance_locks` table exists; `--create-lock-table` creates it if missing |
| `glue_get_table_optimizer` | Reads one real row from `stream_registry` via Athena, then probes `GetTableOptimizer` (compaction/retention/orphan_file_deletion) for that table — exercises the exact IAM permissions Gate 0's conflict detector needs |
| `control_plane_db` | The SQLite control-plane file (`ZAMBONI_CONTROL_PLANE_DB`) is reachable and has all 5 expected tables; `--init-control-plane-db` runs `scripts/init_control_plane_db.py` to create/migrate it if not |

Other useful flags: `--json` for machine-readable output.

**Every check should PASS.** `control_plane_db` will `FAIL` on a genuinely
first-ever run against a brand-new account with no data anywhere — that's
the `ZAMBONI_CONTROL_PLANE_FIRST_INSTALL` case from step 3 above; set it,
re-run with `--init-control-plane-db`, then set it back to `false`.

## 6. Start the app

```powershell
# Checks/creates the AWS profile if needed, refreshes the session, loads
# .env.aws_local, initializes/migrates the control-plane DB, builds the UI
# if missing, starts uvicorn on :8000, opens the browser
powershell -ExecutionPolicy Bypass -File run_aws_local.ps1

# Dev mode (hot-reload) -- starts uvicorn --reload + npm run dev together
powershell -ExecutionPolicy Bypass -File run_ui_dev.ps1 -Mode aws_local
```

Both launchers run `python scripts/init_control_plane_db.py` automatically
before starting the app — idempotent, safe every time, so you don't need to
run it by hand unless you're troubleshooting outside either launcher (e.g.
step 5's `--init-control-plane-db`).

Open `http://localhost:8000` (or `:5173` for the hot-reload dev server) and
log in with the demo credentials shown on the login screen itself.

## Running an engine (HK / Archival / Lifecycle) against real AWS

There are three distinct ways an engine actually runs — pick the one that
matches what you're doing:

| Option | When to use it | How |
|---|---|---|
| **Manual CLI** | Testing a change, validating against real data before trusting the schedule, a one-off run | `python -m engine.scripts.run_*`, this section |
| **EventBridge schedule** | Normal steady-state operation | `aws_ec2` only, not `aws_local` — see `data_operations_guide.md` §6's schedule table (`EnableEngineScheduling` stack parameter) |
| **Control-M** | Orgs that already trigger HK as a post-batch job step | HK Engine's documented trigger is "EventBridge (hourly) **or** Control-M post-batch" (`.claude/CLAUDE.md`'s engine table) — Control-M invokes the identical `python -m engine.scripts.run_hk` CLI below as a job step, it's not a separate code path. `stream_registry.controlm_hk_job`/`dependent_on_controlm_job` (Table Registration → Control-M Integration) is what wires a table to its upstream Control-M job for Gate 1's dependency check, independent of which of these three actually launches the engine process |

The rest of this section is the manual-CLI option, since `aws_local` has
no EventBridge/Control-M of its own. The three engines aren't started by
either launcher above — they're separate, one-shot CLI processes. Every
invocation needs:

```powershell
# Must be a fresh shell, or one where step 3's .env is already loaded --
# these scripts are run directly with `python -m`, not through
# run_aws_local.ps1/run_ui_dev.ps1, so nothing auto-injects .env.aws_local
# or AWS_PROFILE for you (see the gotcha in step 3 above).
.\.venv\Scripts\Activate.ps1
$env:AWS_PROFILE = "<the profile you set up in step 2>"
```

**Scope options** (combine freely — all optional, default is the whole
fleet):

| Flag | Effect | Engines that accept it |
|---|---|---|
| *(none)* | Every enabled/eligible table | All |
| `--table <fqn>` | One specific table | HK only |
| `--domain <name>` | Every table in a domain | HK, Archival |
| `--domain <name> --layer staging\|datalake\|base\|master` | One domain, one layer | HK only |
| `--tier critical\|standard\|low` | Every table in a tier | HK only |
| `--environment prod\|preprod\|dev\|test` | Target environment (default `prod` for HK/Archival, `preprod` for the 3 Lifecycle jobs) | All |
| `--dry-run` / `--no-dry-run` | Evaluate but don't write, vs. execute for real (default: `.env`'s `DRY_RUN_DEFAULT`) | All |

There is no `--force` CLI flag on any script (an earlier version of `run_hk.py`'s own docstring documented one that was never actually implemented — fixed alongside this doc). Per-table window-check bypass is the `stream_registry.force_run` column (read by `orchestrator.py`/`hk_engine.py`'s window evaluation) — a control-plane data flag, not a CLI option, and not currently exposed in the UI either.

**Always start scoped + dry-run against a real account, then widen:**

```powershell
# HK -- compaction, snapshot expiry, orphan cleanup
python -m engine.scripts.run_hk --domain finance --dry-run
python -m engine.scripts.run_hk --table glue_catalog.finance_db.finance_staging --dry-run
python -m engine.scripts.run_hk --domain finance --layer staging --dry-run

# Archival -- export-then-delete cold staging partitions
python -m engine.scripts.run_archival --domain finance --dry-run

# Lifecycle -- 3 sub-jobs, run in this order (each is independently scoped)
python -m engine.scripts.run_lifecycle_scan --environment preprod --dry-run    # discover/refresh nonprod_registry
python -m engine.scripts.run_lifecycle_cycle --environment preprod --dry-run   # evaluate state transitions + notify
python -m engine.scripts.run_cleanup --environment preprod --dry-run          # hard-delete expired PENDING_DROP tables
```

Once a dry run looks right, drop `--dry-run` (or pass `--no-dry-run`) to
execute for real. Full flag reference (identical to the table above, one
line per script) also lives in each script's own docstring
(`engine/scripts/run_*.py`) and in `data_operations_guide.md` §6.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Script tries `aws sso login` against a profile that isn't SSO-based and fails confusingly | The launcher assumed a missing profile check meant an expired session | `run_aws_local.ps1`/`run_ui_dev.ps1` check whether the profile exists at all before attempting a session check, and offer to create it interactively — update to the current scripts if you still hit this |
| Calls silently authenticate as the wrong AWS identity/account | `AWS_SSO_PROFILE` points at a profile (e.g. a cross-team `prod-toolsgenai-sso`) that isn't actually Zamboni's own account | Run `setup_aws_local_profile.ps1` to create/point at a profile genuinely scoped to your Zamboni AWS account |
| `aws_smoke_test.py` checks report the wrong mode, or fail with `AWS_SSO_PROFILE is not set` | `.env` doesn't have `ZAMBONI_MODE`/`AWS_SSO_PROFILE` set — see step 3 above | Fill in `.env`, not just `.env.aws_local` — `python-dotenv` is the only thing that loads `.env` automatically for a directly-run script |
| `aws_smoke_test.py` checks PASS but the running app's Athena/Glue/S3 calls still fail | `AWS_PROFILE` (not just `AWS_SSO_PROFILE`) wasn't exported into the process that launched the app | Launch via `run_aws_local.ps1`/`run_ui_dev.ps1` (both export it), or set `$env:AWS_PROFILE` yourself first |
| `aws sso login --profile zamboni-dev` succeeds, but the app still fails with `ExpiredTokenException` | boto3's default credential chain checks `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN` env vars **before** the named profile's own credentials, even with `profile_name=` passed explicitly — a stale export of these three (an earlier paste-credentials session, an org credential-helper tool, a leftover shell-profile export) silently wins over a fresh SSO login | `config/settings.py::get_boto3_session()` clears those 3 (+`AWS_SECURITY_TOKEN`) before building the session. If you still hit it, run `Get-ChildItem Env: \| Where-Object Name -like 'AWS_*'` and remove any stale `AWS_ACCESS_KEY_ID`/etc., or open a fresh shell |
| Every real query 500s with `CATALOG_NOT_FOUND` | An old checkout still forces `Catalog=ATHENA_CATALOG` into Athena calls or embeds `glue_catalog.` in SQL text — `glue_catalog` is never an Athena concept in this app, see the note in step 1 | Pull the current code; if you've customized `.env`, confirm your `*_TABLE=` values read `zamboni_catalog.<table>` (2-part), not `glue_catalog.zamboni_catalog.<table>` (3-part) — a stale `.env` overrides the Python defaults |
| `POST /api/tables/register` (or any control-plane write) 500s citing a missing column | `zamboni_control.db` was created/last migrated before that column existed as a migration | Run `python scripts/init_control_plane_db.py` (automatic via both launchers, safe to run directly too) — an idempotent `ALTER TABLE ADD COLUMN` that preserves existing rows. **Never** `python scripts/seed_local_db.py --reset` for this — that seeds a completely different file (`ZAMBONI_LOCAL_DB`, the fabricated local/demo fixture), not the control-plane DB |

---

Other modes: [local](local.md) · [aws_ec2](aws_ec2.md) · back to
[the setup index](../SETUP_GUIDE.md)
