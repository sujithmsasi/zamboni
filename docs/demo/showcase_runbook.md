# Zamboni Showcase Runbook — July 17, 2026

Click-by-click path for the demo. Primary path is `aws_local` (real AWS via
SSO, laptop-only — contracts.md D4, no EC2 dependency). Every step has a
`ZAMBONI_MODE=local` fallback variant directly beneath it — if AWS/SSO/
network is unavailable on demo day, switch to local mode without missing a
beat; the data is different (seeded SQLite fleet, not the real Glue
catalog) but every screen and workflow is identical.

**Login gate** (both modes): the app sits behind a demo-only login screen.
Credentials are shown openly on the login page itself: `admin` /
`Zamboni@2026`. This is a client-side gate, not real auth.

---

## 0. Before you start

**Primary (aws_local):**
```powershell
copy .env.aws_local.example .env.aws_local   # first time only — fill in real values
.\run_aws_local.ps1
```
This logs into AWS SSO if needed, builds `ui/dist` if missing, and opens
`http://localhost:8000`.

**Fallback (local):**
```powershell
.\run_local_api.bat
```
No AWS, no SSO — pure SQLite (`zamboni_local.db`), same UI. Opens
`http://localhost:8000`.

**Last resort (Streamlit, if the React/FastAPI stack itself is unavailable):**
```powershell
.\run_local.ps1
```
Opens `http://localhost:8501` — the pre-replatform UI, feature-complete for
everything except the Workstream A safety-core screens that only exist in
React (Gate 0 override UI, locks strip, governance report). Use only if
steps 1–7 below are literally not renderable.

---

## 1. Home — fleet numbers

Navigate to `/` (Home, first item in the sidebar).

Talking points: 5 KPI cards (coverage, executions, failures, conflicts,
active locks), Fleet Coverage by Domain chart, Execution Trend chart, the
Fleet Health banner at the top of every page, Recent Activity grid.

**Local-mode note:** numbers come from the seeded fleet (17 registered
tables across 5 domains), not a real 30k-table catalog — call this out
explicitly rather than let it pass as production scale.

---

## 2. Health Dashboard — Dual-Optimizer Risk Report (the incident narrative)

Navigate to **Health Dashboard** (`/health`) → scroll to the "🛡️ Maintenance
Governance" section.

Talking points, in order:
1. **The incident**: AWS Glue's own table optimizer (compaction + snapshot
   retention + orphan deletion) can run on a schedule completely
   independent of Zamboni's HK engine. When both systems maintain the same
   table without coordination, clock-spaced (not completion-based) timing
   between them corrupted Iceberg metadata in production — the root cause
   Workstream A (engine hardening) exists to fix.
2. Point at the **Dual-Optimizer Risk Report** grid — every row is a table
   where `hk_enabled=true` AND Glue's own optimizer is independently
   enabled (`aws_opt_compaction`/`retention`/`orphan` flags, refreshed via
   `GetTableOptimizer`, cached with a TTL). This is the proof: "here are
   the tables where two uncoordinated systems are both touching the same
   metadata right now."
3. Click **"🔄 Rescan conflicts"** — a live `GetTableOptimizer` fleet scan
   (aws_local) or the local-mode cached scan (local) — to show it isn't a
   stale/canned report.
4. Scroll to **Recent Integrity Failures — Last 7 Days** — the
   `verify_advanced()` safety net that trips the circuit breaker and halts
   remaining steps the moment a metadata pointer or snapshot count doesn't
   move the way the operation in flight expects.

**Local-mode note:** the seeded fleet has exactly one conflicted table
(`fin_payment_master`, `aws_opt_compaction`) — enough to show the report
working, not framed as "most of the fleet is conflicted."

---

## 3. Policy Configuration — Gate 0 override (time-boxed, reasoned, audited)

Navigate to **Policy Configuration** (`/policies`) → **Edit Single Table** →
search/select a table → open the **Gates** section of the accordion.

1. Toggle a Gate 0 override: set a future end time capped at
   `GATE0_OVERRIDE_MAX_HOURS` (24h default — the DatePicker won't let you
   pick beyond it), type a reason (required — the form blocks Save without
   one).
2. Save. Point out the toast's `audit_id` — every override is written to
   `execution_log` as `GATE0_OVERRIDDEN` and to the audit trail, with actor
   + reason + expiry. This is decision D3: **time-boxed audited override,
   not a hard block** — Zamboni doesn't get to unilaterally veto an
   operator who has a legitimate reason to bypass the conflict gate, but
   every bypass is visible and expires on its own.
3. Open **View Configs** to show the override reflected in the table's row.

**Local-mode note:** identical UI and validation; the override write goes
to SQLite instead of the real `hk_config` Iceberg table.

---

## 4. Dry Run Viewer — gate summary including Gate 0

Navigate to **Dry Run Viewer** (`/dryrun`) → search/select the same table
from step 3.

Point at the gate summary: Gate 1 (Control-M dependency) / Gate 2 (blackout
window) / Gate 3 (circuit breaker) / **Gate 0** (conflict + override state)
all shown together with the final EXECUTE/SKIP decision. If the table's
strategy is `binpack`, the conditional compaction SQL preview also renders
— a good "here's literally the SQL Zamboni would run" beat if the audience
wants mechanism detail.

**Local-mode note:** identical.

---

## 5. Live Activity — locks strip

Navigate to **Live Activity** (`/activity`).

Point at the **active locks strip** at the top — `LockService`-held locks
(DynamoDB in aws_local, SQLite `maintenance_locks` in local mode), each
showing `table_fqn` / `operation` / `acquired_at` / `heartbeat_at`. This is
decision D1's enforcement mechanism made visible: **one maintenance
authority per table, ever** — the currently-running and recent-operations
grids below both auto-refresh every 10 seconds (real polling, not a manual
refresh button).

If a lock happens to be stuck (rare — TTL is `LOCK_TTL_MINUTES`, default
120), the force-release button is here too, audited server-side.

**Local-mode note:** identical; the seeded DB has 1024 execution rows.

---

## 6. Recovery story — "72h floor = guaranteed rollback window"

This step is a terminal transcript, not a UI screen — have a terminal
ready alongside the browser.

```powershell
python scripts\recover_metadata.py --fqn glue_catalog.finance_master_db.fin_payment_master --dry-run
```

Walk through the output: it lists rollback candidates from `execution_log`
rows that captured a `metadata_location_before`, validates the selected
target (S3 existence + real Iceberg metadata parse), and would (in a real,
non-dry-run invocation) point the Glue table's `metadata_location` straight
back via `update_table` — no data rewrite, no re-ingestion, just repointing
the catalog.

**The line to land**: decision D2's 72-hour orphan-deletion floor isn't
just a safety margin — it's a **guaranteed rollback window**. Any metadata
state from the last 72 hours is provably still recoverable, because
Zamboni's own vacuum step is contractually forbidden from deleting files
younger than that floor (`ORPHAN_MIN_AGE_HOURS_FLOOR`, clamped up, never
down, regardless of policy). Refusing to roll back to a target Zamboni
itself already garbage-collected is a *feature* — try the refusal path if
there's time (`--to` pointed at a `--dry-run`-visible orphaned metadata
file older than the floor prints the exact refusal message).

**Local-mode note:** `recover_metadata.py` simulates validate/rollback in
local mode (documented gap — no real S3/Glue calls happen) but the
candidate listing, prompts, and refusal messaging are identical. Say so if
asked — this is an honest stub, not a hidden fake.

---

## 7. Control-M Integration — CSV import (the domain-team workflow)

Navigate to **Control-M Integration** (`/controlm`) → **CSV Workflow** tab.

1. **Step 1 — Download Template**: click through, show the exported CSV
   (real `controlm_pipeline_job`/`controlm_hk_job`/Gate 1 job columns for
   every registered table in scope).
2. **Step 2 — Upload Completed Mapping**: upload a filled-in CSV (or the
   same one re-uploaded, unmodified, to show the round-trip actually
   works — this exact scenario was a real bug fixed on 2026-07-07). Point at the dry-run preview grid — "N will be
   updated" — before committing.
3. Click **Save** → confirm the toast's audit ID → jump to the **Control-M
   Job Registry** tab to show the job(s) now present there too (a job
   applied via CSV registers itself in the catalog, not just on the
   matched tables — also a fixed bug, same date).

This is the beat for "how does a domain team onboard 200 tables' worth of
Control-M dependency wiring without 200 manual edits."

**Local-mode note:** identical; writes go to the seeded SQLite
`controlm_jobs`/`stream_registry` tables.

---

## Timing guide (approximate, adjust live)

| Step | Minutes |
|---|---|
| 0. Launch + login | 1 |
| 1. Home | 2 |
| 2. Health/Governance (the incident story) | 5 |
| 3. Gate 0 override | 3 |
| 4. Dry Run Viewer | 2 |
| 5. Live Activity / locks | 2 |
| 6. Recovery transcript | 3 |
| 7. Control-M CSV | 3 |
| **Total** | **~21** |

## If something breaks mid-demo

1. Don't debug live. Say "let me show you that in local mode" and switch —
   `run_local_api.bat` needs no network and starts in seconds.
2. If the React/FastAPI stack itself won't start, fall back to
   `run_local.ps1` (Streamlit, :8501) — slower, older UI, but the core
   engine story (gates, vacuum floors, governance report) is still there
   via `4_Health_Dashboard.py`'s Maintenance Governance section.
3. `scripts\aws_smoke_test.py` (see `docs/deployment/ec2_api_deploy.md`)
   is the fastest way to confirm *before* the room fills up whether
   aws_local connectivity is actually good that day.
