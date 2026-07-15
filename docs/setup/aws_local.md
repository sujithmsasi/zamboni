# aws_local Mode Setup (laptop demo against real AWS)

Runs from your own machine, but talks to a real AWS account via a named
profile — no EC2 instance needed. Useful for demos where the data needs to
be real, or for validating a change against real Athena/Glue before it goes
anywhere near a deployed instance. Despite the name, `AWS_SSO_PROFILE` works
with any named profile — SSO, role-chaining (`role_arn`/`source_profile`),
or static/session credentials — not only real SSO.

See `docs/SETUP_GUIDE.md` for how this fits alongside `local` / `aws_ec2`
mode if you haven't already picked one.

## Prerequisites

1. **Python 3.11+, Node 20+, and AWS CLI v2 installed locally.** Python
   runs the FastAPI backend and engine CLI; Node builds the React UI —
   `run_aws_local.ps1` runs `npm ci`/`npm run build` for you if `ui\dist`
   is missing, but `run_ui_dev.ps1` only runs `npm run dev` and does
   **not** install anything, so `ui\node_modules` must already exist
   before you use it (run `run_aws_local.ps1` once first, or `npm ci`
   manually in `ui\`) — see "Installing Python dependencies" below for
   the piece neither script handles; the AWS CLI is what
   `setup_aws_local_profile.ps1`/`run_aws_local.ps1` shell out to for
   `aws sso login` / `aws sts get-caller-identity` / `aws configure`.
2. **An AWS account with Zamboni's infra already provisioned** — Athena
   workgroups, the Glue `zamboni_catalog` metadata tables, SNS topics, a
   DynamoDB lock table. If the target account is greenfield for Zamboni,
   that infra has to be provisioned once before any of this works. This mode does not create
   AWS infrastructure; it only points the app at an account that already
   has it.

   **`glue_catalog` and `zamboni_catalog` are two different, easily-confused
   things — but only one of them is an Athena concept.** `zamboni_catalog`
   is the real Glue *database* Zamboni's own metadata tables live in; every
   Athena query this app issues addresses it as plain `zamboni_catalog.
   <table>` (2-part, resolved against Athena's own default catalog — no
   catalog qualifier at all). `glue_catalog` is **not** an Athena Data
   Catalog registration in this app (a previous version of this doc
   claimed it needed to be one — that was wrong, corrected 2026-07-15) — it
   is the Spark/Iceberg catalog name used only *inside* AWS Glue ETL jobs,
   when a table's `compaction_engine='glue'` triggers a real Glue job (see
   `engine/strategies/sort.py`/`zorder.py`). If you ever see
   `CATALOG_NOT_FOUND` from a real Athena query, it means the code path
   that raised it is (incorrectly) passing an explicit `Catalog` in its
   `QueryExecutionContext` or embedding a `glue_catalog.` prefix in SQL
   text — `python scripts/aws_smoke_test.py`'s `athena_workgroup`/
   `athena_select_1` checks confirm plain `database.table` addressing
   against your account's real Athena default catalog works.
3. `.env.aws_local` filled in with real bucket names, Athena workgroups,
   SNS topic ARNs, and account ID for your target AWS account (every value
   in `.env.aws_local.example` other than `AWS_SSO_PROFILE` is a
   placeholder — `your-athena-results-bucket`, `123456789012`, etc.).
4. **A named AWS CLI profile pointed at that account.** You do *not* need
   to set this up by hand first — see "Setting up the AWS profile" below,
   which walks through creating one interactively. **Never point
   `AWS_SSO_PROFILE` at `prod-toolsgenai-sso`** unless that specific
   cross-team profile is genuinely what you use — in at least one org's
   environment it's a Bedrock-only profile with no access to Zamboni's own
   AWS account at all, and pointing there will fail outright or, worse,
   quietly authenticate as the wrong identity. `.env.aws_local.example`'s
   default (`zamboni-preprod`) is deliberately just a placeholder name, not
   a working profile — it matches `setup_aws_local_profile.ps1`'s own
   default, so the two line up without you needing to edit anything.

   **Naming convention**: since each AWS account is its own environment,
   name the profile `zamboni-{env}` (`zamboni-dev`, `zamboni-preprod`,
   `zamboni-prod`, ...) to match the account it points at, and set
   `APP_ENV` in the same `.env.aws_local` file to the same `{env}` — e.g.
   `AWS_SSO_PROFILE=zamboni-dev` alongside `APP_ENV=dev`. Nothing derives
   `APP_ENV` from the profile name automatically, so keep the two in sync
   by hand; a mismatch (e.g. `zamboni-dev` profile with `APP_ENV=prod`)
   won't error, it'll just show the wrong environment tag in the header
   while genuinely talking to the dev account.

## Setting up the AWS profile

You have two ways to do this — pick whichever is more convenient, both end
up in the same place:

**Option A — let `run_aws_local.ps1` walk you through it (recommended for
first-time setup).** Just run it (see "Running it" below). As of the
2026-07-11 fix, if `AWS_SSO_PROFILE`'s configured profile doesn't exist on
your machine yet, the script detects this, explains why, and offers to run
the setup script for you right then:

```
[2/4] Checking AWS profile 'zamboni-preprod'...
      Profile 'zamboni-preprod' does not exist on this machine yet.
      This is expected on a fresh machine/org account -- Zamboni has no
      working default profile; it must be created once.
      Set it up now via setup_aws_local_profile.ps1? [Y/n]
```

Say yes, and it launches the interactive setup below, then reloads
`.env.aws_local` and continues — no separate manual step needed.

**Option B — run it yourself first:**
```powershell
powershell -ExecutionPolicy Bypass -File setup_aws_local_profile.ps1
```

Either way, it interactively creates/updates a named profile and writes it
into `.env.aws_local`'s `AWS_SSO_PROFILE`, via one of two modes you choose
at the prompt:

- **Paste credentials** (Access Key ID / Secret / Session Token, copied
  straight from the AWS Console's "Command line or programmatic access"
  panel) — simplest if that's what you have, but an assumed-role session
  token expires (often ~1h), so you'd need to re-run this script with
  fresh values each time.
- **Chain to an existing long-lived profile** (`role_arn` +
  `source_profile` — e.g. point it at a dev SSO profile you already use
  day-to-day, supplying the target account's role ARN it can assume).
  **Recommended** for anything spanning more than a single sitting — a
  multi-day demo-prep window in particular — since AWS CLI/boto3 then
  assumes and auto-refreshes the role on every call with no manual
  re-entry ever needed.

It also creates `.env.aws_local` from the `.example` template automatically
if it doesn't exist yet, and validates the profile via
`aws sts get-caller-identity` before finishing.

## Installing Python dependencies

Same as `local` mode (see `docs/setup/local.md`) — this only affects which
AWS account the app talks to, not what needs to be installed first:

```powershell
python -m venv .venv                          # one-time: create a virtual environment
.\.venv\Scripts\Activate.ps1                  # activate it (needed in each new shell)
python -m pip install -r requirements.txt     # one-time: install Python dependencies
```

Neither script installs Python dependencies — activate the venv above
before running either. `run_aws_local.ps1` also builds the React UI for
you (`npm ci` + `npm run build`) if `ui\dist` is missing; `run_ui_dev.ps1`
does not — run `run_aws_local.ps1` at least once (or `npm ci` by hand in
`ui\`) before your first `run_ui_dev.ps1`, so `ui\node_modules` exists.

## Running it

```powershell
# Windows — checks/creates the profile if needed, refreshes the session,
# loads .env.aws_local, initializes/migrates the control-plane DB, builds
# the UI if missing, starts uvicorn on :8000, opens the browser
powershell -ExecutionPolicy Bypass -File run_aws_local.ps1

# Dev mode (hot-reload) — starts uvicorn --reload + npm run dev together
powershell -ExecutionPolicy Bypass -File run_ui_dev.ps1 -Mode aws_local
```

**2026-07-15 fix**: both launchers now run `python scripts/init_control_plane_db.py`
automatically before starting the app (idempotent — creates the 5
control-plane tables if missing, applies any pending column migrations if
not). Before this fix, neither script ran it at all, so a fresh or
schema-stale `zamboni_control.db` was never caught before the app started
against it — this is what produced both "control plane is empty" and
"`stream_registry` is missing column X" failures on a first `aws_local` run.
If you're on an older checkout, update these two scripts (or run
`python scripts/init_control_plane_db.py` by hand once) before continuing.

`ZAMBONI_CONTROL_PLANE_DB` in `.env.aws_local` can stay a bare filename
(`zamboni_control.db`) here — a laptop run never goes through CodeDeploy, so
there's no wipe-on-deploy risk. It gets created fresh in your working
directory on first write; delete it any time to start over.

## Verifying it

```bash
python scripts/aws_smoke_test.py --create-lock-table
```

Every check should PASS (not SKIPPED — SKIPPED means it's actually still
running in local mode; check your env vars), and `control_plane_db` will
FAIL on a genuinely first-ever run against an empty account — see the
`ZAMBONI_CONTROL_PLANE_FIRST_INSTALL` note below before re-running with
`--init-control-plane-db`.

2026-07-15 fix: every AWS-touching module (`athena_client.py`,
`glue_client.py`, `s3_client.py`, `notifier.py`, `metrics.py`,
`archival.py`, `compaction.py`, `cost_explorer.py`,
`execution_log_parquet.py`, `health_check.py`, `backpressure.py`, plus the
Stale Resources API's S3 scan) now routes through `get_boto3_session()`
explicitly, instead of a bare `boto3.client(...)` relying on the ambient
default credential chain — found via a real run where
`glue_get_table_optimizer` failed with `UnrecognizedClientException` while
every other check in the same session passed. So a passing smoke test now
is much stronger evidence the rest of the app will actually use the right
profile too. `run_aws_local.ps1`/`run_ui_dev.ps1` still export
`AWS_PROFILE` as well, and it's still worth launching that way (or setting
`$env:AWS_PROFILE` yourself first) as defense in depth for any
third-party-library call this codebase's own wrappers don't sit in front
of — but it's no longer the load-bearing fix it used to be for code that
goes through these modules.

2026-07-15 fix (second, same day — corrected later the same day, see the
third fix below): `athena_select_1` used to omit `Catalog` from its
`QueryExecutionContext` entirely. At the time this looked like a smoke-test
gap (silently testing the wrong catalog). It turned out to be the opposite:
`athena_client.py`'s own `Catalog=ATHENA_CATALOG` was the actual bug, not
this check — see below.

2026-07-15 fix (third, same day — the real root cause): `ATHENA_CATALOG`
(`glue_catalog`) is **not** an Athena Data Catalog registration at all in
this app. It is the Spark/Iceberg catalog name used only inside AWS Glue
ETL jobs (`compaction_engine='glue'`, see `engine/strategies/sort.py`/
`zorder.py::build_glue_params()`'s `--catalog` argument). Every place that
previously forced `Catalog=ATHENA_CATALOG` into a real Athena
`QueryExecutionContext`, or embedded a literal `glue_catalog.` prefix into
SQL text, was wrong and has been removed —
`engine/utils/athena_client.py`, every metadata table constant in
`config/settings.py` (now plain `zamboni_catalog.<table>`, 2-part), and
every `"$snapshots"`/`"$files"`/`"$partitions"` business-table query
(`engine/core/integrity_checker.py`, `maintenance_ops.py`,
`health_checker.py`, `commit_frequency.py`, `engine/utils/glue_client.py`,
`engine/operations/archival.py`). `engine/utils/local_db.py::_translate()`
(the Athena-to-SQLite SQL rewriter `engine/core/control_plane.py`'s real
production reads/writes also depend on) was updated to match. `.env`/
`.env.example`/`.env.aws_local.example`'s `*_TABLE=` values and `sql/*.sql`
all updated to the same 2-part convention. **Not changed**: a registered
table's own `table_fqn` (e.g. `glue_catalog.finance_db.fin_payment_master`)
stays 3-part — that's the one place `glue_catalog` is correctly meaningful,
since it's what flows into a Glue job's `--catalog` argument when
`compaction_engine='glue'` triggers one.

**Do not fix an empty/stale control-plane DB with `python
scripts/seed_local_db.py --reset`.** That script seeds `ZAMBONI_LOCAL_DB`
(`zamboni_local.db`, the fabricated local/demo fixture with fake domains and
tables) — a completely different file from `ZAMBONI_CONTROL_PLANE_DB`
(`zamboni_control.db`, the real control-plane DB `aws_local`/`aws_ec2` mode
actually reads/writes). Running it here does not fix anything and, if ever
pointed at the wrong file, would inject fabricated demo data into what
should be a real environment. The correct tool is always
`python scripts/init_control_plane_db.py` (now run automatically by both
launchers — see above).

**First-ever run against a genuinely empty account**: `--init-control-plane-db`
will refuse to proceed with `ControlPlaneEmptyAndNoBackupError` unless you
add `ZAMBONI_CONTROL_PLANE_FIRST_INSTALL=true` to `.env` first (not just
`.env.aws_local` — `config/settings.py` loads `.env` via `dotenv`, so that's
the file that actually needs it if you ever run a script directly rather
than through `run_aws_local.ps1`). This is deliberate — it's the same
safety gate that stops a real EC2 instance replacement from silently
starting with a blank production control plane — but it means a fresh
account's first `--init-control-plane-db` run needs that flag set, not just
the flag alone.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Script tries `aws sso login` against a profile that isn't SSO-based and fails confusingly | Fixed 2026-07-11 — the script now checks whether the profile exists at all *before* attempting a session check, and offers to create it interactively instead of assuming it's just an expired SSO session | Update to the current `run_aws_local.ps1`; if you still hit this, the profile exists but genuinely isn't SSO — re-run `setup_aws_local_profile.ps1` to refresh its credentials |
| Calls silently authenticate as the wrong AWS identity/account | `AWS_SSO_PROFILE` points at a profile (e.g. a cross-team `prod-toolsgenai-sso`) that isn't actually Zamboni's own account | Run `setup_aws_local_profile.ps1` to create/point at a profile that's genuinely scoped to your Zamboni AWS account |
| `aws_smoke_test.py` checks PASS but the running app's Athena/Glue/S3 calls fail | `AWS_PROFILE` (not just `AWS_SSO_PROFILE`) wasn't exported into the process that launched the app | Launch via `run_aws_local.ps1`/`run_ui_dev.ps1` (both export it), or set `$env:AWS_PROFILE` yourself first |
| `aws sso login --profile zamboni-dev` succeeds, but the app still fails with `ExpiredTokenException` (e.g. on `GET /api/locks`) | **Real gotcha, fixed 2026-07-11**: boto3's default credential chain checks `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN` env vars **before** the named profile's own credentials — even when `profile_name=` is passed explicitly. A stale export of these three (from an earlier paste-credentials session, an org credential-helper tool, a leftover console-copied export in your shell profile, etc.) silently wins over a fresh SSO login, and the app ends up sending AWS the *old, actually-expired* static credentials instead of the profile's live SSO session | `config/settings.py::get_boto3_session()` now clears those 3 (+`AWS_SECURITY_TOKEN`) env vars before building the `aws_local` session, so this should no longer happen. If you still hit it: run `Get-ChildItem Env: | Where-Object Name -like 'AWS_*'` in the same PowerShell session before starting the app — if `AWS_ACCESS_KEY_ID` shows up, `Remove-Item Env:AWS_ACCESS_KEY_ID` (and the other two) or open a fresh shell |
| Every real query 500s with `CATALOG_NOT_FOUND` (`GET /api/executions`, `GET /api/health/kpis`, `GET /api/glue/tables/{db}`) | **Real bug, fixed 2026-07-15**: the app was forcing `Catalog=ATHENA_CATALOG` (`glue_catalog`) into every Athena `QueryExecutionContext` and embedding `glue_catalog.` prefixes into SQL text — but `glue_catalog` was never an Athena Data Catalog registration, it's the Spark/Iceberg catalog name Glue ETL jobs use internally. Update to the current code (`engine/utils/athena_client.py`, `config/settings.py`, and your `.env`'s `*_TABLE=` values must all use plain `zamboni_catalog.<table>`, no `glue_catalog.` prefix) | If you're still on an older checkout: pull the fix. If you've customized `.env`, confirm your `*_TABLE=` values read `zamboni_catalog.<table>` (2-part), not `glue_catalog.zamboni_catalog.<table>` (3-part) — a stale `.env` overrides the Python defaults and can silently keep the bug alive even after updating the code |
| `POST /api/tables/register` (or any control-plane write) 500s citing a missing column (e.g. `stream_registry has no column named controlm_job_start_time`) | `zamboni_control.db` was created/last migrated before that column existed as a migration (`config/control_plane_schema.py::CONTROL_PLANE_MIGRATIONS`) — usually because `scripts/init_control_plane_db.py` was never run against this file, which was also this repo's launcher-script gap fixed above | Run `python scripts/init_control_plane_db.py` (now automatic via both launchers on every start) — it's an idempotent `ALTER TABLE ADD COLUMN`, safe to run repeatedly, and preserves existing rows. **Never** `python scripts/seed_local_db.py --reset` for this — wrong file entirely, see above |

---

Other modes: [local](local.md) · [aws_ec2](aws_ec2.md) · back to
[the setup index](../SETUP_GUIDE.md)
