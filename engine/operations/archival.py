"""
Zamboni — Archival Operation
Export-then-Delete for cold staging partitions.

Four-step gate (DELETE only runs if all prior steps pass):
  1. Pre-archive validation  — row count + null check on source partition
  2. Export                  — write to archive S3 as Parquet (INTELLIGENT_TIERING)
  3. Post-archive validation — reconcile exported row count vs source
  4. Delete                  — remove partition from staging Iceberg table

If any step fails, the partition is left intact in staging and an SNS alert is sent.
"""
from datetime import date

import awswrangler as wr
import boto3
import pandas as pd

from config.settings import ARCHIVE_BUCKET, AWS_REGION
from engine.utils.athena_client import read_sql
from engine.utils.logger import get_logger
from engine.utils.partition_utils import (
    build_archive_s3_prefix,
    parse_table_fqn,
)

log = get_logger(__name__)

# Defaults for validation gates
DEFAULT_MIN_ROW_COUNT  = 1_000
DEFAULT_MAX_NULL_PCT   = 5.0

# Partition column fallback order
PARTITION_COL_CANDIDATES = ["partition_date", "business_date", "load_date"]


def archive_partition(
    table_row: dict,
    partition_date: date,
    workgroup: str = "archival",
    dry_run: bool = False,
) -> dict:
    """
    Archive a single partition from staging to S3.

    Args:
        table_row:       stream_registry row for this table
        partition_date:  Date of the partition to archive
        workgroup:       Athena workgroup for queries
        dry_run:         If True, validate only — do not export or delete

    Returns:
        dict with status, rows_archived, bytes_archived, validation outcomes
    """
    table_fqn      = table_row["table_fqn"]
    domain         = table_row.get("domain", "unknown")
    partition_col  = _resolve_partition_column(table_row, table_fqn, workgroup)
    archive_bucket = table_row.get("archive_bucket") or ARCHIVE_BUCKET
    min_rows       = table_row.get("archive_min_row_count") or DEFAULT_MIN_ROW_COUNT
    max_null_pct   = table_row.get("archive_max_null_pct") or DEFAULT_MAX_NULL_PCT
    _, database, table_name = parse_table_fqn(table_fqn)

    archive_path = build_archive_s3_prefix(
        domain=domain,
        table_name=table_name,
        partition_date=partition_date,
        archive_bucket=archive_bucket,
    )

    result = {
        "table_fqn":       table_fqn,
        "partition_date":  partition_date,
        "archive_s3_path": archive_path,
        "partition_col":   partition_col,
        "pre_validation":  None,
        "post_validation": None,
        "rows_archived":   0,
        "bytes_archived":  0,
        "status":          "PENDING",
        "dry_run":         dry_run,
    }

    # ── Step 1 — Pre-archive validation ───────────────────────────────────────
    pre_ok, pre_detail = _pre_validate(
        table_fqn=table_fqn,
        partition_col=partition_col,
        partition_date=partition_date,
        workgroup=workgroup,
        min_row_count=min_rows,
        max_null_pct=max_null_pct,
    )
    result["pre_validation"] = "PASS" if pre_ok else "FAIL"
    result["pre_detail"]     = pre_detail

    if not pre_ok:
        log.warning(
            "archival.pre_validation_failed",
            table_fqn=table_fqn,
            partition_date=str(partition_date),
            detail=pre_detail,
        )
        result["status"] = "FAILURE"
        result["error"]  = f"Pre-validation failed: {pre_detail}"
        return result

    log.info("archival.pre_validation_passed", table_fqn=table_fqn,
             partition_date=str(partition_date), detail=pre_detail)

    if dry_run:
        result["status"] = "DRY_RUN"
        result["rows_archived"] = pre_detail.get("row_count", 0)
        return result

    # ── Step 2 — Export to archive S3 ────────────────────────────────────────
    try:
        rows_exported, bytes_exported = _export_partition(
            table_fqn=table_fqn,
            partition_col=partition_col,
            partition_date=partition_date,
            archive_path=archive_path,
            workgroup=workgroup,
        )
        result["rows_archived"]  = rows_exported
        result["bytes_archived"] = bytes_exported
    except Exception as e:
        log.error("archival.export_failed", table_fqn=table_fqn,
                  partition_date=str(partition_date), error=str(e))
        result["status"] = "FAILURE"
        result["error"]  = f"Export failed: {e}"
        return result

    # ── Step 3 — Post-archive validation ─────────────────────────────────────
    # Compare against rows_exported (the count actually read at export time,
    # step 2) rather than pre_detail's step-1 count -- tighter, since a row
    # written between steps 1 and 2 would already be reflected in what was
    # exported but not in the older pre-validate count.
    post_ok, post_detail = _post_validate(
        source_row_count=rows_exported,
        archive_path=archive_path,
        workgroup=workgroup,
    )
    result["post_validation"] = "PASS" if post_ok else "FAIL"
    result["post_detail"]     = post_detail

    if not post_ok:
        log.error(
            "archival.post_validation_failed",
            table_fqn=table_fqn,
            partition_date=str(partition_date),
            detail=post_detail,
        )
        result["status"] = "FAILURE"
        result["error"]  = f"Post-validation failed: {post_detail}"
        return result

    log.info("archival.post_validation_passed", table_fqn=table_fqn,
             partition_date=str(partition_date))

    # ── Step 3.5 — Pre-delete freshness re-check ─────────────────────────────
    # Real gap fixed here (2026-07-09 audit): steps 1-3 above validate a
    # snapshot of the partition taken *before* delete, but DELETE (step 4)
    # is unconditional on the partition's *current* state at delete time --
    # Athena/Iceberg's DELETE has no "AS OF snapshot" scoping to pin it to
    # exactly the rows that were exported. A row written into this partition
    # after export but before delete would be silently destroyed, never
    # archived. Closed by re-counting the partition immediately before
    # delete and refusing to delete (fail closed, same as every other gate
    # in this sequence) if it no longer matches what was actually exported.
    fresh_ok, fresh_detail = _pre_delete_check(
        table_fqn=table_fqn,
        partition_col=partition_col,
        partition_date=partition_date,
        workgroup=workgroup,
        expected_row_count=rows_exported,
    )
    result["pre_delete_check"] = "PASS" if fresh_ok else "FAIL"
    result["pre_delete_detail"] = fresh_detail

    if not fresh_ok:
        log.error(
            "archival.pre_delete_check_failed",
            table_fqn=table_fqn,
            partition_date=str(partition_date),
            detail=fresh_detail,
        )
        result["status"] = "FAILURE"
        result["error"]  = f"Pre-delete check failed (partition changed since export): {fresh_detail}"
        return result

    # ── Step 4 — Delete from staging ─────────────────────────────────────────
    try:
        _delete_partition(
            table_fqn=table_fqn,
            partition_col=partition_col,
            partition_date=partition_date,
            workgroup=workgroup,
        )
    except Exception as e:
        log.error("archival.delete_failed", table_fqn=table_fqn,
                  partition_date=str(partition_date), error=str(e))
        result["status"] = "FAILURE"
        result["error"]  = f"Delete failed: {e}"
        return result

    result["status"] = "SUCCESS"
    log.info(
        "archival.partition_complete",
        table_fqn=table_fqn,
        partition_date=str(partition_date),
        rows=rows_exported,
        bytes=bytes_exported,
        archive_path=archive_path,
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  VALIDATION GATES
# ══════════════════════════════════════════════════════════════════════════════

def _pre_validate(
    table_fqn: str,
    partition_col: str,
    partition_date: date,
    workgroup: str,
    min_row_count: int,
    max_null_pct: float,
) -> tuple[bool, dict]:
    """
    Pre-archive validation:
      - Row count must be >= min_row_count
      - Null % in partition column must be <= max_null_pct
    """
    _, database, table = parse_table_fqn(table_fqn)

    sql = f"""
        SELECT
            COUNT(*)                                        AS row_count,
            ROUND(
                SUM(CASE WHEN {partition_col} IS NULL THEN 1 ELSE 0 END)
                * 100.0 / COUNT(*), 2
            )                                              AS null_pct
        FROM glue_catalog.{database}.{table}
        WHERE {partition_col} = DATE '{partition_date.isoformat()}'
    """

    df = read_sql(sql, workgroup=workgroup)
    if df.empty:
        return False, {"error": "Query returned no results"}

    row       = df.iloc[0]
    row_count = int(row.get("row_count") or 0)
    null_pct  = float(row.get("null_pct") or 0.0)

    detail = {"row_count": row_count, "null_pct": null_pct}

    if row_count < min_row_count:
        detail["fail_reason"] = f"row_count {row_count} < min {min_row_count}"
        return False, detail

    if null_pct > max_null_pct:
        detail["fail_reason"] = f"null_pct {null_pct}% > max {max_null_pct}%"
        return False, detail

    return True, detail


def _post_validate(
    source_row_count: int,
    archive_path: str,
    workgroup: str,
) -> tuple[bool, dict]:
    """
    Post-archive validation:
      - Read exported Parquet from archive S3
      - Row count must match source exactly
    """
    try:
        session = boto3.Session(region_name=AWS_REGION)
        df      = wr.s3.read_parquet(path=archive_path, boto3_session=session)
        archived_count = len(df)
    except Exception as e:
        return False, {"error": f"Could not read archive: {e}"}

    detail = {
        "source_rows":   source_row_count,
        "archived_rows": archived_count,
    }

    if archived_count != source_row_count:
        detail["fail_reason"] = (
            f"Row count mismatch: source={source_row_count}, "
            f"archived={archived_count}"
        )
        return False, detail

    return True, detail


def _pre_delete_check(
    table_fqn: str,
    partition_col: str,
    partition_date: date,
    workgroup: str,
    expected_row_count: int,
) -> tuple[bool, dict]:
    """
    Re-count the partition immediately before DELETE and confirm it still
    matches what was exported. See the "Step 3.5" comment at the call site
    for why this exists -- closes the export-to-delete TOCTOU window.
    """
    _, database, table = parse_table_fqn(table_fqn)
    sql = f"""
        SELECT COUNT(*) AS row_count
        FROM glue_catalog.{database}.{table}
        WHERE {partition_col} = DATE '{partition_date.isoformat()}'
    """
    df = read_sql(sql, workgroup=workgroup)
    if df.empty:
        return False, {"error": "Pre-delete count query returned no results"}

    current_count = int(df.iloc[0].get("row_count") or 0)
    detail = {"expected_row_count": expected_row_count, "current_row_count": current_count}

    if current_count != expected_row_count:
        detail["fail_reason"] = (
            f"Partition row count changed since export: "
            f"expected {expected_row_count}, now {current_count}"
        )
        return False, detail

    return True, detail


# ══════════════════════════════════════════════════════════════════════════════
#  EXPORT + DELETE
# ══════════════════════════════════════════════════════════════════════════════

def _export_partition(
    table_fqn: str,
    partition_col: str,
    partition_date: date,
    archive_path: str,
    workgroup: str,
) -> tuple[int, int]:
    """
    Read partition from staging via Athena and write to archive S3
    as Parquet with INTELLIGENT_TIERING storage class.

    Returns: (row_count, bytes_written)
    """
    _, database, table = parse_table_fqn(table_fqn)

    sql = f"""
        SELECT *
        FROM glue_catalog.{database}.{table}
        WHERE {partition_col} = DATE '{partition_date.isoformat()}'
    """

    log.info("archival.exporting", table_fqn=table_fqn,
             partition_date=str(partition_date), archive_path=archive_path)

    session = boto3.Session(region_name=AWS_REGION)
    df      = wr.athena.read_sql_query(
        sql=sql,
        database=database,
        workgroup=workgroup,
        boto3_session=session,
        ctas_approach=False,
    )

    if df.empty:
        raise ValueError(f"No data found for partition {partition_date}")

    # Write to archive S3 with INTELLIGENT_TIERING
    wr.s3.to_parquet(
        df=df,
        path=archive_path,
        dataset=False,
        boto3_session=session,
        s3_additional_kwargs={"StorageClass": "INTELLIGENT_TIERING"},
    )

    # Estimate bytes (rough: memory size of DataFrame)
    bytes_written = df.memory_usage(deep=True).sum()

    log.info(
        "archival.export_done",
        table_fqn=table_fqn,
        partition_date=str(partition_date),
        rows=len(df),
        bytes=bytes_written,
    )
    return len(df), int(bytes_written)


def _delete_partition(
    table_fqn: str,
    partition_col: str,
    partition_date: date,
    workgroup: str,
) -> None:
    """
    Delete partition from staging Iceberg table via Athena DELETE.
    Only called after post-archive validation PASSES.
    """
    from engine.utils.athena_client import run_query
    _, database, table = parse_table_fqn(table_fqn)

    sql = f"""
        DELETE FROM glue_catalog.{database}.{table}
        WHERE {partition_col} = DATE '{partition_date.isoformat()}'
    """
    log.info("archival.deleting_partition", table_fqn=table_fqn,
             partition_date=str(partition_date))
    run_query(sql, workgroup=workgroup)
    log.info("archival.partition_deleted", table_fqn=table_fqn,
             partition_date=str(partition_date))


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_partition_column(
    table_row: dict,
    table_fqn: str,
    workgroup: str,
) -> str:
    """
    Resolve partition column for a table.
    Priority:
      1. hk_config.partition_column (registered)
      2. Auto-detect from Glue catalog columns
      3. Raise if none found
    """
    # 1. From config
    from engine.core.config import get_hk_config
    hk_config = get_hk_config(table_fqn)
    if hk_config and hk_config.get("partition_column"):
        return hk_config["partition_column"]

    # 2. Auto-detect from table columns
    _, database, table_name = parse_table_fqn(table_fqn)
    from engine.utils.glue_client import get_table as glue_get_table
    glue_table = glue_get_table(database, table_name)
    if glue_table:
        columns = [
            col["Name"].lower()
            for col in glue_table.get("StorageDescriptor", {}).get("Columns", [])
            + glue_table.get("PartitionKeys", [])
        ]
        for candidate in PARTITION_COL_CANDIDATES:
            if candidate in columns:
                log.info(
                    "archival.partition_col_auto_detected",
                    table_fqn=table_fqn,
                    column=candidate,
                )
                return candidate

    raise ValueError(
        f"Cannot determine partition column for {table_fqn}. "
        f"Register it via Zamboni app or set partition_column in hk_config."
    )


def discover_cold_partitions(
    table_fqn: str,
    partition_col: str,
    retention_days: int,
    workgroup: str = "archival",
) -> list[date]:
    """
    Discover partitions older than retention_days in a staging table.
    Returns list of dates eligible for archival, oldest first.
    """
    from engine.utils.partition_utils import build_cold_partition_filter
    cold_filter = build_cold_partition_filter(partition_col, retention_days)

    _, database, table = parse_table_fqn(table_fqn)
    sql = f"""
        SELECT DISTINCT {partition_col} AS partition_date
        FROM glue_catalog.{database}.{table}
        WHERE {cold_filter}
        ORDER BY {partition_col} ASC
    """

    df = read_sql(sql, workgroup=workgroup)
    if df.empty:
        return []

    return [
        pd.Timestamp(d).date()
        for d in df["partition_date"].dropna().tolist()
    ]
