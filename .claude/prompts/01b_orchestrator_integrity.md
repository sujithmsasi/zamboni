# PHASE 1b — Orchestrator, Integrity Checker, Safe Vacuum

Context: Phase 1a shipped lock_service, conflict_detector, Gate 0, schema.
This phase makes maintenance completion-serialized and commit-verified — the
direct fix for the metadata-loss incident. NOTE (contracts §10): this repo
IS the delivery codebase (clean drop to org — no merging). The org branch's
richer vacuum (12 gap types) is a post-showcase re-port candidate, not a
dependency. Build `engine/core/maintenance_ops.py` as a clean ops module
exposing `run_optimize(fqn,...)`, `run_expire(fqn,...)`,
`run_orphan_delete(fqn,...)`, implemented over whatever vacuum/expiry/optimize
ops THIS repo has (Phase 0 report says where). All safety logic (floors,
two-phase orphan, sanity abort) lives in this module so a future vacuum
upgrade slots in beneath it without touching the safety layer.

## Read first
.claude/CLAUDE.md (Migration Progress), .claude/contracts.md (§2, §3.3, §5),
engine/operations/vacuum.py (fully), engine/engines/hk_engine.py (Gate 0 +
existing op flow), engine/core/lock_service.py, engine/core/conflict_detector.py,
the execution_log writer (both modes), engine/core/circuit_breaker.py,
the backpressure/workgroup mapping code Phase 0 identified.

## Tasks

### 1. engine/core/integrity_checker.py
```python
def capture_state(fqn) -> TableState   # {metadata_location, current_snapshot_id, snapshot_count, captured_at}
def verify_advanced(before, after, operation) -> IntegrityResult
```
- Sources: Glue get_table → Parameters['metadata_location']; snapshot info via
  Athena on "{fqn}$snapshots" (count, max committed_at/snapshot_id). In local
  mode return a stub state (documented) so orchestration is testable.
- verify_advanced asserts per operation:
  optimize/expire → metadata_location CHANGED and after.snapshot chain moved
  forward (new current_snapshot_id OR snapshot_count changed in the expected
  direction: optimize ≥, expire ≤); orphan → metadata_location UNCHANGED
  (orphan delete must never move the pointer — if it did, FAIL loudly).
- Any failed assertion → IntegrityResult(status='FAILED', detail=...).

### 2. Safe vacuum wrappers (inside engine/core/maintenance_ops.py)
Read how expiry + orphan deletion are invoked in THIS repo (Phase 0 report).
All wrappers below live in this module. Then:
- **Snapshot expiry clamp**: wherever retention (days/count) is resolved,
  enforce SNAPSHOT_MIN_AGE_HOURS as a final floor — never expire a snapshot
  younger than the floor regardless of policy. If the mechanism is Athena
  VACUUM via table properties, clamp the properties you set
  (vacuum_max_snapshot_age_seconds ≥ floor, vacuum_min_snapshots_to_keep ≥ 1)
  before running, and restore/record what you changed.
- **Two-phase orphan** (contracts §5):
  a. estimate: query "{fqn}$files" count (and $snapshots) via Athena to compute
     the table's live data-file count; estimate deletion scope with the
     mechanism available (if the implementation can list candidates, use it;
     if it's property-driven Athena VACUUM, compute scope as
     files_referenced_only_by_expiring_snapshots best-effort and document the
     approximation in decisions.md).
  b. sanity: scope_pct > MAX_ORPHAN_DELETE_PCT → ABORT: write vacuum_audit row
     (aborted=True, aborted_reason='ORPHAN_SANITY_ABORT', sanity_pct), SNS via
     the existing alert path, trip circuit breaker, return without deleting.
  c. delete with older_than = clamp_orphan_age(policy) (≥72h floor).
- Implement the new execution_log columns for whichever writer mode(s) this
  repo has. Every expiry/orphan run writes a **vacuum_audit** row (create the Iceberg DDL
  per contracts §3.3 in the Athena DDL location; SQLite mirror table + seed
  migration). Local mode writes to SQLite.

### 3. engine/core/orchestrator.py (contracts §5 — the sequence is LOCKED)
```python
def run_table_maintenance(fqn: str, dry_run: bool = True) -> RunResult
```
- Gate 0 (from 1a) → hold lock; heartbeat via a lightweight timer/thread or
  explicit heartbeat() calls between steps (choose the simpler: explicit calls
  before each step + during long Athena polls if the poll loop is accessible).
- Sequence: capture → OPTIMIZE (existing hk op path, honoring existing gates
  1–3 and backpressure/workgroup mapping UNCHANGED) → verify → capture →
  EXPIRE (clamped) → verify → ORPHAN two-phase → verify(pointer-unchanged) →
  release (finally).
- Each step writes execution_log with lock_id, metadata before/after, snapshot
  ids, integrity_status — through BOTH writer modes.
- Any verify FAILED or step exception: set integrity_status='FAILED', trip
  circuit_breaker(fqn), SNS alert with before/after metadata locations (this is
  the recovery breadcrumb), halt remaining steps, release lock.
- dry_run: full sequence with all writes as dry-run (existing convention),
  integrity_status='SKIPPED', no lock steal risk (still acquire/release lock —
  dry runs must not overlap real runs either).
- Wire-in: give hk_engine (or the batch runner Phase 0 identified) an
  opt-in path to orchestrated mode via setting
  `ORCHESTRATED_MAINTENANCE = os.getenv("ORCHESTRATED_MAINTENANCE","true")` —
  default true; false preserves the pre-existing flow untouched (rollback
  lever for you, not a design fork).

### 4. Tests (tests/unit/test_orchestrator.py, test_integrity.py, extend vacuum tests)
- verify_advanced matrix: advanced-ok, pointer-unchanged-fail (optimize),
  pointer-CHANGED-fail (orphan), snapshot-count-direction checks.
- Orphan sanity: mock estimate → 25% → abort row written, breaker tripped,
  nothing deleted; 5% → proceeds with clamped older_than (assert ≥72h even
  when policy says 24h).
- Snapshot floor: policy 1h → effective ≥ SNAPSHOT_MIN_AGE_HOURS.
- Orchestrator: happy path calls steps in order under one lock_id; mid-step
  verify failure halts (later steps NOT called), lock released, breaker tripped;
  dry_run touches nothing real.
- Backpressure regression: existing workgroup mapping test still passes and
  orchestrated path routes through the same mapping (add one assertion).

## Acceptance criteria
- pytest full suite: prior count + new, ZERO failures. ruff clean.
- seed_local_db.py creates vacuum_audit; a local dry-run
  `python -c "from engine.core.orchestrator import run_table_maintenance as r; print(r('<seeded fqn>', dry_run=True))"`
  returns a RunResult with per-step statuses (paste output).
- Show the vacuum_audit row produced by the dry run (SELECT from SQLite).
- decisions.md updated with: the orphan-estimation approach actually used
  (given vacuum.py's real mechanism) and the 72h-floor = rollback-window rationale.
- Migration Progress entry appended.

## Do NOT
- Recreate org-only components (vacuum gap types, Parquet writer) — contracts
  §10 R10.2; they're a post-showcase org-side re-port, not this phase's job.
- Bypass backpressure/workgroup mapping.
- Introduce clock-based spacing anywhere.

Suggested commit: `feat(engine): completion-serialized orchestrator, integrity verification, safe vacuum floors + vacuum_audit`
