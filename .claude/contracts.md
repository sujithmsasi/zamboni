# Zamboni Replatform — LOCKED CONTRACTS
# This file is committed as .claude/contracts.md in Phase 0.
# Every phase prompt instructs Sonnet 5 to read this file FIRST.
# Nothing here is negotiable mid-build. Changes require Sujith's explicit sign-off.
#
# > PHASE 0 RECONCILIATION (2026-07-05): annotated inline with `> REALITY:`
# > blockquotes wherever this document's assumptions differ from what's
# > actually in the repo. No locked decision (D1-D6) below has been changed.
# > See the Conflict List at the bottom of this file for items that need
# > Sujith's sign-off before Phase 1a starts.

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

> REALITY: confirmed genuinely greenfield — no lock service, no DynamoDB usage,
> no Gate 0, no `get_mode()`/`get_boto3_session()` exist anywhere in the repo
> (repo-wide grep, Phase 0). Workstream A really does start from zero.

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

> REALITY: D2's 72h floor never actually binds given current commit-frequency
> tiers (`engine/core/commit_frequency.py:36-55` — HIGH/MEDIUM/LOW retention
> floors are 7d/14d/30d, all already ≥ 72h). The mechanism conflict is what
> matters here, not the number — see the §5 REALITY note below.

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

> REALITY: `config/settings.py` today already defines an *unwired* constant
> `ORPHAN_MIN_RETENTION_HOURS = 48` (line 103, "Never delete files newer than
> 48h") that is asserted in `tests/unit/test_settings.py` and
> `tests/unit/test_vacuum.py` but never actually consumed by
> `engine/operations/vacuum.py` — the real floor is TBLPROPERTIES-driven (see
> §5). Phase 1a should decide whether `ORPHAN_MIN_AGE_HOURS_FLOOR` (72)
> replaces/coexists with `ORPHAN_MIN_RETENTION_HOURS` (48) — two similarly-
> named orphan-floor constants inviting confusion otherwise.

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

> REALITY: NOT PRESENT — confirmed absent by repo-wide grep for `get_mode`,
> `get_boto3_session`, `ZAMBONI_MODE`, `aws_local`, `aws_ec2` (only hits were
> in this contracts doc and the phase prompts themselves). Today's code only
> has `ZAMBONI_LOCAL_MODE` (bool) and `ZAMBONI_LOCAL_DB` in
> `config/settings.py:118-119`. Phase 1a builds this fresh, exactly as
> written above — no existing function to reconcile against.

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

> REALITY: "existing `deploy/zamboni-cfn.yaml`" does not exist (see §8
> REALITY note) — there is no CFN file to add this resource to yet. Phase
> 1a's fallback path (`scripts/create_lock_table.py`, manual creation) is
> the only currently-viable option; Phase 6 is authoring the CFN from
> scratch per §10 R10.3 anyway, so this resource just needs to be included
> in that fresh template rather than "added to" a prior one.

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

> REALITY: confirmed none of `aws_opt_*`, `gate0_override_*`, `lock_id`,
> `metadata_location_before/after`, `snapshot_id_before/after`,
> `integrity_status` exist yet in `scripts/seed_local_db.py` — clean to add.
> Confirmed the Parquet-writer warning is correct and important: new columns
> must be added to both `engine/core/execution_log.py::write()` (positional
> INSERT, line 117) and `engine/core/execution_log_parquet.py::ParquetLogBuffer.
> _entry_to_dict()` (line 210) or Parquet-mode rows will silently omit them.

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

> REALITY: `older_than_hours_used` here assumes an orphan delete call that
> takes an age parameter — see the §5 REALITY note. This column can still be
> populated (as "the TBLPROPERTIES age value in effect at delete time"), just
> not as a literal argument passed to a delete call, since none exists.

### 3.4 IAM additions (documented in Phase 1a, added to CFN in Phase 6)

```
dynamodb: PutItem, GetItem, UpdateItem, DeleteItem, DescribeTable
          on arn:aws:dynamodb:*:*:table/zamboni_maintenance_locks
glue:     GetTableOptimizer, BatchGetTableOptimizer, ListTableOptimizerRuns
```

> REALITY: `deploy/iam_policy.json` (the current EC2 instance role policy)
> has no DynamoDB or `glue:GetTableOptimizer`/`BatchGetTableOptimizer`/
> `ListTableOptimizerRuns` statements — these are genuinely new additions,
> not present under a different Sid.

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

> REALITY: confirmed `engine/engines/hk_engine.py` only implements gate1
> (line 268 — Control-M dependency), gate2 (line 294 — blackout window),
> gate3 (line 329 — circuit breaker). No gate0 concept anywhere. This
> section is pure greenfield for Phase 1a, nothing to reconcile.

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

> REALITY — the most important reconciliation in this file:
> `engine/operations/vacuum.py` is NOT 12-gap-type/partition-aware in this
> repo — it has 5 named gaps (1,2,3,9,10) and issues a single bare
> `VACUUM db.table;` per table that does snapshot expiry AND orphan removal
> TOGETHER in one Athena call, with retention controlled only via
> TBLPROPERTIES set in advance by `property_sync.py`
> (`vacuum_max_snapshot_age_seconds`, etc. — `engine/core/commit_frequency.py:36-55`).
> There is no separate orphan-only delete call and no `older_than` parameter
> accepted anywhere in the vacuum call path (`engine/operations/vacuum.py:1-20`
> hard-rule comment: "Bare VACUUM only. No clauses, no options.").
>
> This means the "REMOVE ORPHANS — two-phase" step as literally written
> (estimate → sanity-check → delete-with-older_than) cannot be implemented
> as a standalone call against the existing `vacuum.py` functions — orphan
> removal isn't separable from snapshot expiry at call time in this engine.
> Two ways to reconcile, needing Sujith's pick before Phase 1a proceeds:
>   (a) Treat "delete with older_than" as "tighten TBLPROPERTIES to the
>       floor-clamped value immediately before the single VACUUM call" —
>       estimate/sanity-check still run as pre-flight reads against
>       `$files`/`$snapshots`, but the "delete" step becomes the same bare
>       VACUUM call vacuum.py already makes, now preceded by a
>       property-tightening step.
>   (b) Treat vacuum.py's 12-gap-type/partition-aware version (referenced in
>       the org changelog, see `.claude/org_divergence.md`) as a prerequisite
>       re-port before Gate 0/orchestrator work, since that version may
>       already separate orphan deletion from snapshot expiry with an actual
>       age parameter. This would mean re-scoping some org-divergence-backlog
>       work into Workstream A rather than deferring it post-showcase.
> This phase does not choose between (a) and (b) — flagged for sign-off.
>
> Separately: the "12 gap types, partition-aware" vacuum.py referenced here
> is the ORG-ONLY version per `.claude/org_divergence.md` and SHARED_CONTRACTS
> §10/R10.2 — this paragraph in §5 and that backlog entry describe the same
> file two different ways. Confirm which one is authoritative for Workstream
> A before Phase 1b implements orchestrator step wiring.

## 5-A. Athena VACUUM Adaptation (RULING, Sujith 2026-07-05 — supersedes §5's
##      three-step orphan design; discovered in Phase 0 conflict list)

Reality: this repo's vacuum.py issues a single Athena `VACUUM` (combined
snapshot-expiry + orphan-file removal), governed by Iceberg table properties,
no older_than parameter, and the metadata pointer ADVANCES on VACUUM.

Adapted orchestrator sequence (replaces §5 steps 2–3):
  OPTIMIZE → verify_advanced
  → SAFE-VACUUM:
     a. PROPERTY CLAMP (the floors live here now): before VACUUM, read current
        table properties and ALTER TABLE SET TBLPROPERTIES so that
        vacuum_max_snapshot_age_seconds >= max(policy_seconds,
        ORPHAN_MIN_AGE_HOURS_FLOOR*3600) and >= SNAPSHOT_MIN_AGE_HOURS*3600,
        and vacuum_min_snapshots_to_keep >= max(policy, 1).
        Clamped values PERSIST on the table (they ARE the safety floors);
        before/after property sets recorded in vacuum_audit + execution_log.
     b. PRE-FLIGHT SANITY (two-phase becomes estimate-then-run): query
        "{fqn}$snapshots" and "{fqn}$files" — would_expire_pct = snapshots
        older than effective age / total snapshots. If would_expire_pct >
        MAX_ORPHAN_DELETE_PCT → ABORT (ORPHAN_SANITY_ABORT: vacuum_audit row,
        SNS, circuit-breaker trip). Also capture files_count + total_bytes
        before.
     c. RUN VACUUM → verify: pointer ADVANCED (expected for VACUUM — §5's
        "pointer unchanged for orphan" rule is void under combined VACUUM),
        snapshot_count decreased-or-equal, current snapshot age >= floor.
     d. POST-AUDIT: re-query $files → files/bytes delta → vacuum_audit row.
verify_advanced gains operation='vacuum' semantics per (c).
integrity FAILED anywhere → trip breaker, SNS, halt, release lock (unchanged).

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

> ADDED (2026-07-17): registering N tables via N calls to
> POST /api/tables/register cost N synchronous Athena audit_log INSERTs
> (~2-3s each, one per HTTP request) -- reported as 78 tables taking 2-3
> minutes through Browse & Register's client-side loop. See
> api/services/tables_svc.py::register_tables_bulk()/
> api/models.py::RegisterTablesBulkRequest's docstrings.
POST   /api/tables/register-bulk       RegisterTablesBulkRequest (shared
                                        fields + tables: [{table_fqn,
                                        table_format}]) → {results, registered,
                                        failed}, one batched audit.persist_many()
                                        call instead of N Athena round trips
```

### routers/policies.py
```
GET    /api/policies                   paged view (registry ⋈ hk_config, gate cols)
GET    /api/policies/{fqn}
PUT    /api/policies/{fqn}             full hk_config update incl. gates + window + blackout
GET    /api/templates
PUT    /api/templates/{name}
POST   /api/templates/{name}/apply     {domain?, layer?, tier?, dry_run} → affected count

> ADDED (Phase 5a): this section only locked GET/PUT for templates -- the
> Policy Configuration page's "Add Template"/"Delete Template" sub-tabs
> (3_Policy_Configuration.py tmpl_tab_add/tmpl_tab_del) have no equivalent
> contract endpoint. Same precedent as Phase 4's domains router: additive,
> same envelope/dry_run/audit conventions. See
> api/services/policies_svc.py::create_template()/delete_template().
POST   /api/templates                  TemplateCreateRequest (name + all template fields)
DELETE /api/templates/{name}           ?dry_run  -- blocked if built-in or in use by any table
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
> ADDED (Phase 5b): `GET /api/lifecycle/config` -- backs the State Overview
> tab's "How are stale tables identified?" explainer with the engine's real
> DEFAULT_STALE_DAYS/DEFAULT_GREENZONE_DAYS/DEFAULT_PENDING_DROP_DAYS
> constants (engine/engines/lifecycle_engine.py) instead of hardcoding the
> numbers client-side, where they could silently drift. Same precedent as
> every other additive route in this file.

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
GET    /api/jobs/{name}/tables         tables referencing this job (any of the 3 role columns)
DELETE /api/jobs/{name}
```
> ADDED (Control-M Integration follow-up, 2026-07-08): `GET /api/jobs/
> {name}/tables` -- backs the Job List "Tables Mapped" count's drill-in
> popup. Not in the original lock; same precedent as Phase 4's domains
> router and Phase 5a's template create/delete routes.

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

### routers/domains.py
```
> ADDED (Phase 4): this section did not exist in the original Phase 0 lock --
> contracts.md §6 had no domains router at all. Phase 4's DomainManagement
> page needs domain CRUD (parity with app/pages/1_Domain_Management.py), so
> these 4 routes were added then, following the same envelope/dry_run/audit
> conventions as every other router. See api/services/domains_svc.py.
GET    /api/domains            ?active_only
GET    /api/domains/{name}
POST   /api/domains            RegisterDomainRequest (all register_domain() params)
PUT    /api/domains/{name}     partial update (all Streamlit edit-form fields)
```

### routers/system.py
```
GET    /api/system/mode                {mode, app_env, dry_run_default}
GET    /api/system/health              engine/db/aws reachability
GET    /api/locks                      active maintenance locks (DynamoDB scan / SQLite)
DELETE /api/locks/{fqn}                admin force-release (audited)
```

> REALITY: not evaluated in Phase 0 (app-code/API layer doesn't exist yet —
> this is Phase 2 scope). No conflicts to flag; all underlying
> registry/hk_config/execution_log/escalation/controlm functions this API
> would wrap do already exist in `engine/core/` and `app/components/ctrlm_helper.py`.

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

> REALITY: no `ui/` directory exists yet — confirmed clean slate, nothing to
> reconcile. Note the current Streamlit app's branding (fixed 48px topbar,
> Zamboni logo, Inter font per the phase 0 changelog) already uses Inter —
> consistent with theme.ts's fontFamily choice above.

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

> REALITY: `deploy/zamboni-cfn.yaml` does NOT exist — confirmed via glob,
> no `.yaml` files anywhere under `deploy/`. What does exist:
> `deploy/buildspec.yml` (CodeBuild: install → lint/unit tests → integration
> tests → package, no Node/npm steps), `deploy/appspec.yml` +
> `deploy/scripts/{before_install,after_install,app_start}.sh` (CodeDeploy
> hooks: stop Streamlit → copy to /opt/zamboni → pip install → restart
> Streamlit), `deploy/iam_policy.json` (EC2 instance role — no DynamoDB, no
> GetTableOptimizer actions), `deploy/ec2-trust-policy.json`,
> `deploy/cloudwatch/{alarms,dashboard}.json`, `deploy/setup_ec2.sh`,
> `deploy/pipeline_config.md` (263 lines — likely documents the intended
> CodePipeline wiring in prose even though the CFN itself isn't written).
> This is not a blocker: §10 R10.3 already commits Phase 6 to authoring a
> COMPLETE standalone CFN from scratch. Just don't describe it to Sujith or
> in the Phase 6 session as "extending an existing template" — there isn't
> one. The Node/buildspec and systemd/appspec additions in points 1-2 above
> are real net-new additions to files that do exist.

---

## 9. Acceptance Baseline (every phase)

```
python -m pytest tests/unit/ -q        # ≥ current pass count, zero failures
ruff check .                            # All checks passed!
```
Plus phase-specific behavioral assertions listed in each phase prompt.

> REALITY: Phase 0 baseline recorded — `pytest tests/unit/ -q` → 494 passed
> in 33.78s; `ruff check .` → All checks passed! Matches the ≥494 expectation
> in `00_phase0_repo_audit.md` exactly.

---

## Conflict List (Phase 0 — for Sujith's sign-off)

1. **Vacuum mechanism vs. orchestrator design (§5)** — HIGH severity, blocks
   Phase 1b design. `vacuum.py` does snapshot-expiry + orphan-removal in one
   bare `VACUUM` call, TBLPROPERTIES-driven, no per-call `older_than`
   parameter. Contracts §5's "REMOVE ORPHANS — two-phase ... delete with
   older_than=..." step assumes a separable, parameterized orphan-delete
   call that doesn't exist. Needs a pick between: (a) reinterpret "delete
   with older_than" as "tighten TBLPROPERTIES to the floor-clamped value,
   then run the existing bare VACUUM", or (b) treat the org-side 12-gap
   vacuum.py (which may have a real orphan/snapshot split) as a prerequisite
   re-port into Workstream A rather than post-showcase backlog.
2. **"12 gap types" vacuum.py — contradictory framing across documents** —
   MEDIUM severity, informational once (1) is resolved. `00_phase0_repo_audit.md`
   and contracts §5 describe the personal repo's vacuum.py as already having
   "12 gap types, partition-type-aware" logic; contracts describes that exact feature as absent here.
   Phase 0 confirms this framing is correct for this repo —
   5 gaps only, no partition awareness. Recommend updating future phase
   prompts to stop referencing "12 gap types" as already-present.
3. **Two orphan-floor constants** — LOW severity. `config/settings.py:103`
   already has `ORPHAN_MIN_RETENTION_HOURS = 48` (asserted in 2 existing
   tests) which is unwired/aspirational; contracts §2 introduces
   `ORPHAN_MIN_AGE_HOURS_FLOOR = 72`. Recommend Phase 1a either retires the
   old constant (updating its 2 tests) or documents why both exist.
4. **"Existing deploy/zamboni-cfn.yaml" — does not exist** — LOW severity,
   no action blocked since §10 R10.3 already plans a from-scratch CFN.
   Recommend not describing Phase 6 to stakeholders as "extending" a CFN
   stack — there's CodeBuild/CodeDeploy scaffolding and an IAM policy JSON,
   but no CloudFormation template anywhere in the repo today.
5. **Changelog overstates org-exclusivity of two already-present features** —
   LOW severity, informational. The Phase 0 changelog lists "Parquet log
   writer for execution_log" and "CloudTrail activity signals" as org-only
   features absent from this repo. Both are ALREADY IMPLEMENTED here:
   `engine/core/execution_log_parquet.py::ParquetLogBuffer` and
   `engine/monitoring/activity_scanner.py::_query_cloudtrail()`. Recorded in
   `.claude/org_divergence.md` to correct the record — no work is queued for
   these two items.
6. **Legacy `.claude/*.md` memory files never existed** — LOW severity,
   informational. This phase's brief assumed a stale-but-present set of
   memory files to refresh; `git log --all` shows none were ever committed.
   Handled as a first write; flagging so future phase prompts don't
   over-assume prior-session continuity that isn't in this repo's history.
