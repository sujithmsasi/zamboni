# Runbook: Maintenance Lock Operations

**Owner:** Zamboni engine team · **Applies to:** Workstream A (Engine Hardening) · **Last updated:** 2026-07-06

`engine/core/lock_service.py::LockService` gives each table exactly one
maintenance authority at a time (contracts.md §3.1) — the orchestrator
(`engine/core/orchestrator.py`) acquires the lock before Gate 1 and releases
it in a `finally` block regardless of outcome. Backend is selected by
`config.settings.get_mode()`:

| Mode | Backend | Table |
|---|---|---|
| `local` | SQLite | `maintenance_locks` (via `engine/utils/local_db.py`) |
| `aws_local` / `aws_ec2` | DynamoDB | `config.settings.DDB_LOCK_TABLE` (default `zamboni_maintenance_locks`) |

Item shape (both backends): `table_fqn` (key) · `lock_owner`
(`host:pid:uuid8`) · `operation` · `acquired_at` · `heartbeat_at` ·
`expires_at`. DynamoDB TTL is enabled on `expires_at`
(`scripts/create_lock_table.py`), so an abandoned lock disappears on its own
— force-release below is for when you can't wait for that.

## Viewing active locks

**DynamoDB console** — Tables → `zamboni_maintenance_locks` → Explore table
items. Sort/filter by `table_fqn`. `expires_at` is epoch seconds; anything
in the past is a lock TTL is about to reap (or already has, subject to
DynamoDB's TTL delete lag of up to 48h) — it is not actually blocking new
acquisitions once expired, since `acquire()`'s ConditionExpression allows
stealing an expired lock (`attribute_not_exists(table_fqn) OR expires_at <
:now`).

**CLI (aws_local / aws_ec2):**

```bash
aws dynamodb scan --table-name zamboni_maintenance_locks
```

**Local mode (SQLite):**

```bash
sqlite3 zamboni_local.db "SELECT * FROM maintenance_locks;"
```

**Upcoming:** `GET /api/locks` (contracts.md §6, `routers/system.py`) will
return this as a paged JSON list once the FastAPI layer lands in Phase 2 —
not yet implemented.

## When a lock is legitimately held

A table showing `SKIP_LOCK_HELD` in `execution_log.skip_reason` with a
recent `heartbeat_at` almost always means another run is genuinely in
progress (EventBridge and Control-M triggers for the same table+window
dedupe via idempotency, but overlapping *different* windows can still both
attempt to acquire). **Do nothing** — the lock has a TTL
(`LOCK_TTL_MINUTES`, default 120) and will release on its own when the
holding run finishes or times out.

## Force-releasing a lock

Only force-release when **all** of the following are true:

1. `heartbeat_at` is stale — older than `LOCK_TTL_MINUTES` +
   `LOCK_HEARTBEAT_SECONDS` with no update, which usually means the holding
   process crashed rather than finished cleanly.
2. You have confirmed (via `execution_log`, CloudWatch, or the process host)
   that no orchestrator run for this `table_fqn` is actually still
   executing. Force-releasing a lock held by a *live* run reintroduces
   exactly the race condition Workstream A exists to prevent — a second
   maintenance operation could start while the first is mid-VACUUM.
3. You have a business reason to unblock the table now rather than wait out
   the TTL (e.g. blocking a time-sensitive downstream job).

**DynamoDB:**

```bash
aws dynamodb delete-item \
  --table-name zamboni_maintenance_locks \
  --key '{"table_fqn": {"S": "glue_catalog.<db>.<table>"}}'
```

**Local mode (SQLite):**

```bash
sqlite3 zamboni_local.db \
  "DELETE FROM maintenance_locks WHERE table_fqn = 'glue_catalog.<db>.<table>';"
```

**Upcoming:** `DELETE /api/locks/{fqn}` (contracts.md §6) will be the
audited, UI-driven equivalent once Phase 2 ships — it will write an
`audit_log` entry the way every other mutating endpoint does. Until then,
force-release via the console/CLI is not audited by Zamboni itself; note the
action (who, when, why) somewhere durable (ticket, Slack, runbook log) since
`audit_log` won't capture it.

## After a force-release

Confirm the table picks up its next scheduled run normally — check
`execution_log` for a fresh row with `status = 'RUNNING'` at the next
trigger, and that it reaches `SUCCESS`/`DRY_RUN` rather than immediately
hitting `SKIP_LOCK_HELD` again (which would indicate something is still
re-acquiring the lock, e.g. a retry loop on the crashed process).
