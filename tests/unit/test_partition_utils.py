"""
Unit tests for engine/utils/partition_utils.py
No AWS required — pure logic tests.
"""
from datetime import date

import pytest

from engine.utils.partition_utils import (
    build_archive_s3_prefix,
    build_cold_partition_filter,
    build_hot_partition_filter,
    date_range,
    parse_table_fqn,
)

# ── build_hot_partition_filter ────────────────────────────────────────────────

def test_hot_filter_with_days():
    result = build_hot_partition_filter(
        "partition_date", days=7, reference_date=date(2026, 4, 1)
    )
    assert result == "partition_date >= DATE '2026-03-25'"


def test_hot_filter_no_days_returns_none():
    result = build_hot_partition_filter("partition_date", days=None)
    assert result is None


def test_hot_filter_one_day():
    result = build_hot_partition_filter(
        "partition_date", days=1, reference_date=date(2026, 4, 1)
    )
    assert result == "partition_date >= DATE '2026-03-31'"


# ── build_cold_partition_filter ───────────────────────────────────────────────

def test_cold_filter_30_days():
    result = build_cold_partition_filter(
        "partition_date", retention_days=30, reference_date=date(2026, 4, 1)
    )
    assert result == "partition_date < DATE '2026-03-02'"


def test_cold_filter_90_days():
    result = build_cold_partition_filter(
        "partition_date", retention_days=90, reference_date=date(2026, 4, 1)
    )
    assert result == "partition_date < DATE '2026-01-01'"


# ── parse_table_fqn ───────────────────────────────────────────────────────────

def test_parse_fqn_valid():
    catalog, db, table = parse_table_fqn("glue_catalog.finance_db.finance_staging")
    assert catalog == "glue_catalog"
    assert db      == "finance_db"
    assert table   == "finance_staging"


def test_parse_fqn_missing_catalog():
    with pytest.raises(ValueError, match="Invalid table FQN"):
        parse_table_fqn("finance_db.finance_staging")


def test_parse_fqn_too_many_parts():
    with pytest.raises(ValueError, match="Invalid table FQN"):
        parse_table_fqn("a.b.c.d")


def test_parse_fqn_empty():
    with pytest.raises(ValueError, match="Invalid table FQN"):
        parse_table_fqn("")


# ── build_archive_s3_prefix ───────────────────────────────────────────────────

def test_archive_prefix():
    result = build_archive_s3_prefix(
        domain="finance",
        table_name="finance_staging",
        partition_date=date(2026, 1, 15),
        archive_bucket="s3://my-archive-bucket",
    )
    assert result == "s3://my-archive-bucket/staging_archive/finance/finance_staging/partition_date=2026-01-15/"


def test_archive_prefix_trailing_slash_handled():
    result = build_archive_s3_prefix(
        domain="ers",
        table_name="ers_staging",
        partition_date=date(2026, 2, 1),
        archive_bucket="s3://my-archive-bucket/",  # trailing slash
    )
    # Should not double-slash
    assert "s3://my-archive-bucket//staging_archive" not in result
    assert result.startswith("s3://my-archive-bucket/staging_archive/")


# ── date_range ────────────────────────────────────────────────────────────────

def test_date_range_same_day():
    result = date_range(date(2026, 1, 1), date(2026, 1, 1))
    assert result == [date(2026, 1, 1)]


def test_date_range_three_days():
    result = date_range(date(2026, 1, 1), date(2026, 1, 3))
    assert result == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]


def test_date_range_is_inclusive():
    result = date_range(date(2026, 3, 29), date(2026, 4, 1))
    assert len(result) == 4
    assert result[0]  == date(2026, 3, 29)
    assert result[-1] == date(2026, 4, 1)
