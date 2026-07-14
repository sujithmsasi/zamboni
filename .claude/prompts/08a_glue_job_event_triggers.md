# PHASE 8a — Opt-In Glue Job State Change Triggers (event-assisted HK)

Context: today HKEngine is triggered only by the hourly ZamboniHkScheduleRule
(EventBridge → SSM Run Command, added in the 2026-07-09 EventBridge engine
scheduling session) or by Control-M. That reconciliation loop is correct but
coarse — a table's HK run can lag its real upstream Glue job completion by up
to an hour. This phase adds a second, best-effort, opt-in path: when a
registered upstream Glue job reports SUCCEEDED, dispatch HKEngine for the
specific mapped table(s) shortly afterward, without weakening or replacing the
hourly safety net.

This is an event-assisted enhancement, not a replacement path. AWS Glue
service events are best-effort and may be missing, delayed, duplicated, or
out of order — the design must stay correct under all of those conditions.

## Read first
`.claude/CLAUDE.md` (Engine Architecture Facts — Idempotency, Backpressure,
Control-M Integration sections; Migration Progress entries for "Lifecycle
Engine domain-gating", "structured hardening plan", "lock heartbeat"),
`.claude/context_hints.md`, `.claude/decisions.md`, `.claude/contracts.md`,
`README.md`, `deploy/zamboni-cfn.yaml` (the existing `ZamboniHkScheduleRule`
+ EventBridge SSM-target pattern, the 2026-07-11 dedicated control-plane EBS
volume, the 2026-07-12 log-shipping/logrotate setup),
`engine/scripts/run_hk.py`, `engine/engines/hk_engine.py`,
`engine/core/registry.py`, `engine/core/idempotency.py`,
`engine/utils/glue_client.py`, `engine/monitoring/metrics.py`,
`docs/deployment/data_operations_guide.md`.

Also consult (do not guess AWS field names or semantics from memory):
- https://docs.aws.amazon.com/glue/latest/dg/automating-awsglue-with-cloudwatch-events.html
- https://docs.aws.amazon.com/eventbridge/latest/ref/events-ref-glue.html
- https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-rule-retry-policy.html
- https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-monitoring-events-best-practices.html

## Known current state (verified facts to cite, not re-derive)
- `stream_registry` already carries `controlm_pipeline_job`,
  `controlm_hk_job`, `dependent_on_controlm_job`, `dependent_job_type`
  (default `'glue'`) — see CLAUDE.md's "Control-M Integration (complete)"
  section. `hk_engine.py`'s Gate 1 already reads these for the Control-M
  dependency check. This phase's canonical mapping (task 8 below) must be
  audited against this existing field set before introducing a new one.
- `idempotency.py`'s `execution_id = sha1(table_fqn|operation|window_id)`
  deliberately excludes `run_id` so two different triggers (EventBridge
  hourly safety-net + Control-M) dedupe to the same execution. A Glue-event
  trigger is a THIRD trigger source and must dedupe through this same
  mechanism, not bypass it.
- `engine/core/lock_service.py::LockHeartbeat`, Gate 0/1/2/3, the circuit
  breaker, dry-run defaults, and run-frequency checks are all real,
  already-hardened machinery (2026-07-10/11 sessions) — this phase must
  route through them, never around them.
- The 5 control-plane tables (`stream_registry`, `hk_config`,
  `domain_registry`, `nonprod_registry`, `controlm_jobs`) are SQLite-primary
  via `engine/core/control_plane.py` (2026-07-09 migration). Any new
  control-plane table this phase needs goes through
  `config/control_plane_schema.py` + `scripts/init_control_plane_db.py` +
  `scripts/seed_local_db.py`, the same way, not a new ad-hoc mechanism.
- `deploy/zamboni-cfn.yaml`'s EC2 UserData is fragile to edit (2026-07-12
  session: any UserData byte-change forces "Some interruptions" on the
  running instance, and cloud-init never reruns it on an existing instance
  anyway). New setup logic for this phase belongs in
  `deploy/scripts/configure_*.sh` (run by `after_install.sh`, which fires
  on every deploy), not inline in UserData — same lesson already applied to
  log shipping.

## Required architecture

1. Preserve the existing hourly `ZamboniHkScheduleRule` unchanged as the
   authoritative reconciliation/safety-net path. Nothing about it changes,
   including its `EnableEngineScheduling` gate.

2. Add a CloudFormation parameter `EnableGlueEventTriggers`
   (`AllowedValues: [ENABLED, DISABLED]`, default `DISABLED`), independent of
   `EnableEngineScheduling`. Existing deployments must see zero behavior
   change by default.

3. Add an EventBridge rule matching ONLY `source: aws.glue`,
   `detail-type: "Glue Job State Change"`, terminal states `SUCCEEDED`,
   `FAILED`, `TIMEOUT`, `STOPPED`. Do not subscribe to every `aws.glue` event.

4. Buffered design, EventBridge rule → SQS processing queue → a Zamboni
   Glue-event consumer daemon on the EC2 instance → existing HKEngine for
   mapped tables. **No SNS relay** — Glue publishes these events onto the
   account's default EventBridge event bus automatically (no explicit
   subscribe step on the Glue side), and the rule's target is the SQS
   queue directly; SQS is a native EventBridge target type, so SNS is only
   relevant if a future need arises for fan-out to multiple independent
   subscribers, which this single-consumer design doesn't have. Add
   retry/redrive handling and a DLQ (EventBridge target retry policy +
   delivery DLQ if delivery and processing failures need separate
   handling — justify the split or the single-DLQ choice in the doc). Do
   not introduce Lambda merely as glue code unless a concrete, documented
   limitation makes the EC2/SQS approach unsuitable.

5. New, independently testable modules — parsing must not live inside
   HKEngine:
   - `engine/events/glue_event.py` — parse/validate.
   - `engine/events/glue_event_consumer.py` — SQS poll loop, mapping,
     dispatch, idempotency, notification.
   - `engine/scripts/run_glue_event_consumer.py` — the long-running
     entrypoint (same shape as `run_hk.py`'s siblings).

6. Parse and validate at minimum: top-level EventBridge event ID, source,
   detail-type, event timestamp, AWS account and region, Glue job name,
   Glue job run ID, terminal state, failure message when present. Confirm
   actual AWS field names from the docs above or a checked-in sanitized
   sample event (task 20) — do not silently guess alternate field names.

7. Never interpolate event-controlled strings into shell commands or SQL.
   Validate Glue job names; use existing control-plane access patterns
   (`engine.core.control_plane`). Invoke subprocess commands with argument
   arrays, never shell-built strings.

## Table mapping and execution

8. Map a Glue job event only to registered, active, HK-enabled tables whose
   canonical upstream dependency is `dependent_on_controlm_job = <event job
   name>` AND `dependent_job_type = 'glue'`. Audit `controlm_pipeline_job`
   and any other legacy field before changing behavior; document ONE
   canonical mapping in this phase rather than expanding legacy ambiguity,
   while keeping existing UI/API behavior backward compatible.

9. Treat `hk_config.gate1_enabled` as the table-level opt-in to
   event-triggered upstream-dependency processing. Do not trigger tables
   that aren't opted in.

10. For SUCCEEDED events:
    - Verify the exact Glue job run with `GetJobRun` before dispatch.
    - Handle delayed/out-of-order events — a newer RUNNING/STARTING run must
      never be bypassed by a stale SUCCEEDED event for an older run.
    - Dispatch the existing HKEngine one mapped table at a time. Never use
      `--force`.
    - Preserve safe-window, lock, circuit-breaker, domain-active, dry-run,
      run-frequency, and existing operation-idempotency behavior exactly —
      the event is a trigger hint, never permission to bypass any gate.
    - Respect `DRY_RUN_DEFAULT`.

11. For FAILED/TIMEOUT/STOPPED: do not run housekeeping. Emit structured
    logs + CloudWatch metrics. Reuse the existing SNS/notifier path for one
    actionable failure summary (job name, run ID, state, event time, mapped
    domains/tables). Deduplicate — a re-delivered EventBridge event for the
    same failure must not re-notify.

12. If no registered table maps to the job: not a processing failure. Log
    `glue_event.unmapped`, emit an `UnmappedGlueEvents` metric, acknowledge
    (delete) the message.

## Idempotency and queue correctness

13. EventBridge/SQS are at-least-once. Add durable event-receipt idempotency
    keyed by the EventBridge event ID — same event ID handled once, a
    duplicate becomes a no-op, partial multi-table processing resumes
    safely, failed processing stays retryable, receipt state is never marked
    complete before all intended work is terminal. Do NOT add the event ID
    into the existing table-operation idempotency hash in
    `engine/core/idempotency.py` — that hash intentionally dedupes across
    trigger sources and must stay untouched. If a new control-plane table is
    needed for event-receipt state, add it through
    `config/control_plane_schema.py` + `init_control_plane_db.py` +
    `seed_local_db.py`, matching how the 5 existing control-plane tables
    are wired.

14. Use SQS long polling. Extend message visibility so a legitimate,
    in-progress HK operation can't become visible to a second consumer
    while still running. Delete a message only after successful or
    intentionally-terminal handling.

15. Poison/malformed messages retry only to a bounded limit, then land in
    the processing DLQ with enough structured context for diagnosis.

## Infrastructure and security

16. Least-privilege CFN resources: the EventBridge rule, primary SQS queue,
    DLQ(s), redrive policy, a queue policy allowing only this specific
    EventBridge rule to SendMessage, EC2 role permissions limited to
    receive/delete/change-visibility on the primary queue, any CloudWatch
    metric/alarm permissions genuinely required.

17. Install the consumer via the existing CodeDeploy/systemd conventions:
    restart after failure, stop cleanly during deployment, source
    `/opt/zamboni/.env`, run from `/opt/zamboni/.venv`, write structured
    logs under `/var/log/zamboni` (covered by the existing CloudWatch
    Agent + logrotate config from the 2026-07-12 session), and stay
    disabled when `EnableGlueEventTriggers` is `DISABLED`.

18. CloudWatch metrics + alarms via `engine/monitoring/metrics.py`:
    `GlueEventsReceived`, `GlueEventsProcessed`, `DuplicateGlueEvents`,
    `UnmappedGlueEvents`, `GlueEventProcessingFailures`, `GlueEventAgeSeconds`,
    EventBridge `FailedInvocations`, processing-DLQ visible-message count.

## Scope exclusions
19. Do not automatically register, delete, or mutate tables in response to
    Glue Data Catalog Database/Table events — that's Phase 8b's discovery
    scope, and even there it stays non-destructive. Do not reduce or remove
    the hourly HK schedule. Do not change dry-run defaults, safety gates,
    lifecycle deletion semantics, or the deterministic table-operation
    idempotency key. Do not modify `migrate_repo.ps1`, milestone history, or
    unrelated files.

## Testing
Add focused unit tests covering: valid SUCCEEDED parsing; FAILED/TIMEOUT/
STOPPED handling; malformed/incomplete event; unexpected source/detail-type/
state; duplicate event ID; out-of-order event; exact-run verification
failure; unmapped job; inactive domain; HK-disabled table; gate1-disabled
table; multiple tables mapped to one job; partial multi-table failure +
safe retry; SQS deletion only after terminal processing; visibility
extension during long processing; poison-message/DLQ behavior; dry-run
behavior; no safety-gate bypass; failure-notification deduplication; CFN
parameter defaulting to DISABLED; least-privilege queue policies/IAM.

## Validation
```
python -m pytest tests/unit/ -v
python -m pytest tests/api/ -v
cfn-lint deploy/zamboni-cfn.yaml
bash -n <every touched shell script>
git diff --check
```

## Documentation
Update `README.md` and `docs/deployment/data_operations_guide.md` with: the
hybrid event-driven + hourly-reconciliation architecture; enablement and
rollback instructions; that Glue event delivery is best-effort; queue/DLQ
inspection and replay procedures; metrics and alarms; existing-instance CFN
upgrade steps; a sanitized sample Glue Job State Change event; a manual
end-to-end validation procedure.

## Delivery format
Before editing: current-state summary, proposed event flow, files expected
to change, risks and rollback strategy. After implementation: a table of
requirement → implementation → files changed → tests proving it →
remaining operational validation. Do not claim live-AWS validation unless
actually performed. Preserve all unrelated working-tree changes.

## Acceptance criteria
- `python -m pytest tests/unit/ -v` and `tests/api/ -v` → zero failures.
- `cfn-lint deploy/zamboni-cfn.yaml` → zero errors/warnings.
- `bash -n` clean on every touched shell script; `git diff --check` clean.
- `EnableGlueEventTriggers` defaults to `DISABLED`; a stack with it unset
  behaves identically to today.
- No changes to `vacuum.py`, orchestrator sequencing, Gate 0-3 logic, the
  deterministic table-operation idempotency key, or the hourly HK rule.
- Append a dated Migration Progress entry to `.claude/CLAUDE.md`.

Suggested commit: `feat(engine): opt-in Glue Job State Change triggers for targeted HK dispatch`
