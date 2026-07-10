"""
Unit tests for compaction strategy SQL/param builders and dynamic router.
No AWS required.
"""
from unittest.mock import MagicMock

import pytest

from engine.operations.dynamic_router import route
from engine.strategies.binpack import build_optimize_sql, estimate_output_files
from engine.strategies.sort import build_glue_params as sort_params
from engine.strategies.sort import recommend_num_workers as sort_workers
from engine.strategies.zorder import build_glue_params as zorder_params
from engine.strategies.zorder import recommend_num_workers as zorder_workers

# ── binpack SQL builder ───────────────────────────────────────────────────────

def test_binpack_sql_basic():
    sql = build_optimize_sql("glue_catalog.finance_db.finance_staging")
    assert sql.startswith("OPTIMIZE finance_db.finance_staging ")
    assert "OPTIMIZE TABLE" not in sql
    assert "glue_catalog" not in sql
    assert "REWRITE DATA" in sql
    assert "BIN_PACK" in sql


def test_binpack_sql_no_inline_size_clause():
    # Athena's OPTIMIZE takes no WITH/options clause -- target size is set
    # via TBLPROPERTIES ahead of time (property_sync.py), not inline here.
    sql = build_optimize_sql("glue_catalog.finance_db.finance_staging", target_file_size_mb=256)
    assert "WITH" not in sql
    assert "256MB" not in sql
    assert "file_size_limit" not in sql


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


# ── Glue job polling timeout (2026-07-09 audit) ───────────────────────────────
# _wait_for_glue_job() used to be a bare `while True` with no timeout at
# all -- a stuck Glue job would hang the calling worker thread forever.

def test_wait_for_glue_job_times_out_on_stuck_job():
    from engine.operations.compaction import _wait_for_glue_job

    glue = MagicMock()
    glue.get_job_run.return_value = {"JobRun": {"JobRunState": "RUNNING"}}

    with pytest.raises(RuntimeError, match="timed out"):
        _wait_for_glue_job(glue, "run-stuck", poll_interval=0, timeout_s=0)

    glue.batch_stop_job_run.assert_called_once_with(
        JobName="zamboni-compaction", JobRunIds=["run-stuck"]
    )


def test_wait_for_glue_job_succeeds_before_timeout():
    from engine.operations.compaction import _wait_for_glue_job

    glue = MagicMock()
    glue.get_job_run.return_value = {"JobRun": {"JobRunState": "SUCCEEDED"}}

    _wait_for_glue_job(glue, "run-ok", poll_interval=0, timeout_s=300)  # must not raise
    glue.batch_stop_job_run.assert_not_called()


def test_wait_for_glue_job_still_raises_on_failed_state():
    from engine.operations.compaction import _wait_for_glue_job

    glue = MagicMock()
    glue.get_job_run.return_value = {
        "JobRun": {"JobRunState": "FAILED", "ErrorMessage": "OOM"}
    }

    with pytest.raises(RuntimeError, match="FAILED"):
        _wait_for_glue_job(glue, "run-failed", poll_interval=0, timeout_s=300)
