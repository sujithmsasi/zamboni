# Local Mode Setup

**No AWS account needed.** A local SQLite file (`zamboni_local.db`) stands in
for everything — Athena tables, the control plane, all of it — pre-seeded
with realistic demo data (17 tables across 4 domains, execution history, cost
data, the works). This is the fastest way to see the whole app, and the
default starting point for offline development.

See `docs/SETUP_GUIDE.md` for how this fits alongside `aws_local` /
`aws_ec2` mode if you haven't already picked one.

## Quick start (recommended — one command)

```powershell
python scripts\seed_local_db.py   # one-time: create + seed the local database
.\run_local_api.bat               # builds the UI if needed, starts the API+UI on :8000
```

Open `http://localhost:8000` — login with the demo credentials shown on the
login screen itself (there's no real secret here to protect, see
`ui/src/pages/Login/`).

## Manual step-by-step (PowerShell)

Only needed if you want to run the pieces yourself instead of
`run_local_api.bat` — for example, to see build output, or because you're
customizing something. **Every command below is PowerShell syntax** — `&&`
command-chaining and cmd.exe's `set VAR=value` do not work the same way in
native PowerShell 5.1 and will fail or silently do nothing.

```powershell
# 1. Build the React UI (one time, or whenever ui/src changes and you're
#    not using dev/hot-reload mode below)
cd ui
npm ci
npm run build
cd ..

# 2. Set the env vars for this PowerShell session (note: $env:VAR = "value",
#    not "set VAR=value" — the latter is cmd.exe syntax and won't actually
#    export an environment variable in PowerShell)
$env:ZAMBONI_MODE = "local"
$env:ZAMBONI_LOCAL_MODE = "true"
$env:ZAMBONI_LOCAL_DB = "zamboni_local.db"

# 3. Start the API (serves both the API and the built React UI on :8000)
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## Dev mode (hot-reload, for editing the React source)

This mode runs **two servers at once** so that saving a change in
`ui/src/` shows up in the browser immediately, without rebuilding:

- One process serves the FastAPI backend on port **8000**, with
  `--reload` (so Python changes also auto-restart it).
- A second process runs Vite's dev server on port **5173** — this is what
  you actually open in your browser. It serves the React app directly from
  source and forwards (proxies) any `/api/...` request to the backend on
  :8000, so from the browser's point of view it looks like one app.

You don't need to start these two processes by hand — one script starts
both together and stops both cleanly on Ctrl+C:

```powershell
powershell -ExecutionPolicy Bypass -File run_ui_dev.ps1 -Mode local
```

Then open `http://localhost:5173` (not :8000 — that port has no
hot-reloading).

## Choosing which environment this simulates

Every registered domain/table in the seeded demo data carries an
`environment` value (`dev`/`preprod`/`prod`/`test`) — the same value shown
as the `DEV`/`PROD`/etc. tag next to the app version in the header. In
`aws_local`/`aws_ec2` mode this is effectively fixed, since one AWS account
= one environment in practice. Local mode has no such constraint, so you
can pick it explicitly before seeding:

```powershell
$env:APP_ENV = "dev"        # or "preprod", "prod", "test"
python scripts\seed_local_db.py
```

If `APP_ENV` isn't set, it defaults to `dev` (same default the running app
itself uses — `config/settings.py::APP_ENV`). The checked-in
`zamboni_local.db` in this repo was seeded with `APP_ENV=prod`, matching
the demo data's historical convention — reseed with a different value
locally any time, but don't commit that variant unless you mean to change
the repo's demo baseline.

## Resetting the local database

```powershell
python scripts\seed_local_db.py --reset
```

Wipes `zamboni_local.db` and re-seeds it from scratch. Safe to run any time
— nothing outside this one file is touched in local mode.

**A Windows gotcha you'll hit if a server is still running**: `--reset`
deletes and recreates the SQLite file, and Windows won't let you delete a
file that a running process has open. If you see
`PermissionError: [WinError 32] The process cannot access the file`, a
stale `uvicorn` from an earlier session still holds it — find and stop it
first:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*' } | Select-Object ProcessId, CommandLine
Stop-Process -Id <the PID above> -Force
```

Then re-run the seed script.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `PermissionError` on `--reset` | A running `uvicorn` process has `zamboni_local.db` open | Kill it first (see above), then re-run |
| `&&`-chained commands fail, or `set VAR=value` doesn't seem to do anything | That's cmd.exe/bash syntax, not PowerShell | Run each command on its own line, and use `$env:VAR = "value"` to set environment variables |
| A page shows stale/empty data after editing `config/policy_templates.json` or similar | The running app writes some config files directly (Policy Configuration → Templates, for example) | Check `git status` for stray uncommitted diffs on those files before assuming a seeding bug |
| A table/domain is missing a field you expected the seed script to fill in | `engine/utils/local_db.py::insert_rows()` derives its INSERT column list from `rows[0].keys()` only — a batch mixing dicts with different key sets silently drops the extra keys for every row, not just the odd one out | If you're editing `scripts/seed_local_db.py`, keep every dict in a single `insert_rows()` batch on the *same* key set (see the comment at the top of `seed_domains()` for a worked example of a real bug this caused) |
| Seeded data shows the wrong environment tag (e.g. always `PROD`) | `APP_ENV` wasn't set before running `seed_local_db.py` (defaults to `dev`), or a real `.env` file in the repo root already sets it to something else | Set `$env:APP_ENV` explicitly before reseeding — see "Choosing which environment this simulates" above; check for a `.env` file if the value is unexpected |

---

Other modes: [aws_local](aws_local.md) · [aws_ec2](aws_ec2.md) · back to
[the setup index](../SETUP_GUIDE.md)
