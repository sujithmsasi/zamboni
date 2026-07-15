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
# loads .env.aws_local, builds the UI if missing, starts uvicorn on :8000,
# opens the browser
powershell -ExecutionPolicy Bypass -File run_aws_local.ps1

# Dev mode (hot-reload) — starts uvicorn --reload + npm run dev together
powershell -ExecutionPolicy Bypass -File run_ui_dev.ps1 -Mode aws_local
```

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

---

Other modes: [local](local.md) · [aws_ec2](aws_ec2.md) · back to
[the setup index](../SETUP_GUIDE.md)
