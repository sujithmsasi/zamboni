"""
Zamboni — Athena Client
Thin wrapper around boto3 Athena — workgroup-aware, polls to completion.
All Athena calls in the project go through here.
"""
import time
import boto3
from typing import Optional
from config.settings import (
    ATHENA_CATALOG, ATHENA_DATABASE,
    ATHENA_RESULTS_BUCKET, ATHENA_WORKGROUPS, AWS_REGION
)
from engine.utils.logger import get_logger

log = get_logger(__name__)

_client: Optional[boto3.client] = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("athena", region_name=AWS_REGION)
    return _client


def run_query(
    sql: str,
    workgroup: str = "standard",
    database: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Execute an Athena query and wait for completion.

    Args:
        sql:        SQL to run.
        workgroup:  Tier key (critical|standard|low|archival|app)
                    OR a literal workgroup name if not in the map.
        database:   Athena database context. Defaults to ATHENA_DATABASE.
        dry_run:    Log SQL only — do not execute.

    Returns:
        QueryExecutionId on success, None on dry_run.

    Raises:
        RuntimeError if the query FAILED or was CANCELLED.
    """
    wg = ATHENA_WORKGROUPS.get(workgroup, workgroup)
    db = database or ATHENA_DATABASE

    if dry_run:
        log.info("athena.dry_run", workgroup=wg, database=db, sql=sql[:300])
        return None

    client = _get_client()
    response = client.start_query_execution(
        QueryString=sql,
        WorkGroup=wg,
        ResultConfiguration={"OutputLocation": ATHENA_RESULTS_BUCKET},
        QueryExecutionContext={"Catalog": ATHENA_CATALOG, "Database": db},
    )
    query_id = response["QueryExecutionId"]
    log.info("athena.submitted", query_id=query_id, workgroup=wg)

    return _poll(client, query_id, wg)


def _poll(client, query_id: str, workgroup: str, interval: int = 3) -> str:
    """Poll until terminal state. Raises on FAILED / CANCELLED."""
    while True:
        resp  = client.get_query_execution(QueryExecutionId=query_id)
        state = resp["QueryExecution"]["Status"]["State"]

        if state == "SUCCEEDED":
            stats = resp["QueryExecution"].get("Statistics", {})
            log.info(
                "athena.succeeded",
                query_id=query_id,
                workgroup=workgroup,
                bytes_scanned=stats.get("DataScannedInBytes", 0),
                execution_ms=stats.get("TotalExecutionTimeInMillis", 0),
            )
            return query_id

        if state in ("FAILED", "CANCELLED"):
            reason = resp["QueryExecution"]["Status"].get("StateChangeReason", "unknown")
            log.error("athena.failed", query_id=query_id, state=state, reason=reason)
            raise RuntimeError(f"Athena query {query_id} {state}: {reason}")

        log.debug("athena.polling", query_id=query_id, state=state)
        time.sleep(interval)


def get_query_stats(query_id: str) -> dict:
    """Return cost-tracking stats for a completed query."""
    resp  = _get_client().get_query_execution(QueryExecutionId=query_id)
    stats = resp["QueryExecution"].get("Statistics", {})
    return {
        "bytes_scanned": stats.get("DataScannedInBytes", 0),
        "execution_ms":  stats.get("TotalExecutionTimeInMillis", 0),
    }


def read_sql(
    sql: str,
    workgroup: str = "standard",
    database: Optional[str] = None,
) -> "pandas.DataFrame":
    """
    Execute a SELECT and return results as a pandas DataFrame.
    Uses awswrangler for clean result fetching.
    """
    import awswrangler as wr
    import boto3 as _boto3

    wg = ATHENA_WORKGROUPS.get(workgroup, workgroup)
    db = database or ATHENA_DATABASE

    log.info("athena.read_sql", workgroup=wg, database=db, sql=sql[:300])

    return wr.athena.read_sql_query(
        sql=sql,
        database=db,
        workgroup=wg,
        boto3_session=_boto3.Session(region_name=AWS_REGION),
        ctas_approach=False,
    )
