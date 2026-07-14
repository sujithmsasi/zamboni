# PHASE 8b — Opt-In Glue Data Catalog Event Triggers (targeted discovery + HK)

Context: Phase 8a reacts to a specific Glue *job* finishing. This phase adds
a second, independent opt-in signal — Glue Data *Catalog* events (new
database/table, updated table, new/updated partitions) — so Zamboni can
narrow full-fleet HK scans and surface newly-created tables faster, without
ever treating a best-effort, unordered catalog event as authoritative for
registration or deletion decisions.

Run this phase after, or independently in parallel with, 8a — they share the
SQS/consumer pattern but subscribe to different event shapes and must not be
merged into one rule or one queue.

## Read first
Same base list as `08a_glue_job_event_triggers.md`'s "Read first" section,
plus that phase's own output once it exists (module layout, DLQ/queue
naming conventions, metrics helper usage) so this phase reuses the same
conventions rather than inventing parallel ones.

Also consult:
https://docs.aws.amazon.com/glue/latest/dg/automating-awsglue-with-cloudwatch-events.html
— Glue Catalog events are explicitly best-effort, unordered, and a single
`BatchCreatePartition`/`BatchUpdatePartition` event can carry up to 100
changed tables/partitions, which is why buffering + coalescing matters here
more than in 8a's single-job-per-event shape.

## Required behavior

Capture these `aws.glue` events:
1. `Glue Data Catalog Database State Change` — `CreateDatabase`
2. `Glue Data Catalog Table State Change` — `CreateTable`, `UpdateTable`,
   `CreatePartition`, `BatchCreatePartition`, `UpdatePartition`,
   `BatchUpdatePartition`

Route through SQS so bursts can be buffered, deduplicated by EventBridge
event ID (reuse 8a's event-receipt idempotency mechanism/table rather than
building a second one), and coalesced by database/table for a configurable
debounce window before acting.

Behavior:
- New database → schedule safe discovery only; never auto-register it.
- New table → discover and report whether it is registered and HK-eligible
  (log + metric; surface via the existing Table Registration flow, not a
  new auto-register path).
- Table/partition change → trigger a *targeted* HK evaluation for the
  corresponding already-registered table only.
- Preserve every existing HK safety gate, lock, dry-run setting,
  run-frequency check, and idempotency behavior. Never use `--force`.
- Ignore unregistered, inactive, non-Iceberg, or HK-disabled tables, with
  structured logs explaining why (mirrors 8a's `glue_event.unmapped`
  convention).
- Keep the existing hourly HK EventBridge rule as the reconciliation
  fallback — catalog events are best-effort, unordered, and may be missing
  or duplicated, so this path narrows scans, it never replaces the safety
  net.
- Never perform a destructive lifecycle action (drop, delete, archive)
  directly from a catalog event. Lifecycle Engine's own gates
  (domain-active allowlist, PENDING_DROP re-check, etc.) are untouched and
  remain the only path to deletion.

## Infrastructure

- New CFN parameter `EnableGlueCatalogEvents`, default `DISABLED`,
  independent of `EnableGlueEventTriggers` (8a) and `EnableEngineScheduling`.
  A deployment can run 8a without 8b, or vice versa, or neither.
- Least-privilege EventBridge rule (matching only the 6 detail-types above,
  not every `aws.glue` catalog event), its own SQS queue + DLQ + queue
  policy scoped to this rule, EC2 IAM limited to receive/delete/change-
  visibility on this queue.
- A testable event parser and consumer, structured the same way as 8a's
  `engine/events/` modules (e.g. `engine/events/glue_catalog_event.py`,
  `engine/events/glue_catalog_event_consumer.py`,
  `engine/scripts/run_glue_catalog_event_consumer.py`) — do not merge
  parsing logic with 8a's job-event parser; the shapes and downstream
  actions are different enough to warrant separate, focused modules.
- Metrics via `engine/monitoring/metrics.py`: received, deduplicated,
  coalesced, unmapped, processed, failed, and DLQ counts — named
  consistently with 8a's metric family (e.g. `GlueCatalogEventsReceived`)
  so a dashboard can group them.

## Testing
Cover: single-partition and batch-partition (`BatchCreatePartition`/
`BatchUpdatePartition`, up to and above the 100-item AWS limit) events,
duplicate and out-of-order events, malformed events, unregistered tables
(ignored, not errored), and confirmed targeted HK dispatch for a matching
registered table (verifying it goes through the same gates as any other
HK trigger, not a shortcut path).

## Validation
```
python -m pytest tests/unit/ -v
python -m pytest tests/api/ -v
cfn-lint deploy/zamboni-cfn.yaml
bash -n <every touched shell script>
git diff --check
```

## Documentation
Cover enablement, rollback, DLQ replay, and the hourly-reconciliation
fallback in `README.md` and `docs/deployment/data_operations_guide.md`,
cross-linked with 8a's documentation rather than duplicated.

## Scope exclusions
Do not modify `migrate_repo.ps1`, milestone history, or unrelated files. Do
not weaken any existing safety gate, dry-run default, or the lifecycle
deletion state machine. Do not change the hourly HK rule.

## Acceptance criteria
- `python -m pytest tests/unit/ -v` and `tests/api/ -v` → zero failures.
- `cfn-lint deploy/zamboni-cfn.yaml` → zero errors/warnings.
- `EnableGlueCatalogEvents` defaults to `DISABLED`; unset behaves
  identically to today.
- No auto-registration, no auto-deletion, from any catalog event.
- Append a dated Migration Progress entry to `.claude/CLAUDE.md`.

Suggested commit: `feat(engine): opt-in Glue Data Catalog event triggers for targeted discovery/HK`
