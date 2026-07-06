# Zamboni — Context Hints / Do-NOT List

First-written 2026-07-05 (Phase 0 audit; no prior version existed in git
history — treat everything below as newly established, not "restored").

## Do NOT
- Do not read `os.environ` directly outside `config/settings.py`. Every
  module imports constants from `config.settings` — this is enforced by
  convention, not by a linter rule, so it's easy to violate silently.
- Do not replace `engine/utils/local_db.py::_translate_date_diff` (line 150)
  with a regex. It hand-scans character-by-character specifically to handle
  nested parentheses in `DATE_DIFF('unit', a, b)` arguments that a single
  regex can't reliably capture. This was a fix for a real bug — regressing
  it silently breaks local-mode date arithmetic in ways that only show up
  with nested expressions.
- Do not assume Iceberg VACUUM in this codebase supports an `older_than`
  argument or a separate orphan-only delete call. Athena engine v3's bare
  `VACUUM db.table;` does snapshot expiry AND orphan removal together, with
  retention controlled only via TBLPROPERTIES set ahead of time. Any new
  orchestration logic that assumes a controllable per-call orphan age needs
  to either (a) tighten TBLPROPERTIES immediately before the VACUUM call, or
  (b) get an explicit design decision from Sujith — don't invent a
  parameter the underlying VACUUM call doesn't accept.
- Do not add `run_id` into the idempotency hash in
  `engine/core/idempotency.py::build_execution_id`. It's excluded on purpose
  so that EventBridge and Control-M triggers for the same table+window
  dedupe onto the same execution_id.
- Do not assume `pd.NA` is falsy in boolean contexts — `pd.NA` raises on
  direct bool coercion. Existing fixes exist for this in `hk_enabled` and
  `is_backup_pattern` lambdas (see commit `2987879`) — check for
  `pd.isna()` handling before adding new boolean-derived columns from
  nullable pandas dtypes.
- Do not assume `get_mode()`, `get_boto3_session()`, or a `ZAMBONI_MODE` env
  var already exist — they don't (verified via repo-wide grep, Phase 0).
  Only `ZAMBONI_LOCAL_MODE` (bool) exists today. Building these is explicit
  Phase 1a scope, not a refactor of something already there.
- Do not assume a lock service, DynamoDB table, or Gate 0 already exists —
  none do (verified via repo-wide grep). `hk_engine.py` only wires
  gate1/gate2/gate3; there is no `gate0_enabled` concept anywhere yet.
- Do not assume `deploy/zamboni-cfn.yaml` exists — it doesn't. `deploy/`
  has CodeBuild/CodeDeploy pieces (buildspec, appspec, install/start
  scripts) and an IAM policy JSON, but no CloudFormation template at all.
- Do not treat `ZAMBONI_LOCAL_MODE` as a test-only mock — it's a real
  SQLite-backed dev/demo path used for UI development and validation
  without AWS connectivity; treat it as a legitimate backend, not a stub.
- Do not schema-break `execution_log` writes by adding a column to only one
  writer path. `execution_log.py::write()` (positional SQL INSERT) and
  `execution_log_parquet.py::ParquetLogBuffer._entry_to_dict()` (dict
  serialization) must both be updated together or Parquet-mode rows will
  silently miss new columns.
- Do not call `circuit_breaker.trip()` with just a table_fqn — it requires
  `failure_count` (and takes `dry_run`). Contracts.md §5 shorthand
  `circuit_breaker.trip(fqn)` is describing the concept, not the literal
  call signature.

## Known-good patterns to keep replicating
- Backward-compatible column probing: catch the exception, check the
  message for `"column"` + the column name, no-op rather than fail. Used in
  `idempotency.py` and `property_sync.py` for `last_execution_id` /
  `properties_synced` — lets code run against environments mid-migration.
- Fail-open on non-critical checks: `backpressure.py` returns `True`
  (allow dispatch) if the Athena query-count check itself errors, rather
  than blocking all work on an observability failure.
- Every `hk_config` flag read as `hk_config.get("gateN_enabled", default)`
  with an explicit default matching current safe behavior (gate1 default
  0/off since Control-M API integration wasn't ready yet when it landed;
  gate2/gate3 default 1/on).
