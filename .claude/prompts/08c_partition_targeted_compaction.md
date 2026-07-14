# PHASE 8c — Event-Derived Partition-Targeted Compaction

Context: this phase upgrades the compaction path Phase 8b's catalog-event
consumer dispatches into. It does NOT touch VACUUM (snapshot expiry/orphan
cleanup) at all — `vacuum.py`'s bare `VACUUM db.table;` has no predicate
support and that hard rule is unchanged. This phase only narrows what
`OPTIMIZE ... REWRITE DATA USING BIN_PACK` (and the sort/zorder strategies)
rewrite, from a heuristic to an event-verified fact.

**Sequencing: depends on Phase 8b's consumer/queue existing.** Do not build
a second queue or a second `CreatePartition`/`UpdatePartition`/
`BatchCreatePartition` subscription — this phase changes what 8b's consumer
*does* with those specific detail-types, reusing its coalescing/debounce
machinery. The idempotency-key extension in task 4 below is a **blocking
prerequisite** — do not enable event-derived targeting in production before
it lands and is tested.

## Read first
`08b_glue_catalog_event_triggers.md` (assumes it exists and reuses its
consumer), `08a_glue_job_event_triggers.md`, `engine/operations/compaction.py`,
`engine/utils/partition_utils.py::build_hot_partition_filter`,
`engine/strategies/{binpack,sort,zorder}.py`, `engine/core/idempotency.py`,
`engine/core/lock_service.py`, `engine/core/config.py` (`hk_config`'s
`partition_filter_days`/`processing_cadence`/`partition_type` fields),
`.claude/CLAUDE.md`'s Vacuum model and Idempotency facts.

## Known current state (verified, not to be re-derived)
- Partition-scoped compaction **already exists**: `compaction.py` builds a
  `partition_filter` via `build_hot_partition_filter()` and threads it
  through all three strategies (`binpack.py`/`sort.py`/`zorder.py` all
  accept `partition_filter: str | None`). What's missing is a way to
  populate that filter from a real event instead of a static date guess.
- `build_hot_partition_filter()` is a **calendar lookback heuristic**
  (`column >= <today - N days>`, type-aware for date/timestamp/
  int_yyyymmdd/string) driven by `hk_config.partition_filter_days` or
  `processing_cadence`. For `partition_type in {identity, none}` it
  returns `None` — those tables get NO partition scoping today, always
  full-table `OPTIMIZE`. It also cannot see a backfill into an old
  partition, since it only ever looks forward from "today."
- `engine/core/idempotency.py::build_execution_id(run_id, table_fqn,
  operation, window_id)` hashes only `table_fqn|operation|window_id` —
  `run_id` is deliberately excluded (cross-trigger dedupe). Both current
  call sites (`orchestrator.py:195-196`, `hk_engine.py:497-498`) call
  `get_window_id()` with no arguments, which defaults to
  `datetime.now(UTC).strftime("%Y%m%d")`, and both pass
  `operation="hk_run"` — the whole per-table run is one dedupe unit, not
  per-operation. **This is the exact mechanism that would silently
  collapse two different same-day partition-targeted dispatches for the
  same table into one execution_id, dropping the second.**
- `LockService.acquire()` is keyed by `table_fqn` only, not
  `table_fqn+partition`. That stays true here — see task 7.

## Required architecture

### 1. `engine/utils/partition_utils.py::build_event_partition_filter()`
New function, sibling to `build_hot_partition_filter()`, reusing its
existing type-aware literal-formatting logic (don't duplicate the
date/timestamp/int_yyyymmdd/string quoting rules — factor them out into a
shared literal-formatter both functions call):
```python
def build_event_partition_filter(
    partition_columns: list[str],
    partition_type: str | None,
    partition_value_tuples: list[tuple[str, ...]],
) -> str | None:
```
- Single-column partitions: `partition_col IN (val1, val2, ...)`.
- Composite/multi-column partitions: OR of ANDed equality groups —
  `(col1=v1a AND col2=v2a) OR (col1=v1b AND col2=v2b)`.
- `partition_type in {identity, none}` (or unrecognized): return `None` —
  same fallback convention as `build_hot_partition_filter`, never guess a
  literal format for a type that isn't understood.
- Every literal value formatted through the shared quoting helper — never
  string-interpolate a raw event value directly into SQL.

### 2. Glue-verified partition values (predicate safety)
Before building the filter, re-verify the event's claimed partition
key/values against a real `GetPartition`/`GetPartitions` call — the exact
same discipline as 8a's `GetJobRun` re-verification. An event whose values
don't resolve to a real, current partition is dropped (structured log,
`glue_event.partition_verification_failed` metric), never blindly trusted.

### 3. `compaction.py` — event-derived filter takes priority
Add an optional `event_partition_filter: str | None` parameter to the
compaction entry point. When present, it is used **instead of** (not
merged with) the `build_hot_partition_filter()` heuristic call for that
dispatch. When absent (the existing hourly/window-based path, or Control-M
triggers), behavior is byte-for-byte unchanged — the heuristic remains the
only filter source there.

### 4. Idempotency key extension — BLOCKING PREREQUISITE
Do not change `build_execution_id`'s hash composition
(`table_fqn|operation|window_id` is locked, hardened machinery per
CLAUDE.md — do not touch the formula). Instead, for event-triggered
partition-targeted dispatches ONLY, construct a distinct `window_id`
string before calling `build_execution_id`, e.g.:
```
window_id = f"{get_window_id()}:evt:{sha1(','.join(sorted(flattened_partition_values))).hexdigest()[:10]}"
```
Properties this must satisfy (write these as literal test cases):
- Two different partition-value batches for the same table on the same
  day → two different `window_id`s → two different `execution_id`s → both
  runs actually execute (the bug this fixes).
- The SAME batch re-delivered (duplicate EventBridge event, or 8b's
  consumer redelivering after a crash before ack) → the SAME `window_id`
  → the SAME `execution_id` → naturally deduped by the existing
  `check_already_executed()` path, no new dedupe logic needed.
- The regular scheduled hourly/Control-M `hk_run` for the same table on
  the same day keeps its plain `window_id` (no `:evt:` suffix) — it must
  never collide with, or be shadowed by, an event-triggered run's ID, and
  vice versa. Both should be free to run (subject to the same lock).
- This is purely additive string convention on the caller side — zero
  changes to `idempotency.py` itself.

### 5. Batch coalescing (reuses 8b's debounce window)
Within one debounce window for one table, union the partition-value sets
from every `CreatePartition`/`UpdatePartition`/`BatchCreatePartition`
event received, then dispatch exactly once with the unioned predicate —
never one compaction call per partition event.

### 6. Bounded fallback to full-table
If any of the following, skip event-derived targeting for that dispatch
and instead just mark the table due for its next full-table pass (do not
build an unbounded or needlessly-huge `WHERE ... IN (...)`):
- `partition_type` resolves to `identity`/`none`/unrecognized.
- `GetPartition` verification fails for any partition in the batch.
- The coalesced batch exceeds a new `MAX_EVENT_PARTITIONS_PER_RUN`
  setting (`config/settings.py`, propose default 50) — past that point an
  `IN (...)` predicate isn't meaningfully cheaper than a full scan.

### 7. Lock granularity — unchanged, on purpose
`LockService.acquire()` stays keyed by `table_fqn` only. Two
partition-targeted dispatches for the same table still fully serialize
through the same lock (no partition-level lock). This is a deliberate
tradeoff, not a gap: this whole safety layer exists because of a prior
corruption incident from under-serialized concurrent operations on one
table's metadata pointer — do not weaken it to gain parallelism.

### 8. Everything else routes through existing machinery unchanged
Gate 0-3, dry-run, circuit breaker, `LockHeartbeat`, integrity
verification (`capture_state`/`verify_advanced` — Iceberg commits are
still table-level snapshots regardless of the rewrite's predicate scope,
so verification logic needs zero changes) all apply identically to an
event-triggered targeted run. No new bypass path.

### 9. Execution log / audit visibility
Add a way to see, per run, whether it was full-table or event-partition-
targeted (e.g. a `compaction_scope` value, or reuse an existing free-text
notes/skip_reason-style field if one fits without overloading its
meaning — decide during implementation, document the choice). If a new
`execution_log` column is added, follow the two-writer-path rule in
`.claude/context_hints.md` exactly: both `execution_log.py`'s positional
INSERT and `execution_log_parquet.py::_entry_to_dict()`, plus both the
local SQLite DDL and the Athena `ALTER TABLE`.

### 10. Metrics (via `engine/monitoring/metrics.py`)
`PartitionTargetedCompactions`, `EventDerivedPartitionsCompacted`,
`FallbackToFullTableCount` (broken out by fallback reason — identity-type/
verification-failed/over-limit), `AvgPartitionsPerTargetedRun`.

## Testing
- `build_event_partition_filter`: every `partition_type` (date/timestamp/
  int_yyyymmdd/string/identity/none/unrecognized), single- and
  multi-column composite keys, empty value list, literal-injection safety
  (a partition value containing a quote/SQL metacharacter must not break
  out of the literal).
- Idempotency: two different same-day partition batches for one table
  produce two distinct `execution_id`s and both actually run; the same
  batch redelivered produces the same `execution_id` and is correctly
  deduped; an event-triggered run and the same day's regular scheduled
  `hk_run` for the same table don't collide.
- `compaction.py`: event-derived filter wins over the heuristic when both
  are available; heuristic-only behavior is byte-identical when no event
  filter is passed; falls back to full-table-due-flag (not an oversized
  `IN`) for identity-type/verification-failure/over-`MAX_EVENT_
  PARTITIONS_PER_RUN`.
- Coalescing: N partition events within one debounce window for one table
  produce exactly one compaction dispatch with the unioned predicate.
- End-to-end: assert the actual SQL/params sent to the compaction
  strategy contain the expected `WHERE ... IN (...)` clause and NOT the
  full-table (no-`WHERE`) form, for a realistic multi-partition event
  batch.
- Regression: every existing Gate 0-3/lock/dry-run/circuit-breaker test
  pattern for compaction still passes unmodified for an event-triggered
  targeted run (reuse the orchestrator's existing test fixtures, don't
  build parallel ones).

## Validation
```
python -m pytest tests/unit/ -v
python -m pytest tests/api/ -v
ruff check .
```
No new CFN resources expected (reuses 8b's queue/rule) — re-run
`cfn-lint deploy/zamboni-cfn.yaml` only if 8b's CFN was touched to support
this phase's settings.

## Documentation
Add a section to `docs/deployment/data_operations_guide.md`: how
targeted-vs-full-table compaction is chosen, what
`MAX_EVENT_PARTITIONS_PER_RUN` controls and its default, how to read the
new execution-log scope field, and the explicit statement that VACUUM
remains table-level regardless of this feature.

## Scope exclusions
No changes to `vacuum.py`, snapshot-expiry/orphan-cleanup logic, lock
granularity, Gate 0-3 ordering, or the `build_execution_id` hash formula
itself. No auto-registration or destructive action — unchanged from 8b.

## Acceptance criteria
- `python -m pytest tests/unit/ -v` and `tests/api/ -v` → zero failures,
  including the idempotency-collision regression tests in task 4.
- A realistic multi-partition batch event produces exactly one compaction
  dispatch scoped to those partitions, verified end-to-end in a test.
- No behavior change to the existing heuristic-driven or full-table path
  when no event filter is present.
- Append a dated Migration Progress entry to `.claude/CLAUDE.md`.

Suggested commit: `feat(engine): event-derived partition-targeted compaction (Phase 8c)`
