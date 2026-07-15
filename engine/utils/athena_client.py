"""
Zamboni -- Athena Client
Thin wrapper around boto3 Athena -- workgroup-aware, polls to completion.
All Athena calls in the project go through here.

Hardening additions (Sprint 7):
  - Query timeout with automatic cancel on expiry
  - Structured timeout/cancel exceptions for caller handling
  - Timeout metrics emitted to CloudWatch
  - Cancel-on-shutdown support via cancel_query()
"""
from __future__ import annotations

import time

import boto3

from config.settings import (  # noqa: F401
    ATHENA_CATALOG,
    ATHENA_DATABASE,
    ATHENA_QUERY_TIMEOUT_SECONDS,
    ATHENA_RESULTS_BUCKET,
    ATHENA_WORKGROUPS,
    AWS_REGION,
    ZAMBONI_LOCAL_DB,
    ZAMBONI_LOCAL_MODE,
    get_boto3_session,
)
from engine.utils.logger import get_logger

log = get_logger(__name__)

_client: boto3.client | None = None


# ── Custom exceptions ─────────────────────────────────────────────────────────

class AthenaQueryTimeout(RuntimeError):
    """Raised when a query exceeds ATHENA_QUERY_TIMEOUT_SECONDS."""
    def __init__(self, query_id: str, workgroup: str, elapsed_s: float):
        self.query_id  = query_id
        self.workgroup = workgroup
        self.elapsed_s = elapsed_s
        super().__init__(
            f"Athena query {query_id} timed out after {elapsed_s:.0f}s "
            f"(workgroup={workgroup}, limit={ATHENA_QUERY_TIMEOUT_SECONDS}s)"
        )


class AthenaQueryFailed(RuntimeError):
    """Raised when Athena returns FAILED or CANCELLED state."""
    def __init__(self, query_id: str, state: str, reason: str):
        self.query_id = query_id
        self.state    = state
        self.reason   = reason
        super().__init__(f"Athena query {query_id} {state}: {reason}")


class AthenaQueryCancelledLeaseLost(RuntimeError):
    """
    Raised when a query is actively cancelled mid-poll because the
    caller's maintenance lock lease was lost (2026-07-11 audit fix,
    engine.core.lock_service.LockHeartbeat). Distinct from
    AthenaQueryTimeout/AthenaQueryFailed so callers can tell "we gave up
    waiting" apart from "ownership was stolen out from under us."
    """
    def __init__(self, query_id: str):
        self.query_id = query_id
        super().__init__(f"Athena query {query_id} cancelled -- maintenance lock lease was lost")


def _get_client() -> boto3.client:
    global _client
    if _client is None:
        _client = get_boto3_session().client("athena", region_name=AWS_REGION)
    return _client


# ── run_query ─────────────────────────────────────────────────────────────────

def run_query(
    sql:      str,
    workgroup: str = "standard",
    database:  str | None = None,
    dry_run:   bool = False,
    timeout_s: int | None = None,
    cancel_check=None,
) -> str | None:
    """
    Execute an Athena DML/DDL query and wait for completion.

    Args:
        sql:        SQL to run.
        workgroup:  Tier key (critical|standard|low|archival|app)
                    OR a literal workgroup name if not in the map.
        database:   Athena database context. Defaults to ATHENA_DATABASE.
        dry_run:    Log SQL only -- do not execute.
        timeout_s:  Per-call timeout override in seconds.
                    Defaults to ATHENA_QUERY_TIMEOUT_SECONDS setting.
        cancel_check: Optional zero-arg callable returning True once the
                    caller's maintenance lock lease is lost
                    (engine.core.lock_service.LockHeartbeat.lost, passed
                    as `lambda: heartbeat.lost`) -- 2026-07-11 audit fix.
                    Checked every poll interval; when it returns True the
                    query is actively cancelled and
                    AthenaQueryCancelledLeaseLost is raised instead of
                    letting the wait (and the query it's waiting on)
                    complete unprotected after ownership may have moved to
                    another process.

    Returns:
        QueryExecutionId on success, None on dry_run.

    Raises:
        AthenaQueryTimeout             if the query exceeds the timeout.
        AthenaQueryFailed              if Athena returns FAILED or CANCELLED.
        AthenaQueryCancelledLeaseLost  if cancel_check() returns True mid-poll.
    """
    wg  = ATHENA_WORKGROUPS.get(workgroup, workgroup)
    db  = database or ATHENA_DATABASE
    tmo = timeout_s if timeout_s is not None else ATHENA_QUERY_TIMEOUT_SECONDS

    # ── Local mode (no AWS) ──────────────────────────────────────────────────
    if ZAMBONI_LOCAL_MODE:
        from engine.utils.local_db import run_query_local
        return run_query_local(sql)

    if dry_run:
        log.info("athena.dry_run", workgroup=wg, database=db, sql=sql[:300])
        return None

    client   = _get_client()
    response = client.start_query_execution(
        QueryString=sql,
        WorkGroup=wg,
        ResultConfiguration={"OutputLocation": ATHENA_RESULTS_BUCKET},
        QueryExecutionContext={"Catalog": ATHENA_CATALOG, "Database": db},
    )
    query_id = response["QueryExecutionId"]
    log.info("athena.submitted", query_id=query_id, workgroup=wg,
             timeout_s=tmo, sql_preview=sql[:200])

    return _poll(client, query_id, wg, timeout_s=tmo, cancel_check=cancel_check)


def cancel_query(query_id: str) -> bool:
    """
    Cancel a running Athena query.
    Returns True if cancel was issued, False if query already terminal.
    """
    try:
        _get_client().stop_query_execution(QueryExecutionId=query_id)
        log.info("athena.cancelled", query_id=query_id)
        return True
    except Exception as e:
        log.warning("athena.cancel_failed", query_id=query_id, error=str(e))
        return False


# ── _poll ─────────────────────────────────────────────────────────────────────

def _poll(
    client,
    query_id:  str,
    workgroup: str,
    interval:  int = 3,
    timeout_s: int | None = None,
    cancel_check=None,
) -> str:
    """
    Poll until terminal state.

    Raises:
        AthenaQueryTimeout             on wall-clock timeout (also cancels the query).
        AthenaQueryFailed              on FAILED / CANCELLED state.
        AthenaQueryCancelledLeaseLost  if cancel_check() returns True mid-poll.
    """
    started_at = time.monotonic()

    while True:
        elapsed = time.monotonic() - started_at

        # Lease-lost check -- takes priority over the timeout check below,
        # since a lost lease means this process may no longer be the sole
        # owner of the table right now, not just that it's been waiting a
        # while.
        if cancel_check is not None and cancel_check():
            log.error("athena.cancelled_lease_lost", query_id=query_id, workgroup=workgroup)
            try:
                client.stop_query_execution(QueryExecutionId=query_id)
                log.info("athena.auto_cancelled_on_lease_lost", query_id=query_id)
            except Exception as ce:
                log.warning("athena.auto_cancel_failed", query_id=query_id, error=str(ce))
            raise AthenaQueryCancelledLeaseLost(query_id)

        # Timeout check
        if timeout_s is not None and elapsed >= timeout_s:
            log.error(
                "athena.timeout",
                query_id=query_id, workgroup=workgroup,
                elapsed_s=round(elapsed, 1), timeout_s=timeout_s,
            )
            # Cancel the query before raising
            try:
                client.stop_query_execution(QueryExecutionId=query_id)
                log.info("athena.auto_cancelled_on_timeout", query_id=query_id)
            except Exception as ce:
                log.warning("athena.auto_cancel_failed", query_id=query_id,
                            error=str(ce))

            # Emit timeout metric
            _emit_timeout_metric(workgroup, elapsed)

            raise AthenaQueryTimeout(query_id, workgroup, elapsed)

        resp  = client.get_query_execution(QueryExecutionId=query_id)
        state = resp["QueryExecution"]["Status"]["State"]

        if state == "SUCCEEDED":
            stats = resp["QueryExecution"].get("Statistics", {})
            scanned = stats.get("DataScannedInBytes", 0)
            exec_ms = stats.get("TotalExecutionTimeInMillis", 0)
            log.info(
                "athena.succeeded",
                query_id=query_id, workgroup=workgroup,
                bytes_scanned=scanned, execution_ms=exec_ms,
                elapsed_s=round(elapsed, 1),
            )
            return query_id

        if state in ("FAILED", "CANCELLED"):
            reason = resp["QueryExecution"]["Status"].get(
                "StateChangeReason", "unknown"
            )
            log.error("athena.failed", query_id=query_id,
                      state=state, reason=reason)
            raise AthenaQueryFailed(query_id, state, reason)

        log.debug("athena.polling", query_id=query_id, state=state,
                  elapsed_s=round(elapsed, 1))
        time.sleep(interval)


def _emit_timeout_metric(workgroup: str, elapsed_s: float) -> None:
    """Emit a CloudWatch metric for query timeouts (best-effort)."""
    try:
        from engine.monitoring.metrics import put_metric
        put_metric(
            name="AthenaQueryTimeout",
            value=1,
            unit="Count",
            dimensions=[{"Name": "Workgroup", "Value": workgroup}],
        )
        put_metric(
            name="AthenaQueryElapsedSeconds",
            value=elapsed_s,
            unit="Seconds",
            dimensions=[{"Name": "Workgroup", "Value": workgroup}],
        )
    except Exception:
        pass  # never block on metric emission


def get_query_stats(query_id: str) -> dict:
    """Return cost-tracking stats for a completed query."""
    resp  = _get_client().get_query_execution(QueryExecutionId=query_id)
    stats = resp["QueryExecution"].get("Statistics", {})
    return {
        "bytes_scanned": stats.get("DataScannedInBytes", 0),
        "execution_ms":  stats.get("TotalExecutionTimeInMillis", 0),
    }


def read_sql(
    sql:       str,
    workgroup: str = "standard",
    database:  str | None = None,
    timeout_s: int | None = None,
    cancel_check=None,
):
    """
    Execute a SELECT and return results as a pandas DataFrame.

    Raises AthenaQueryTimeout if the query exceeds timeout_s (or
    ATHENA_QUERY_TIMEOUT_SECONDS if not given).

    Real bug fixed here (2026-07-09 audit): this used to delegate straight
    to wr.athena.read_sql_query(), which polls internally with no
    caller-side timeout at all -- despite the docstring's claim,
    `timeout_s` was dead code, and a hang here (every Gate 0/health-check/
    capture_state call in orchestrator.py uses read_sql) could block an HK
    worker thread indefinitely. Now submits via the same
    start_query_execution + _poll() path run_query() already uses (which
    genuinely enforces the timeout and auto-cancels on expiry), then fetches
    results for that completed query_id via wr.athena.get_query_results --
    same return shape as before, real timeout enforcement underneath.
    """
    # ── Local mode (no AWS) ──────────────────────────────────────────────────
    if ZAMBONI_LOCAL_MODE:
        from engine.utils.local_db import read_sql_local
        return read_sql_local(sql)

    import awswrangler as wr

    wg  = ATHENA_WORKGROUPS.get(workgroup, workgroup)
    db  = database or ATHENA_DATABASE
    tmo = timeout_s if timeout_s is not None else ATHENA_QUERY_TIMEOUT_SECONDS

    log.info("athena.read_sql", workgroup=wg, database=db, sql=sql[:300], timeout_s=tmo)

    client   = _get_client()
    response = client.start_query_execution(
        QueryString=sql,
        WorkGroup=wg,
        ResultConfiguration={"OutputLocation": ATHENA_RESULTS_BUCKET},
        QueryExecutionContext={"Catalog": ATHENA_CATALOG, "Database": db},
    )
    query_id = response["QueryExecutionId"]

    _poll(client, query_id, wg, timeout_s=tmo, cancel_check=cancel_check)  # raises AthenaQueryTimeout/Failed/CancelledLeaseLost

    return wr.athena.get_query_results(
        query_execution_id=query_id,
        boto3_session=get_boto3_session(),
    )
