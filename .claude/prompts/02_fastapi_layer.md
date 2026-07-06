# PHASE 2 — FastAPI Layer (full locked contract)

Context: Engine hardening complete (1a–1c). This phase wraps the engine in the
LOCKED API contract (.claude/contracts.md §6) — ~45 endpoints across 8 routers.
The engine layer is called in-process (direct imports), never shelled.
Streamlit remains untouched and running.

## Read first
.claude/contracts.md §2, §5, §6, §7 (client expectations), .claude/CLAUDE.md,
.claude/components.md (engine function signatures), engine/core/registry.py,
engine/core/config.py, engine/core/escalation.py, engine/core/governance.py,
engine/core/orchestrator.py, engine/core/recovery.py, engine/core/lock_service.py,
app/components/athena_runner.py (query patterns to reuse), the page files for
any endpoint whose logic currently lives inline in Streamlit (e.g., dry-run
viewer gate summary, stale queries, cost queries, KPI queries) — you will LIFT
that SQL/logic into api/services/ functions, not duplicate it.

## Structure to create
```
api/
├── main.py            # FastAPI app, routers, CORS (dev: allow http://localhost:5173),
│                      # /docs on, static mount of ui/dist AFTER routes (guard if absent)
├── deps.py            # get_current_user() env-stub (ZAMBONI_USER, default "local-dev"),
│                      # get_dry_run_default(), pagination dep (page/size, size≤250)
├── models.py          # Pydantic v2 models: envelope {data,pagination,error},
│                      # MutationResult {success,dry_run,audit_id}, request models
│                      # mirroring engine signatures (RegisterTableRequest etc.)
├── services/          # lifted query/logic layer shared with (not copied from) pages:
│   ├── tables_svc.py  policies_svc.py  gates_svc.py  lifecycle_svc.py
│   ├── executions_svc.py  controlm_svc.py  settings_svc.py  system_svc.py
└── routers/
    ├── tables.py  policies.py  gates.py  lifecycle.py
    ├── executions.py  controlm.py  settings_router.py  system.py
```
requirements.txt: add fastapi, uvicorn[standard], python-multipart
(check versions compatible with Python 3.11; pin).

## Implementation rules
- Endpoint set, paths, params, response envelope: EXACTLY contracts §6.
  If an engine function is missing a capability the contract needs (e.g., paged
  list with filters), implement it in api/services/ using the same SQL patterns
  as the existing pages (reuse the exact WHERE-builder patterns; strip
  glue_catalog. only for display fields, never in WHERE).
- Every mutation: dry_run flows to engine; write audit via existing
  engine/core/audit (actor from get_current_user); return audit_id (generate
  and pass through if the audit fn returns none — extend audit minimally to
  return an id if trivial, else uuid recorded in the event).
- Gates router PUT: enforce GATE0_OVERRIDE_MAX_HOURS cap and required reason
  when setting override; audit GATE0_OVERRIDE_SET.
- CSV endpoints: job-mapping import reuses the EXACT parsing/matching logic
  from the Bulk Control-M import tab (lift into controlm/tables service — the
  blank-row stripping, header rename compat, LIKE match preview). Export
  reuses the export tab's SELECT. Return the per-row match report as data.
- /api/system/locks: DynamoDB scan (aws modes) / SQLite select; DELETE
  force-release calls lock_service admin path (add release_force(fqn, actor)
  — audited).
- Envelope error handler: one exception handler converting HTTPException +
  unhandled errors into the envelope with proper status codes; engine
  exceptions surface message (500) with structured log.
- Static serving: if ui/dist exists mount at "/", html=True — routes first.

## Tests — tests/api/ (new dir, FastAPI TestClient, local mode env)
- conftest: seeded SQLite (reuse seed script), TestClient fixture, env
  ZAMBONI_MODE=local + ZAMBONI_USER=test-user.
- Per router: at minimum one happy GET (envelope+pagination asserted), one
  filtered GET, one mutation dry_run=true (audit_id present, no data change),
  one validation failure (422/400 in envelope).
- Contract smoke: iterate app.routes → assert every path from contracts §6
  exists with the right method (hard guard against drift).
- Gates override cap test; job-mapping import round-trip with a 2-row CSV
  (one matching, one blank line → stripped).

## Acceptance criteria
- pytest tests/unit + tests/api all green; ruff clean.
- `uvicorn api.main:app --port 8000` (local mode): /docs renders; paste output of
  `curl -s localhost:8000/api/system/mode` and
  `curl -s "localhost:8000/api/tables?page=1&size=5"` (envelope visible).
- Contract smoke test passes (every §6 route present).
- No Streamlit file modified except zero (this phase touches none).
- Migration Progress appended (include endpoint count + test counts).

## Do NOT
- Invent endpoints or change shapes vs contracts §6.
- Copy-paste page SQL — LIFT into services (pages keep working as-is; the
  React pages will call the API; Streamlit still uses its own path this phase).
- Add auth libraries — the stub only; the OIDC seam is deps.get_current_user.

Suggested commit: `feat(api): FastAPI layer — full locked contract (8 routers, services, tests)`
