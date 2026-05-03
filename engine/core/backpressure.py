"""
Zamboni — Athena Backpressure Helper
v2 Design Section 9 / Phase 7 — before dispatching new Athena work, check
how many queries are currently RUNNING in the workgroup. If at the
MaxConcurrentQueries limit, wait with exponential backoff.

Lightweight — single API call per check, exponential backoff on saturation.
Falls open (allows the dispatch) if the check itself fails — never blocks.
"""
from __future__ import annotations

import random
import time

from config.settings import AWS_REGION
from engine.utils.logger import get_logger

log = get_logger(__name__)

# ── Default concurrency ceilings per workgroup (v2 design) ───────────────────
# Match these to actual Athena workgroup MaxConcurrentQueries settings.
_DEFAULT_LIMITS = {
    "zamboni-critical": 25,
    "zamboni-standard": 25,
    "zamboni-low":      10,
    "zamboni-archival": 10,
    "zamboni-app":      5,
}


def get_running_query_count(workgroup: str) -> int | None:
    """
    Query Athena for the count of currently RUNNING queries in this workgroup.
    Returns None on failure — caller treats as 'unknown, allow dispatch'.
    """
    try:
        import boto3
        client = boto3.client("athena", region_name=AWS_REGION)

        # list_query_executions returns IDs across all workgroups.
        # Athena does NOT provide a direct workgroup filter — we list recent
        # IDs and check each. To keep this cheap, we fetch a small page and
        # filter by status + workgroup using GetQueryExecution.
        response = client.list_query_executions(
            WorkGroup=workgroup,
            MaxResults=50,
        )
        ids = response.get("QueryExecutionIds", [])
        if not ids:
            return 0

        # Batch get details — Athena allows up to 50 IDs per call
        batch = client.batch_get_query_execution(
            QueryExecutionIds=ids[:50]
        )
        running = sum(
            1 for q in batch.get("QueryExecutions", [])
            if q.get("Status", {}).get("State") in ("QUEUED", "RUNNING")
        )
        return running
    except Exception as e:
        log.warning(
            "backpressure.count_check_failed",
            workgroup=workgroup, error=str(e),
        )
        return None


def wait_for_capacity(
    workgroup: str,
    max_wait_seconds: int = 60,
    limit: int | None = None,
) -> bool:
    """
    Block until the workgroup has capacity for one more query, or timeout.
    Returns True if capacity is available, False if timed out.
    Falls open — if the count check fails, returns True immediately.
    """
    limit = limit or _DEFAULT_LIMITS.get(workgroup, 25)
    start = time.time()
    attempt = 0

    while time.time() - start < max_wait_seconds:
        count = get_running_query_count(workgroup)
        if count is None:
            # Can't check — fail open
            return True
        if count < limit:
            return True

        # Exponential backoff with jitter
        attempt += 1
        delay = min(8.0, 0.5 * (2 ** attempt)) + random.uniform(0, 0.3)
        log.info(
            "backpressure.waiting",
            workgroup=workgroup,
            running=count, limit=limit, delay_s=round(delay, 1),
        )
        time.sleep(delay)

    log.warning(
        "backpressure.timeout",
        workgroup=workgroup,
        max_wait_seconds=max_wait_seconds,
    )
    return False


def can_dispatch(workgroup: str, limit: int | None = None) -> bool:
    """
    Non-blocking check — return True if there's capacity right now.
    Used for fast skip rather than wait+retry.
    """
    limit = limit or _DEFAULT_LIMITS.get(workgroup, 25)
    count = get_running_query_count(workgroup)
    if count is None:
        return True  # fail open
    return count < limit
