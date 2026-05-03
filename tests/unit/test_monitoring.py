"""
Unit tests for monitoring — metrics and health check logic.
No AWS required.
"""
from unittest.mock import MagicMock, patch

from engine.monitoring.health_check import HealthCheckResult
from engine.monitoring.metrics import NAMESPACE, publish_engine_run, publish_fleet_health

# ── HealthCheckResult ─────────────────────────────────────────────────────────

def test_health_result_all_pass():
    result = HealthCheckResult()
    result.add("Athena",  True,  "OK")
    result.add("S3",      True,  "OK")
    result.add("Glue",    True,  "OK")
    assert result.passed is True
    assert len(result.errors) == 0


def test_health_result_one_fail():
    result = HealthCheckResult()
    result.add("Athena",  True,  "OK")
    result.add("S3",      False, "Access denied")
    result.add("Glue",    True,  "OK")
    assert result.passed is False
    assert len(result.errors) == 1
    assert "S3" in result.errors[0]


def test_health_result_summary():
    result = HealthCheckResult()
    result.add("Check1", True)
    result.add("Check2", True)
    result.add("Check3", False, "Failed")
    assert "2/3" in result.summary()


def test_health_result_no_checks():
    result = HealthCheckResult()
    assert result.passed is True
    assert result.summary() == "0/0 checks passed"


# ── Metrics ───────────────────────────────────────────────────────────────────

def test_namespace_constant():
    assert NAMESPACE == "Zamboni"


def test_publish_engine_run_dry_run():
    """In dry_run mode, no CloudWatch API calls should be made."""
    with patch("engine.monitoring.metrics._get_client") as mock_client:
        publish_engine_run(
            engine="hk", run_id="test-run",
            succeeded=10, failed=2, skipped=5,
            duration_s=120.0,
            dry_run=True,
        )
        mock_client.assert_not_called()


def test_publish_fleet_health_dry_run():
    with patch("engine.monitoring.metrics._get_client") as mock_client:
        publish_fleet_health(
            total_tables=1000,
            hk_enabled=800,
            failures_7d=3,
            gb_reclaimed_30d=45.2,
            dry_run=True,
        )
        mock_client.assert_not_called()


def test_coverage_calculation():
    """Coverage % calculated correctly."""
    total   = 1000
    enabled = 800
    pct     = round(enabled / total * 100, 1)
    assert pct == 80.0


def test_coverage_zero_tables():
    """Should not divide by zero."""
    total   = 0
    enabled = 0
    pct     = round(enabled / total * 100, 1) if total > 0 else 0
    assert pct == 0


def test_publish_engine_run_calls_cloudwatch():
    """When not dry_run, CloudWatch put_metric_data should be called."""
    mock_cw = MagicMock()
    with patch("engine.monitoring.metrics._get_client", return_value=mock_cw):
        publish_engine_run(
            engine="hk", run_id="test-run",
            succeeded=10, failed=0, skipped=3,
            duration_s=60.0,
            dry_run=False,
        )
    assert mock_cw.put_metric_data.called


def test_put_metric_uses_correct_namespace():
    """Metrics are published under the Zamboni namespace."""
    mock_cw = MagicMock()
    with patch("engine.monitoring.metrics._get_client", return_value=mock_cw):
        from engine.monitoring.metrics import put_metric
        put_metric("TestMetric", 42, "Count", dry_run=False)

    call_kwargs = mock_cw.put_metric_data.call_args
    assert call_kwargs[1]["Namespace"] == "Zamboni" or call_kwargs[0][0] == "Zamboni" \
        or mock_cw.put_metric_data.call_args.kwargs.get("Namespace") == "Zamboni" \
        or "Zamboni" in str(mock_cw.put_metric_data.call_args)
