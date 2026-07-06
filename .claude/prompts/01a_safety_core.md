# PHASE 1a — Safety Core: Lock Service, Conflict Detector, Gate 0

Context: AWS Glue table optimizer runs (compaction/retention/orphan-deletion
spaced 5–10 min) corrupted Iceberg metadata. Root cause: clock-spacing instead
of completion-serialization. This phase builds the coordination primitives.
Phase 0 already re-baselined .claude files and committed contracts.md.

## Read first
.claude/CLAUDE.md (incl. Migration Progress), .claude/contracts.md (§1–§4),
.claude/context_hints.md, engine/engines/hk_engine.py, engine/operations/vacuum.py,
config/settings.py, scripts/seed_local_db.py, the Phase 0 delta report notes in
CLAUDE.md, engine/utils/athena_client.py, engine/utils/glue_client.py.

## Tasks

### 1. Mode/session factory (contracts §2)
If Phase 0 reported get_mode()/get_boto3_session() missing, implement exactly
per contracts §2 in config/settings.py (or engine/utils/aws_session.py if a
session helper already exists — extend it). All new boto3 clients in this phase
MUST go through it. Backward-compat with ZAMBONI_LOCAL_MODE preserved.

### 2. Settings constants (contracts §2)
Add all maintenance-safety constants to config/settings.py verbatim.

### 3. engine/core/lock_service.py (contracts §3.1)
```python
class LockService:
    def acquire(self, table_fqn, operation, ttl_min=LOCK_TTL_MINUTES) -> Lock|None
    def heartbeat(self, lock: Lock) -> bool
    def release(self, lock: Lock) -> None
```
- Backend by get_mode(): local → SQLite table maintenance_locks (create in
  seed_local_db.py DDL + migrations); aws_local/aws_ec2 → DynamoDB
  DDB_LOCK_TABLE via get_boto3_session().
- DynamoDB semantics EXACTLY per contracts §3.1 (conditional Put / conditional
  Update / conditional Delete). lock_owner = f"{hostname}:{pid}:{uuid4().hex[:8]}".
- SQLite: single transaction — DELETE expired rows, then INSERT; IntegrityError
  → held by other.
- scripts/create_lock_table.py: idempotent DynamoDB table creation with TTL on
  expires_at (for use before Phase 6 puts it in CFN). Uses get_boto3_session().
- Structured logs on every acquire/steal-expired/contend/release.

### 4. engine/core/conflict_detector.py
```python
def check_table(fqn) -> dict            # live Glue GetTableOptimizer x3 types
def get_cached(fqn) -> dict|None        # stream_registry aws_opt_* within TTL
def check_with_cache(fqn) -> dict       # cached, else live + write-back
def scan_fleet(fqns=None, batch=...)    # populate cache fleet-wide
```
- Glue API: get_table_optimizer(CatalogId?, DatabaseName, TableName, Type) for
  Type in {compaction, retention, orphan_file_deletion}; EntityNotFound /
  optimizer absent → enabled=False. Parse fqn safely (strip glue_catalog.).
- Write-back UPDATE to stream_registry aws_opt_* + aws_opt_checked_at through
  the existing write path (execute_write / run_query pattern) so local mode
  works too (local mode live-check: return all False, still stamp checked_at —
  document this in a docstring).

### 5. Schema migrations (contracts §3.2)
- scripts/seed_local_db.py: add columns to its DDL AND migrations list:
  stream_registry aws_opt_* (4), hk_config gate0_override_* (3),
  execution_log lock_id/metadata_before/after/snapshot ids/integrity_status (6),
  plus maintenance_locks table DDL.
- Wherever Phase 0 found the Athena DDL lives (scripts/ddl/ or similar): add
  matching ALTER TABLE ... ADD COLUMNS statements (idempotent guidance comment).
- Parquet log writer: extend its schema/columns for the 6 new execution_log
  fields with safe defaults (None/'SKIPPED') so existing writes don't break.

### 6. Gate 0 in engine/engines/hk_engine.py (contracts §4)
Insert BEFORE the existing Gate 1 block, following the file's existing gate
style (skip-reason logging + _write_log + return "skipped"):
- Order: override check → conflict check (check_with_cache) → in-flight check
  (execution_log RUNNING for fqn) → lock acquire. Store the acquired lock where
  the downstream flow (and Phase 1b orchestrator) can heartbeat/release it —
  design a small context object or return-tuple; keep it minimal and documented.
- Skip reasons: SKIP_AWS_OPTIMIZER_CONFLICT, SKIP_ALREADY_RUNNING, SKIP_LOCK_HELD.
- Overridden path logs GATE0_OVERRIDDEN with reason/actor into execution_log
  notes/skip_reason field (whichever exists) and proceeds.
- Ensure lock release in the existing finally/exception path of the table run
  (if none exists, add try/finally around the per-table processing).

### 7. Tests (new file tests/unit/test_safety_core.py)
- Lock: acquire→contend fails→release→acquire succeeds; expired lock stolen;
  heartbeat extends; wrong-owner release/heartbeat rejected. (SQLite backend;
  DynamoDB path unit-tested with botocore Stubber or moto if already a dep —
  check requirements first; if neither, Stubber.)
- Conflict: cached fresh → no live call; stale → live + write-back; any type
  enabled → conflict True.
- Gate 0: conflict → SKIP_AWS_OPTIMIZER_CONFLICT; override active → proceeds +
  GATE0_OVERRIDDEN logged; lock held → SKIP_LOCK_HELD; clean → lock acquired.
- Settings: orphan floor clamp helper max(policy, floor) — add tiny pure helper
  clamp_orphan_age(policy_hours) in settings or vacuum utils and test it.

## Acceptance criteria
- `python -m pytest tests/unit/ -q` → prior count + new tests, ZERO failures.
- `ruff check .` → clean.
- `python scripts/seed_local_db.py` succeeds; PRAGMA shows all new columns +
  maintenance_locks table.
- Behavioral self-check (run in local mode, show output): simulate two
  acquire() calls same fqn → second returns None; Gate 0 unit shows all four
  skip/pass paths.
- No changes to vacuum.py logic itself (that's 1b). No UI changes.
- Append Migration Progress entry to CLAUDE.md.

Suggested commit: `feat(engine): safety core — lock service, conflict detector, Gate 0`
