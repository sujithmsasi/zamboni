"""
Zamboni — Glue Client
Catalog reads, table discovery, job status checks (Gate 1).
"""
import boto3
from typing import Optional
from config.settings import AWS_REGION
from engine.utils.logger import get_logger

log = get_logger(__name__)

_client: Optional[boto3.client] = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("glue", region_name=AWS_REGION)
    return _client


# ── Catalog ───────────────────────────────────────────────────────────────────

def get_databases() -> list[str]:
    """Return all Glue database names."""
    paginator = _get_client().get_paginator("get_databases")
    names = []
    for page in paginator.paginate():
        for db in page.get("DatabaseList", []):
            names.append(db["Name"])
    return names


def get_tables(database: str) -> list[dict]:
    """Return all tables in a Glue database (paginated)."""
    paginator = _get_client().get_paginator("get_tables")
    tables = []
    for page in paginator.paginate(DatabaseName=database):
        tables.extend(page.get("TableList", []))
    return tables


def get_table(database: str, table_name: str) -> Optional[dict]:
    """Return a single table or None if not found."""
    try:
        return _get_client().get_table(DatabaseName=database, Name=table_name)["Table"]
    except _get_client().exceptions.EntityNotFoundException:
        return None


def is_iceberg_table(table: dict) -> bool:
    """Return True if a Glue table is an Iceberg table."""
    params = table.get("Parameters", {})
    return params.get("table_type", "").upper() == "ICEBERG"


def drop_table(database: str, table_name: str, dry_run: bool = False) -> bool:
    """Drop a table from Glue catalog. Returns True on success."""
    if dry_run:
        log.info("glue.drop_table.dry_run", database=database, table=table_name)
        return True
    try:
        _get_client().delete_table(DatabaseName=database, Name=table_name)
        log.info("glue.drop_table.done", database=database, table=table_name)
        return True
    except Exception as e:
        log.error("glue.drop_table.failed", database=database, table=table_name, error=str(e))
        return False


def get_table_location(database: str, table_name: str) -> Optional[str]:
    """Return the S3 location of a table, or None."""
    table = get_table(database, table_name)
    if not table:
        return None
    return table.get("StorageDescriptor", {}).get("Location")


# ── Gate 1 — Upstream Job Status ──────────────────────────────────────────────

def get_last_job_run(job_name: str) -> Optional[dict]:
    """
    Return the most recent Glue job run or None.
    Used for Gate 1 upstream batch completion check.
    """
    try:
        runs = _get_client().get_job_runs(JobName=job_name, MaxResults=1).get("JobRuns", [])
        return runs[0] if runs else None
    except _get_client().exceptions.EntityNotFoundException:
        log.warning("glue.job_not_found", job_name=job_name)
        return None


def is_upstream_job_complete(job_name: str) -> bool:
    """
    Return True if the most recent run of the upstream Glue job SUCCEEDED.
    Returns False if: job never ran, still running, or failed.
    Gate 1 uses this to determine if it is safe to run HK.
    """
    run = get_last_job_run(job_name)
    if not run:
        return False
    state = run.get("JobRunState", "")
    log.info("glue.gate1_check", job_name=job_name, state=state)
    return state == "SUCCEEDED"
