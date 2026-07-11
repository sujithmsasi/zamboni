# Local Mode Setup

**No AWS account needed.** A local SQLite file (`zamboni_local.db`) stands in
for everything — Athena tables, the control plane, all of it — pre-seeded
with realistic demo data (17 tables across 4 domains, execution history, cost
data, the works). This is the fastest way to see the whole app, and the
default starting point for offline development.

See `docs/SETUP_GUIDE.md` for how this fits alongside `aws_local` /
`aws_ec2` mode if you haven't already picked one.

## Quick start

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

## Dev mode (hot-reload)

Run the backend with `--reload` on :8000 and `cd ui && npm run dev`
separately on :5173 — the Vite dev server proxies API calls to :8000 via
CORS (`api/main.py`'s allowed-origins list already includes it).

```powershell
powershell -ExecutionPolicy Bypass -File run_ui_dev.ps1 -Mode local
```

## Streamlit fallback (legacy, being phased out)

```bash
streamlit run app/Home.py
```

See the cutover checklist in `docs/deployment/ec2_api_deploy.md` for when
this gets retired.

## Resetting the local database

```bash
python scripts/seed_local_db.py --reset
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
| `PermissionError` on `--reset` | A running `uvicorn`/Streamlit process has `zamboni_local.db` open | Kill it first (see above), then re-run |
| A page shows stale/empty data after editing `config/policy_templates.json` or similar | The running app writes some config files directly (Policy Configuration → Templates, for example) | Check `git status` for stray uncommitted diffs on those files before assuming a seeding bug |
| A table/domain is missing a field you expected the seed script to fill in | `engine/utils/local_db.py::insert_rows()` derives its INSERT column list from `rows[0].keys()` only — a batch mixing dicts with different key sets silently drops the extra keys for every row, not just the odd one out | If you're editing `scripts/seed_local_db.py`, keep every dict in a single `insert_rows()` batch on the *same* key set (see the comment at the top of `seed_domains()` for a worked example of a real bug this caused) |

---

Other modes: [aws_local](aws_local.md) · [aws_ec2](aws_ec2.md) · back to
[the setup index](../SETUP_GUIDE.md)
