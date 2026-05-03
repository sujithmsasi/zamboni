"""
Zamboni — Glue Client
Catalog reads, table discovery, job status checks (Gate 1).
"""
from __future__ import annotations

import boto3

from config.settings import AWS_REGION, ZAMBONI_LOCAL_MODE
from engine.utils.logger import get_logger

log = get_logger(__name__)

_client: boto3.client | None = None

# Databases shown in local mode (seeded by seed_local_db.py)
_LOCAL_DATABASES = [
    "finance_staging_db", "finance_datalake_db",
    "finance_base_db", "finance_master_db",
    "ers_staging_db", "ers_datalake_db",
    "membership_staging_db", "claims_staging_db",
    "preprod_finance_db", "dev_ers_db",
]


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("glue", region_name=AWS_REGION)
    return _client


# ── Catalog ───────────────────────────────────────────────────────────────────

def get_databases() -> list[str]:
    """Return all Glue database names."""
    if ZAMBONI_LOCAL_MODE:
        from engine.utils.local_db import read_sql_local
        df = read_sql_local(
            "SELECT DISTINCT database_name FROM stream_registry "
            "UNION SELECT DISTINCT database_name FROM nonprod_registry "
            "WHERE database_name IS NOT NULL"
        )
        if not df.empty and "database_name" in df.columns:
            return df["database_name"].dropna().tolist()
        # Fallback: derive from table_fqn
        df2 = read_sql_local("SELECT DISTINCT table_fqn FROM stream_registry")
        if df2.empty:
            return _LOCAL_DATABASES
        dbs = set()
        for fqn in df2["table_fqn"].tolist():
            parts = str(fqn).split(".")
            if len(parts) >= 2:
                dbs.add(parts[-2])
        return sorted(dbs) or _LOCAL_DATABASES
    paginator = _get_client().get_paginator("get_databases")
    names = []
    for page in paginator.paginate():
        for db in page.get("DatabaseList", []):
            names.append(db["Name"])
    return names


def get_tables(database: str, table_format: str | None = None) -> list[dict]:
    """Return all tables in a Glue database (paginated)."""
    if ZAMBONI_LOCAL_MODE:
        from engine.utils.local_db import read_sql_local
        sql = (f"SELECT table_fqn, table_format FROM stream_registry "
               f"WHERE table_fqn LIKE '%.{database}.%'")
        df = read_sql_local(sql)
        result = []
        for _, row in df.iterrows():
            fqn   = str(row.get("table_fqn", ""))
            parts = fqn.split(".")
            name  = parts[-1] if parts else fqn
            fmt   = str(row.get("table_format", "iceberg"))
            if table_format and fmt != table_format:
                continue
            result.append({"Name": name, "DatabaseName": database,
                           "Parameters": {"table_type": fmt.upper()}})
        return result
    paginator = _get_client().get_paginator("get_tables")
    tables = []
    for page in paginator.paginate(DatabaseName=database):
        tables.extend(page.get("TableList", []))
    return tables


def get_table(database: str, table_name: str) -> dict | None:
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


def get_table_location(database: str, table_name: str) -> str | None:
    """Return the S3 location of a table, or None."""
    table = get_table(database, table_name)
    if not table:
        return None
    return table.get("StorageDescriptor", {}).get("Location")


# ── Gate 1 — Upstream Job Status ──────────────────────────────────────────────

def get_last_job_run(job_name: str) -> dict | None:
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
    if ZAMBONI_LOCAL_MODE:
        # In local mode all upstream jobs are considered complete
        log.debug("glue.local_mode.job_complete", job_name=job_name)
        return True
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
