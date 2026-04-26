"""
Unit tests for archival validation gate logic.
No AWS required — mocks Athena and S3 calls.
"""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock
import pandas as pd

from engine.utils.partition_utils import (
    build_cold_partition_filter,
    build_archive_s3_prefix,
)


# ── Cold partition filter ─────────────────────────────────────────────────────

def test_cold_filter_30_days():
    result = build_cold_partition_filter(
        "partition_date", retention_days=30, reference_date=date(2026, 4, 1)
    )
    assert result == "partition_date < DATE '2026-03-02'"


def test_cold_filter_7_days():
    result = build_cold_partition_filter(
        "business_date", retention_days=7, reference_date=date(2026, 4, 1)
    )
    assert result == "business_date < DATE '2026-03-25'"


def test_cold_filter_90_days():
    result = build_cold_partition_filter(
        "partition_date", retention_days=90, reference_date=date(2026, 4, 1)
    )
    assert result == "partition_date < DATE '2026-01-01'"


# ── Archive S3 prefix ─────────────────────────────────────────────────────────

def test_archive_prefix_structure():
    prefix = build_archive_s3_prefix(
        domain="finance",
        table_name="finance_staging",
        partition_date=date(2026, 1, 15),
        archive_bucket="s3://my-archive",
    )
    assert "staging_archive/finance/finance_staging" in prefix
    assert "partition_date=2026-01-15" in prefix
    assert prefix.endswith("/")


def test_archive_prefix_different_domains():
    ers = build_archive_s3_prefix("ers", "ers_staging", date(2026, 2, 1), "s3://archive")
    fin = build_archive_s3_prefix("finance", "finance_staging", date(2026, 2, 1), "s3://archive")
    assert "ers" in ers
    assert "finance" in fin
    assert ers != fin


# ── Pre-validation logic ──────────────────────────────────────────────────────
# read_sql is imported as `from engine.utils.athena_client import read_sql`
# so patch must target engine.operations.archival.read_sql

def test_pre_validation_passes():
    with patch("engine.operations.archival.read_sql") as mock_sql:
        mock_sql.return_value = pd.DataFrame([{"row_count": 50_000, "null_pct": 0.5}])
        from engine.operations.archival import _pre_validate
        ok, detail = _pre_validate(
            table_fqn="glue_catalog.finance_db.finance_staging",
            partition_col="partition_date",
            partition_date=date(2026, 1, 1),
            workgroup="archival",
            min_row_count=1_000,
            max_null_pct=5.0,
        )
    assert ok is True
    assert detail["row_count"] == 50_000


def test_pre_validation_fails_low_row_count():
    with patch("engine.operations.archival.read_sql") as mock_sql:
        mock_sql.return_value = pd.DataFrame([{"row_count": 500, "null_pct": 0.0}])
        from engine.operations.archival import _pre_validate
        ok, detail = _pre_validate(
            table_fqn="glue_catalog.finance_db.finance_staging",
            partition_col="partition_date",
            partition_date=date(2026, 1, 1),
            workgroup="archival",
            min_row_count=1_000,
            max_null_pct=5.0,
        )
    assert ok is False
    assert "row_count" in detail["fail_reason"]


def test_pre_validation_fails_high_nulls():
    with patch("engine.operations.archival.read_sql") as mock_sql:
        mock_sql.return_value = pd.DataFrame([{"row_count": 50_000, "null_pct": 10.0}])
        from engine.operations.archival import _pre_validate
        ok, detail = _pre_validate(
            table_fqn="glue_catalog.finance_db.finance_staging",
            partition_col="partition_date",
            partition_date=date(2026, 1, 1),
            workgroup="archival",
            min_row_count=1_000,
            max_null_pct=5.0,
        )
    assert ok is False
    assert "null_pct" in detail["fail_reason"]


# ── Post-validation logic ─────────────────────────────────────────────────────

def test_post_validation_passes():
    mock_df = pd.DataFrame({"col": range(50_000)})
    with patch("awswrangler.s3.read_parquet", return_value=mock_df):
        from engine.operations.archival import _post_validate
        ok, detail = _post_validate(
            source_row_count=50_000,
            archive_path="s3://archive/staging_archive/finance/table/partition_date=2026-01-01/",
            workgroup="archival",
        )
    assert ok is True
    assert detail["archived_rows"] == 50_000


def test_post_validation_fails_row_mismatch():
    mock_df = pd.DataFrame({"col": range(49_999)})
    with patch("awswrangler.s3.read_parquet", return_value=mock_df):
        from engine.operations.archival import _post_validate
        ok, detail = _post_validate(
            source_row_count=50_000,
            archive_path="s3://archive/path/",
            workgroup="archival",
        )
    assert ok is False
    assert "mismatch" in detail["fail_reason"]


def test_post_validation_fails_on_s3_error():
    with patch("awswrangler.s3.read_parquet", side_effect=Exception("S3 access denied")):
        from engine.operations.archival import _post_validate
        ok, detail = _post_validate(
            source_row_count=1000,
            archive_path="s3://archive/path/",
            workgroup="archival",
        )
    assert ok is False
    assert "error" in detail


# ── Delete blocked until post-validation passes ───────────────────────────────

def test_archive_partition_blocks_delete_on_pre_fail():
    """If pre-validation fails, export and delete should NOT be called."""
    table_row = {
        "table_fqn":              "glue_catalog.finance_db.finance_staging",
        "domain":                 "finance",
        "tier":                   "standard",
        "environment":            "prod",
        "archive_retention_days": 30,
        "archive_min_row_count":  1_000,
        "archive_max_null_pct":   5.0,
        "archive_bucket":         None,
    }

    with patch("engine.operations.archival.read_sql") as mock_sql, \
         patch("engine.operations.archival._resolve_partition_column",
               return_value="partition_date"), \
         patch("engine.operations.archival._export_partition") as mock_export, \
         patch("engine.operations.archival._delete_partition") as mock_delete:

        # Low row count → pre-validation fails
        mock_sql.return_value = pd.DataFrame([{"row_count": 50, "null_pct": 0.0}])

        from engine.operations.archival import archive_partition
        result = archive_partition(
            table_row=table_row,
            partition_date=date(2026, 1, 1),
            dry_run=False,
        )

    assert result["status"] == "FAILURE"
    assert result["pre_validation"] == "FAIL"
    mock_export.assert_not_called()
    mock_delete.assert_not_called()
