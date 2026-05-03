"""
Zamboni — Catalog Cleanup Operation
Hard deletion for non-prod tables that have passed through PENDING_DROP.
Two steps (both must succeed):
  1. Drop table from Glue catalog
  2. Sweep S3 data/ and metadata/ prefixes
"""
from engine.utils.glue_client import drop_table, get_table_location
from engine.utils.logger import get_logger
from engine.utils.partition_utils import parse_table_fqn
from engine.utils.s3_client import delete_prefix, get_prefix_size_bytes, parse_s3_uri

log = get_logger(__name__)


def cleanup_table(
    table_fqn: str,
    dry_run: bool = False,
) -> dict:
    """
    Permanently delete a non-prod table.
    Drops from Glue catalog and sweeps all S3 data.

    Args:
        table_fqn: Fully qualified table name
        dry_run:   If True, calculate bytes to reclaim but do not delete

    Returns:
        dict with catalog_dropped, s3_cleaned, bytes_reclaimed, error
    """
    _, database, table_name = parse_table_fqn(table_fqn)

    result = {
        "table_fqn":      table_fqn,
        "catalog_dropped": False,
        "s3_cleaned":      False,
        "bytes_reclaimed": 0,
        "dry_run":         dry_run,
        "error":           None,
    }

    # ── Get S3 location before dropping from catalog ──────────────────────────
    s3_location = get_table_location(database, table_name)
    log.info(
        "catalog_cleanup.start",
        table_fqn=table_fqn,
        s3_location=s3_location,
        dry_run=dry_run,
    )

    # ── Step 1 — Estimate bytes to reclaim ────────────────────────────────────
    if s3_location:
        try:
            bucket, prefix = parse_s3_uri(s3_location)
            bytes_reclaimed = get_prefix_size_bytes(bucket, prefix)
            result["bytes_reclaimed"] = bytes_reclaimed
            log.info(
                "catalog_cleanup.s3_size",
                table_fqn=table_fqn,
                bytes=bytes_reclaimed,
                gb=round(bytes_reclaimed / 1e9, 3),
            )
        except Exception as e:
            log.warning("catalog_cleanup.size_check_failed", error=str(e))

    if dry_run:
        result["catalog_dropped"] = True   # would be dropped
        result["s3_cleaned"]      = True   # would be cleaned
        log.info("catalog_cleanup.dry_run", table_fqn=table_fqn,
                 bytes_reclaimed=result["bytes_reclaimed"])
        return result

    # ── Step 2 — Drop from Glue catalog ──────────────────────────────────────
    try:
        dropped = drop_table(database, table_name, dry_run=False)
        result["catalog_dropped"] = dropped
        log.info("catalog_cleanup.catalog_dropped", table_fqn=table_fqn)
    except Exception as e:
        result["error"] = f"Glue DROP failed: {e}"
        log.error("catalog_cleanup.catalog_drop_failed", table_fqn=table_fqn, error=str(e))
        return result

    # ── Step 3 — Sweep S3 ─────────────────────────────────────────────────────
    if s3_location:
        try:
            bucket, prefix = parse_s3_uri(s3_location)
            deleted = delete_prefix(bucket, prefix, dry_run=False)
            result["s3_cleaned"] = True
            log.info(
                "catalog_cleanup.s3_swept",
                table_fqn=table_fqn,
                objects_deleted=deleted,
            )
        except Exception as e:
            result["error"] = f"S3 sweep failed: {e}"
            log.error("catalog_cleanup.s3_sweep_failed", table_fqn=table_fqn, error=str(e))
            # Catalog is already dropped — partial cleanup, still report
            return result
    else:
        log.warning("catalog_cleanup.no_s3_location", table_fqn=table_fqn)
        result["s3_cleaned"] = True   # Nothing to sweep

    log.info(
        "catalog_cleanup.complete",
        table_fqn=table_fqn,
        bytes_reclaimed=result["bytes_reclaimed"],
    )
    return result


def is_backup_pattern(table_name: str) -> tuple[bool, str]:
    """
    Detect backup/temp table naming patterns.
    Used by Lifecycle Engine to auto-flag tables for faster cleanup.

    Returns: (is_backup, pattern_matched)
    """
    import re
    patterns = [
        (r"_bkp$",            "_bkp"),
        (r"_backup$",         "_backup"),
        (r"_bak$",            "_bak"),
        (r"_copy$",           "_copy"),
        (r"_temp$",           "_temp"),
        (r"_tmp$",            "_tmp"),
        (r"_old$",            "_old"),
        (r"_\d{8}$",          "timestamp_suffix"),   # e.g. _20260101
        (r"_\d{4}_\d{2}_\d{2}$", "date_suffix"),    # e.g. _2026_01_01
    ]
    name_lower = table_name.lower()
    for pattern, label in patterns:
        if re.search(pattern, name_lower):
            return True, label
    return False, ""
