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
            "WHERE database_name IS NOT NULL AND database_name != '' "
            "UNION "
            "SELECT DISTINCT SUBSTR(table_fqn, INSTR(table_fqn,'.')+1, "
            "INSTR(SUBSTR(table_fqn,INSTR(table_fqn,'.')+1),'.') - 1) AS database_name "
            "FROM stream_registry WHERE table_fqn LIKE '%.%.%'"
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




# ── Partition Discovery ───────────────────────────────────────────────────────

def discover_partition_spec(
    database: str,
    table_name: str,
    workgroup: str = "standard",
) -> dict:
    """
    Discover the partition column name and type for an Iceberg table.

    Gap 13: Iceberg tables do not expose partition specs via Glue PartitionKeys.
    We query the $partitions metadata table to get the actual Iceberg
    partition spec (column name + transform).

    Returns dict:
      {
        "partition_column": "transaction_date",
        "partition_type":   "date",    # date|timestamp|int_yyyymmdd|string|identity|none
        "transform":        "days",    # days|months|years|hours|identity|bucket|truncate
        "source_column":    "transaction_date",
        "discovered":       True,
      }
    Returns {"partition_type": "none", "discovered": False} on failure.
    """
    if ZAMBONI_LOCAL_MODE:
        return {"partition_type": "date", "partition_column": "partition_date",
                "transform": "days", "discovered": False, "source": "local_mode_default"}

    try:
        # Strategy 1: query Iceberg $partitions metadata table
        from engine.utils.athena_client import read_sql
        sql = f"""
            SELECT partition
            FROM "glue_catalog"."{database}"."{table_name}$partitions"
            LIMIT 1
        """
        df = read_sql(sql, workgroup=workgroup, database=database)
        if not df.empty and "partition" in df.columns:
            # Partition column names come from the struct fields
            part_cols = list(df["partition"].iloc[0].keys()) if hasattr(df["partition"].iloc[0], "keys") else []
            if part_cols:
                col_name = part_cols[0]  # first partition column
                col_type = _infer_partition_type_from_glue(database, table_name, col_name)
                return {
                    "partition_column": col_name,
                    "partition_type":   col_type,
                    "transform":        "days" if col_type == "date" else "identity",
                    "source_column":    col_name,
                    "discovered":       True,
                    "source":           "$partitions_metadata",
                }
    except Exception:
        pass

    try:
        # Strategy 2: Glue StorageDescriptor.Columns + heuristic
        table = get_table(database, table_name)
        if not table:
            return {"partition_type": "none", "discovered": False}

        columns = table.get("StorageDescriptor", {}).get("Columns", [])
        result  = _detect_date_column(columns)
        if result:
            return {**result, "discovered": True, "source": "glue_columns_heuristic"}
    except Exception:
        pass

    return {"partition_type": "none", "discovered": False, "source": "not_found"}


def _infer_partition_type_from_glue(database: str, table_name: str, col_name: str) -> str:
    """Map Glue column type to Zamboni partition_type."""
    try:
        table   = get_table(database, table_name)
        columns = table.get("StorageDescriptor", {}).get("Columns", [])
        for col in columns:
            if col["Name"].lower() == col_name.lower():
                return _glue_type_to_partition_type(col.get("Type", ""))
    except Exception:
        pass
    return "date"  # safe default for date-named columns


def _glue_type_to_partition_type(glue_type: str) -> str:
    """Convert Glue type string to Zamboni partition_type."""
    t = glue_type.lower().strip()
    if t == "date":
        return "date"
    if t in ("timestamp", "timestamp with time zone"):
        return "timestamp"
    if t in ("int", "integer", "bigint", "long"):
        return "int_yyyymmdd"   # assume yyyyMMdd convention for int partitions
    if t in ("string", "varchar", "char"):
        return "string"
    return "date"  # fallback


def _detect_date_column(columns: list[dict]) -> dict | None:
    """
    Heuristic: find a date-named column in the table schema.
    Prefers columns named partition_date, load_date, process_date,
    transaction_date, event_date, business_date in priority order.
    """
    preferred = [
        "partition_date", "load_date", "process_date",
        "transaction_date", "event_date", "business_date",
        "created_date", "updated_date", "dt",
    ]
    col_map = {c["Name"].lower(): c for c in columns}

    for name in preferred:
        if name in col_map:
            col = col_map[name]
            return {
                "partition_column": col["Name"],
                "partition_type":   _glue_type_to_partition_type(col.get("Type", "")),
                "transform":        "days",
                "source_column":    col["Name"],
            }

    # Last resort: any column with "date" or "dt" in the name
    for name, col in col_map.items():
        if "date" in name or name.endswith("_dt"):
            return {
                "partition_column": col["Name"],
                "partition_type":   _glue_type_to_partition_type(col.get("Type", "")),
                "transform":        "days",
                "source_column":    col["Name"],
            }

    return None


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
