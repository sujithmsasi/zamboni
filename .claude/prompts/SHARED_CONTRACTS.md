# Zamboni Replatform — LOCKED CONTRACTS
# This file is committed as .claude/contracts.md in Phase 0.
# Every phase prompt instructs Sonnet 5 to read this file FIRST.
# Nothing here is negotiable mid-build. Changes require Sujith's explicit sign-off.

---

## 0. Program Overview

Two workstreams, engine first:

- **Workstream A (P0): Engine Hardening** — response to a production incident where
  AWS Glue table optimizer runs (compaction + snapshot retention + orphan deletion,
  spaced 5–10 min apart) caused Iceberg metadata loss. Root cause: clock-based
  spacing instead of completion-based serialization. Fix: one maintenance authority
  per table, lock-serialized, age-floored deletion, commit-verified.
- **Workstream B (P1): Enterprise UI** — migrate Streamlit → FastAPI + React 18 +
  TypeScript + Ant Design v5, white enterprise theme, engine layer untouched.
  Streamlit stays live on :8501 during migration; React/FastAPI on :8000.

Showcase deadline: **July 17, 2026**. Engine hardening ships first so the incident
fix + governance report are demo-able even if UI slips.

---

## 1. Locked Decisions (Sujith, 2026-07-05)

| # | Decision | Value |
|---|---|---|
| D1 | Lock backend | **DynamoDB** table `zamboni_maintenance_locks` — machine-only ephemeral state. ALL other new tables are **Iceberg in glue_catalog.zamboni_catalog** so they are queryable for VP reports (Zamboni actions, health, table-level detail). |
| D2 | Orphan deletion floor | **72h hard floor** (policy values below 72 are clamped UP, never down). Default when unset: 96h. Rationale doubles as marketing: the floor IS the guaranteed rollback window. |
| D3 | Gate 0 override | **Time-boxed audited override**, not hard block. `gate0_override_until` + reason + actor on hk_config. Auto-expires. Every overridden run logs `GATE0_OVERRIDDEN`. |
| D4 | Demo target | Laptop, `ZAMBONI_MODE=aws_local` against real AWS. EC2 deploy documented but not the demo dependency. |
| D5 | Auth | Env-var user stub now; OIDC seam (`get_current_user()` FastAPI dependency) for SSO later. |
| D6 | UI stack | FastAPI + uvicorn (systemd, :8000) serving API + built React static bundle. React 18 + TS + Vite + Ant Design v5 + TanStack Query + react-router. No Redux. No Node in production. |

---

## 2. Settings Constants (config/settings.py additions)

```python
# ── Maintenance safety (Workstream A) ─────────────────────────────
ORPHAN_MIN_AGE_HOURS_FLOOR   = int(os.getenv("ORPHAN_MIN_AGE_HOURS_FLOOR", "72"))   # hard clamp-up
ORPHAN_DEFAULT_AGE_HOURS     = int(os.getenv("ORPHAN_DEFAULT_AGE_HOURS", "96"))
SNAPSHOT_MIN_AGE_HOURS       = int(os.getenv("SNAPSHOT_MIN_AGE_HOURS", "24"))       # never expire younger
MAX_ORPHAN_DELETE_PCT        = int(os.getenv("MAX_ORPHAN_DELETE_PCT", "20"))        # abort above this
CONFLICT_CACHE_TTL_HOURS     = int(os.getenv("CONFLICT_CACHE_TTL_HOURS", "24"))
LOCK_TTL_MINUTES             = int(os.getenv("LOCK_TTL_MINUTES", "120"))
LOCK_HEARTBEAT_SECONDS       = int(os.getenv("LOCK_HEARTBEAT_SECONDS", "60"))
GATE0_OVERRIDE_MAX_HOURS     = int(os.getenv("GATE0_OVERRIDE_MAX_HOURS", "24"))
DDB_LOCK_TABLE               = os.getenv("DDB_LOCK_TABLE", "zamboni_maintenance_locks")
```

Mode resolution (implement if not already present — Phase 0 reports this):

```python
def get_mode() -> str:
    m = os.getenv("ZAMBONI_MODE")
    if m in ("local", "aws_local", "aws_ec2"):
        return m
    return "local" if os.getenv("ZAMBONI_LOCAL_MODE", "").lower() == "true" else "aws_ec2"

def get_boto3_session():
    import boto3
    if get_mode() == "aws_local":
        return boto3.Session(profile_name=os.getenv("AWS_SSO_PROFILE", "prod-toolsgenai-sso"))
    return boto3.Session()  # instance role / env chain
```

---

## 3. Schema — LOCKED DDL

### 3.1 DynamoDB (locks only — machine state, TTL-expiring)

```yaml
# CloudFormation resource (added to existing deploy/zamboni-cfn.yaml in Phase 6;
# for Phases 1a-1c create manually or via scripts/create_lock_table.py)
ZamboniMaintenanceLocks:
  Type: AWS::DynamoDB::Table
  Properties:
    TableName: zamboni_maintenance_locks
    BillingMode: PAY_PER_REQUEST
    AttributeDefinitions:
      - {AttributeName: table_fqn, AttributeType: S}
    KeySchema:
      - {AttributeName: table_fqn, KeyType: HASH}
    TimeToLiveSpecification: {AttributeName: expires_at, Enabled: true}
```

Item shape:
```
table_fqn (S, PK) | lock_owner (S, "host:pid:uuid8") | operation (S)
acquired_at (S ISO) | heartbeat_at (S ISO) | expires_at (N epoch seconds, TTL)
```

Semantics:
- **acquire**: PutItem, ConditionExpression
  `attribute_not_exists(table_fqn) OR expires_at < :now`
- **heartbeat**: UpdateItem SET heartbeat_at, expires_at; ConditionExpression
  `lock_owner = :me`
- **release**: DeleteItem; ConditionExpression `lock_owner = :me`
- Local mode: SQLite table `maintenance_locks` (same columns), acquire =
  transaction: DELETE WHERE expired, then INSERT (PK conflict → held).

### 3.2 Iceberg column additions (Athena `ALTER TABLE ... ADD COLUMNS`;
###     mirror in scripts/seed_local_db.py migrations list)

```sql
-- stream_registry
ALTER TABLE glue_catalog.zamboni_catalog.stream_registry ADD COLUMNS (
  aws_opt_compaction BOOLEAN, aws_opt_retention BOOLEAN,
  aws_opt_orphan BOOLEAN, aws_opt_checked_at TIMESTAMP);

-- hk_config
ALTER TABLE glue_catalog.zamboni_catalog.hk_config ADD COLUMNS (
  gate0_override_until TIMESTAMP, gate0_override_reason STRING,
  gate0_override_by STRING);

-- execution_log
ALTER TABLE glue_catalog.zamboni_catalog.execution_log ADD COLUMNS (
  lock_id STRING, metadata_location_before STRING,
  metadata_location_after STRING, snapshot_id_before BIGINT,
  snapshot_id_after BIGINT, integrity_status STRING);
-- integrity_status ∈ VERIFIED | FAILED | SKIPPED
-- NOTE: execution_log has a Parquet writer mode (EXECUTION_LOG_MODE).
-- New columns MUST flow through BOTH the Athena and Parquet writer paths.
```

### 3.3 New Iceberg table: vacuum_audit (VP-reportable)

```sql
CREATE TABLE IF NOT EXISTS glue_catalog.zamboni_catalog.vacuum_audit (
  run_id STRING, table_fqn STRING, operation STRING,
  snapshots_before INT, snapshots_after INT,
  files_estimated INT, files_deleted INT, bytes_reclaimed BIGINT,
  older_than_hours_used INT, sanity_pct DOUBLE,
  aborted BOOLEAN, aborted_reason STRING,
  lock_id STRING, dry_run BOOLEAN,
  started_at TIMESTAMP, completed_at TIMESTAMP
) LOCATION 's3://${ZAMBONI_METADATA_BUCKET}/zamboni/vacuum_audit/'
TBLPROPERTIES ('table_type'='ICEBERG','format'='parquet');
```

### 3.4 IAM additions (documented in Phase 1a, added to CFN in Phase 6)

```
dynamodb: PutItem, GetItem, UpdateItem, DeleteItem, DescribeTable
          on arn:aws:dynamodb:*:*:table/zamboni_maintenance_locks
glue:     GetTableOptimizer, BatchGetTableOptimizer, ListTableOptimizerRuns
```

---

## 4. Gate 0 — Maintenance Conflict Gate (LOCKED semantics)

Runs BEFORE Gate 1 in the HK flow. Order of checks:

```
1. Override check: hk_config.gate0_override_until set AND now() < it
   → log GATE0_OVERRIDDEN (reason, actor) to execution_log → continue to step 3.
2. Conflict check: read stream_registry.aws_opt_* where
   aws_opt_checked_at within CONFLICT_CACHE_TTL_HOURS.
   Cache stale/missing → LIVE lazy check: glue.get_table_optimizer for
   compaction, retention, orphan_file_deletion; write results back to
   stream_registry. Any optimizer enabled → skip SKIP_AWS_OPTIMIZER_CONFLICT.
3. In-flight check: execution_log has status='RUNNING' for fqn →
   skip SKIP_ALREADY_RUNNING.
4. Lock: lock_service.acquire(fqn, operation) fails → skip SKIP_LOCK_HELD.
5. All clear → proceed to Gate 1 (existing). Lock held for the whole
   orchestrated sequence; heartbeat during long ops; release in finally.
```

Gate 0 is NOT user-toggleable like Gates 1–3 (no gate0_enabled flag).
The only bypass is the time-boxed override.

---

## 5. Orchestrator Sequence (LOCKED)

`engine/core/orchestrator.py :: run_table_maintenance(fqn, dry_run) -> RunResult`

```
Gate 0 (above) → lock held
├─ capture_state()  → before₀ {metadata_location, current_snapshot_id, snapshot_count}
├─ OPTIMIZE (existing hk op)          → verify_advanced(before₀, after₁, "optimize")
├─ EXPIRE SNAPSHOTS (existing vacuum, + SNAPSHOT_MIN_AGE_HOURS clamp)
│                                      → verify_advanced(after₁, after₂, "expire")
├─ REMOVE ORPHANS — two-phase:
│    a. estimate scope (Iceberg $files / $snapshots metadata via Athena)
│    b. sanity: scope_pct > MAX_ORPHAN_DELETE_PCT → ABORT
│       (aborted_reason='ORPHAN_SANITY_ABORT', SNS alert, vacuum_audit row) 
│    c. delete with older_than = max(policy, ORPHAN_MIN_AGE_HOURS_FLOOR)
│                                      → verify + vacuum_audit row
└─ finally: release lock. ANY verify failure → integrity_status=FAILED,
   circuit_breaker.trip(fqn), SNS alert, halt remaining steps.
```

Rules:
- Strictly sequential. NEVER clock-spaced. Each step starts only after the
  previous step's commit is verified.
- Every step writes execution_log with lock_id + metadata before/after +
  snapshot ids + integrity_status (through BOTH log writer modes).
- Existing `engine/operations/vacuum.py` (12 gap types, partition-aware) and
  the v2 features (idempotency, backpressure, property sync) are EXTENDED,
  not replaced. Orchestrator calls into them; pre-flight and verification
  wrap them. Workgroup routing must pass through the existing backpressure
  mapping unchanged.

---

## 6. API Contract — LOCKED (FastAPI, Phase 2)

Conventions: all responses `{"data": ..., "pagination": {page,size,total}?, "error": null|{code,message}}`.
All mutations accept `dry_run: bool = true` and return `{"success", "dry_run", "audit_id"}`.
Auth: `Depends(get_current_user)` on every route; actor flows into audit.
Errors: HTTPException with the same envelope. OpenAPI docs at /docs.

### routers/tables.py
```
GET    /api/tables                     ?page&size&domain&layer&tier&env&search  (paged)
GET    /api/tables/{fqn}
POST   /api/tables/register            RegisterTableRequest (all register_table() params)
PUT    /api/tables/{fqn}               same shape, partial update
POST   /api/tables/bulk-controlm       {filters, set_fields, dry_run}
POST   /api/tables/job-mapping/import  CSV upload (multipart) → per-row match report
GET    /api/tables/job-mapping/export  → CSV stream
GET    /api/glue/databases
GET    /api/glue/tables/{db}           ?pattern&unregistered_only
```

### routers/policies.py
```
GET    /api/policies                   paged view (registry ⋈ hk_config, gate cols)
GET    /api/policies/{fqn}
PUT    /api/policies/{fqn}             full hk_config update incl. gates + window + blackout
GET    /api/templates
PUT    /api/templates/{name}
POST   /api/templates/{name}/apply     {domain?, layer?, tier?, dry_run} → affected count
```

### routers/gates.py
```
GET    /api/gates/{fqn}                gate flags + override state + conflict cache
PUT    /api/gates/{fqn}                {gate1..3_enabled?, gate0_override_until?,
                                        gate0_override_reason?} (override capped at
                                        GATE0_OVERRIDE_MAX_HOURS; reason required)
GET    /api/conflicts                  Dual-Optimizer Risk Report: hk_enabled AND any
                                       aws_opt_* true; paged; ?export=csv
POST   /api/conflicts/rescan           {fqns?: [..]} empty = full fleet scan (async ok)
```

### routers/lifecycle.py
```
GET    /api/nonprod                    ?env&state&page&size
POST   /api/nonprod/exempt             {fqns:[...], reason, dry_run}
POST   /api/nonprod/claim              {fqns:[...], reason, dry_run}
GET    /api/nonprod/deletions          paged deletion history
```

### routers/executions.py
```
GET    /api/executions                 ?fqn&engine&status&from&to&page&size
GET    /api/executions/{id}
GET    /api/dryrun/{fqn}               gate summary + planned ops (existing viewer logic)
GET    /api/health/kpis                home + health dashboard numbers
GET    /api/costs                      ?group_by=domain|layer|tier&from&to
GET    /api/stale                      ?kind=hk|orphan|zero_row|nonprod&filters
```

### routers/controlm.py
```
GET    /api/jobs                       ?search
POST   /api/jobs                       upsert single
POST   /api/jobs/import                CSV upload, upsert-all
DELETE /api/jobs/{name}
```

### routers/settings_router.py
```
GET    /api/settings                   platform settings
PUT    /api/settings
GET    /api/escalation
POST   /api/escalation                 {key, entry}
PUT    /api/escalation/{key}
DELETE /api/escalation/{key}
GET    /api/audit                      ?actor&action&from&to&page&size
```

### routers/system.py
```
GET    /api/system/mode                {mode, app_env, dry_run_default}
GET    /api/system/health              engine/db/aws reachability
GET    /api/locks                      active maintenance locks (DynamoDB scan / SQLite)
DELETE /api/locks/{fqn}                admin force-release (audited)
```

---

## 7. React Conventions — LOCKED (Phases 3–5)

```
ui/
├── index.html  vite.config.ts  tsconfig.json  package.json
└── src/
    ├── main.tsx            # QueryClientProvider + ConfigProvider(theme) + Router
    ├── App.tsx             # AntD Layout: Sider nav + Header + Content
    ├── theme.ts            # LOCKED tokens below
    ├── api/
    │   ├── client.ts       # fetch wrapper: baseURL /api, envelope unwrap, error toast
    │   ├── types.ts        # TS interfaces mirroring Pydantic models
    │   └── hooks/          # one file per router: useTables.ts, usePolicies.ts, ...
    ├── components/
    │   ├── DataGrid.tsx    # AntD <Table> wrapper: server pagination, size changer
    │   │                   # [15,25,50,100,250], loading skeleton, rowSelection opt-in
    │   ├── PageHeader.tsx  # title + subtitle + actions slot
    │   ├── StateBadge.tsx  # lifecycle/gate/status tag colors
    │   ├── DryRunBanner.tsx# global banner when dry_run default is on
    │   └── CsvButtons.tsx  # import (Upload) / export (download) pair
    └── pages/
        ├── Home/           # THE CANONICAL PATTERN — built completely in Phase 3
        ├── TableRegistration/  PolicyConfig/  NonProdLifecycle/
        ├── HealthDashboard/    LiveActivity/  ExecutionLog/  CostReport/
        ├── DryRunViewer/       StaleResources/ DomainManagement/
        ├── Settings/           AuditLog/
        └── (each: index.tsx + hooks.ts + local components/ if needed)
```

theme.ts (starting point — Sujith tunes in Phase 3 acceptance):
```ts
export const zamboniTheme = {
  token: {
    colorPrimary: '#00539F',        // AAA navy
    colorInfo: '#00539F',
    borderRadius: 4,
    fontFamily: "'Inter', -apple-system, 'Segoe UI', sans-serif",
    colorBgLayout: '#f5f7fa',       // light gray canvas
    colorBgContainer: '#ffffff',    // white cards/tables
    fontSize: 13,
  },
  components: {
    Table:  { headerBg: '#f0f4f8', headerColor: '#1e3a5f', cellPaddingBlock: 8 },
    Layout: { siderBg: '#ffffff', headerBg: '#ffffff' },
    Menu:   { itemSelectedBg: '#e6f0fa', itemSelectedColor: '#00539F' },
  },
} as const;
```

Pattern rules (Waves 1–2 replicate Home EXACTLY):
- TanStack Query per endpoint, `staleTime: 30_000`; mutations invalidate the
  matching query keys (replaces cached_read_registry.clear()).
- Loading = Skeleton, error = Alert with retry, empty = Empty with hint.
- Every table through <DataGrid>; never raw <Table>.
- Every mutation: confirm modal when destructive, dry_run passed from global
  context, success message shows audit_id.
- No new patterns invented in Waves 1–2. Deviation requires a note in
  decisions.md and Sujith's ok.

---

## 8. Deployment Contract (Phase 6 — extends EXISTING stack)

Existing assets (from Sujith's changelog — read, don't recreate):
`deploy/zamboni-cfn.yaml` (full stack), CodePipeline + CodeDeploy wiring,
buildspec, EC2 tag filtering, IAM naming conventions, encrypted EBS,
CW log groups.

Phase 6 EXTENDS them:
1. buildspec: add Node 20 install → `cd ui && npm ci && npm run build`;
   artifact includes `ui/dist/`.
2. New systemd unit `zamboni-api.service`: uvicorn `api.main:app`
   --host 0.0.0.0 --port 8000, EnvironmentFile=.env, Restart=on-failure.
   Streamlit unit untouched until cutover sign-off.
3. CFN additions: DynamoDB lock table (3.1), IAM policy additions (3.4).
4. Laptop demo scripts: `run_aws_local.ps1` = aws sso login → export
   ZAMBONI_MODE=aws_local → uvicorn (serving built ui/dist) → open browser.
   Plus `run_ui_dev.ps1` = uvicorn + `npm run dev` (Vite :5173 proxy → :8000)
   for development.

---

## 9. Acceptance Baseline (every phase)

```
python -m pytest tests/unit/ -q        # ≥ current pass count, zero failures
ruff check .                            # All checks passed!
```
Plus phase-specific behavioral assertions listed in each phase prompt.
Every phase ends by appending a dated entry to `.claude/CLAUDE.md` under
`## Migration Progress`: what shipped, files touched, new test count,
anything deferred.

---

## 10. Personal-First Development & Clean Org Drop (LOCKED workflow)

The program is BUILT and TESTED entirely on Sujith's personal repo
(github.com/sujithmsaji/zamboni via claude.ai sessions + personal laptop).
Delivery to the org repo is a **clean drop**: the finished codebase is
committed to the org repo as a NEW branch (`new-phase1` or `enhancement`) —
NO merging into the existing org branch, NO patch surgery, NO conflict
resolution. Decided 2026-07-05 for time-to-showcase.

Rules:
- **R10.1** The personal repo is the sole build venue for Phases 0–6. Org
  code never enters these sessions.
- **R10.2** The old org branch is retired from active development. Its
  exclusive features (vacuum 12-gap-type hardening, Parquet log writer,
  CloudTrail signals) are NOT rebuilt here; they are candidates for a
  post-showcase re-port done ORG-SIDE (Bedrock Claude can read both branches
  there). Phase 0 records them in `.claude/org_divergence.md` as the re-port
  backlog — nothing more.
- **R10.3** Phase 6 authors a COMPLETE standalone CloudFormation template
  (deploy/zamboni-cfn.yaml) from scratch here — EC2 + IAM + DynamoDB lock
  table + SGs + CodePipeline/CodeDeploy skeleton — parameterized so org-side
  Bedrock only adapts naming/tags/VPC params to org conventions.
- **R10.4** Phase 7 (org laptop, Bedrock Sonnet 5 or 4.6): create branch →
  drop code → adapt CFN to org conventions → deploy → smoke test. No merge.
- **R10.5** Demo machine = wherever `aws sso login --profile
  prod-toolsgenai-sso` works. Phase 7 completes before Jul 15 rehearsals.
