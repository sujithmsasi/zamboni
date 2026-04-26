"""
Unit tests for compaction strategy SQL/param builders and dynamic router.
No AWS required.
"""
import pytest
from engine.strategies.binpack import build_optimize_sql, estimate_output_files
from engine.strategies.sort import build_glue_params as sort_params, recommend_num_workers as sort_workers
from engine.strategies.zorder import build_glue_params as zorder_params, recommend_num_workers as zorder_workers
from engine.operations.dynamic_router import route, WORKER_THRESHOLDS


# ── binpack SQL builder ───────────────────────────────────────────────────────

def test_binpack_sql_basic():
    sql = build_optimize_sql("glue_catalog.finance_db.finance_staging")
    assert "OPTIMIZE TABLE glue_catalog.finance_db.finance_staging" in sql
    assert "REWRITE DATA" in sql
    assert "BIN_PACK" in sql


def test_binpack_sql_target_size():
    sql = build_optimize_sql("glue_catalog.finance_db.finance_staging", target_file_size_mb=256)
    assert "256MB" in sql


def test_binpack_sql_with_partition_filter():
    sql = build_optimize_sql(
        "glue_catalog.finance_db.finance_staging",
        partition_filter="partition_date >= DATE '2026-03-01'"
    )
    assert "WHERE" in sql
    assert "partition_date >= DATE '2026-03-01'" in sql


def test_binpack_sql_no_partition_filter():
    sql = build_optimize_sql("glue_catalog.finance_db.finance_staging")
    assert "WHERE" not in sql


def test_estimate_output_files():
    # 10GB table with 128MB target → ~80 files
    assert estimate_output_files(10.0, 128) == 80


def test_estimate_output_files_zero():
    assert estimate_output_files(0, 128) == 0


# ── sort params builder ───────────────────────────────────────────────────────

def test_sort_params_basic():
    params = sort_params(
        table_fqn="glue_catalog.finance_db.finance_base",
        sort_columns=["effective_date", "member_id"],
    )
    assert params["--strategy"] == "sort"
    assert params["--sort_columns"] == "effective_date,member_id"
    assert params["--table_fqn"] == "glue_catalog.finance_db.finance_base"


def test_sort_params_execution_class():
    params = sort_params(
        table_fqn="glue_catalog.finance_db.finance_base",
        sort_columns=["effective_date"],
        execution_class="STANDARD",
    )
    assert params["--execution_class"] == "STANDARD"


def test_sort_params_partition_filter():
    params = sort_params(
        table_fqn="glue_catalog.finance_db.finance_base",
        sort_columns=["effective_date"],
        partition_filter="partition_date >= DATE '2026-01-01'",
    )
    assert "--partition_filter" in params


def test_sort_workers_small_table():
    assert sort_workers(5.0, "G.2X") >= 2


def test_sort_workers_large_table():
    workers = sort_workers(200.0, "G.2X")
    assert workers > 5
    assert workers <= 50


# ── zorder params builder ─────────────────────────────────────────────────────

def test_zorder_params_basic():
    params = zorder_params(
        table_fqn="glue_catalog.finance_db.finance_master",
        zorder_columns=["region", "product_code"],
    )
    assert params["--strategy"] == "zorder"
    assert params["--zorder_columns"] == "region,product_code"


def test_zorder_workers_more_than_sort():
    # Z-order needs more workers for same data size
    z = zorder_workers(50.0, "G.2X")
    s = sort_workers(50.0, "G.2X")
    assert z >= s


# ── dynamic router ────────────────────────────────────────────────────────────

def test_route_small_table():
    decision = route(tier="standard", total_size_gb=5.0, total_files=500)
    assert decision.worker_type == "G.1X"
    assert decision.execution_class == "FLEX"
    assert decision.num_workers >= 2


def test_route_medium_table():
    decision = route(tier="standard", total_size_gb=30.0, total_files=3000)
    assert decision.worker_type == "G.2X"
    assert decision.execution_class == "FLEX"


def test_route_large_table():
    decision = route(tier="standard", total_size_gb=200.0, total_files=20000)
    assert decision.worker_type == "G.4X"


def test_route_critical_tier_gets_standard():
    decision = route(tier="critical", total_size_gb=5.0, total_files=500)
    assert decision.execution_class == "STANDARD"


def test_route_standard_tier_gets_flex():
    decision = route(tier="standard", total_size_gb=5.0, total_files=500)
    assert decision.execution_class == "FLEX"


def test_route_low_tier_gets_flex():
    decision = route(tier="low", total_size_gb=5.0, total_files=500)
    assert decision.execution_class == "FLEX"


def test_route_file_count_overrides_size():
    # Small size but high file count → bigger worker
    decision = route(tier="standard", total_size_gb=5.0, total_files=8000)
    assert decision.worker_type in ("G.2X", "G.4X")


def test_route_reason_populated():
    decision = route(tier="critical", total_size_gb=15.0, total_files=1500)
    assert len(decision.reason) > 0
    assert "STANDARD" in decision.reason
