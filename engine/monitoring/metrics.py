"""
Zamboni — CloudWatch Metrics Publisher
Publishes custom metrics to CloudWatch namespace 'Zamboni'.
Called at the end of each engine run and from the Streamlit app health check.

Metrics published:
  HK/Archival/Lifecycle:
    - TablesProcessed, Succeeded, Failed, Skipped
    - SnapshotsExpired, OrphansDeleted, BytesRewritten
    - DurationSeconds
    - CircuitBreakerTrips

  Fleet health (published by monitoring cron):
    - TablesRegistered, HKEnabled, HKCoverage
    - TablesWithRecentFailures
"""
import boto3
from datetime import datetime, timezone
from typing import Optional

from config.settings import AWS_REGION
from engine.utils.logger import get_logger

log = get_logger(__name__)

NAMESPACE = "Zamboni"

_client: Optional[boto3.client] = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("cloudwatch", region_name=AWS_REGION)
    return _client


def put_metric(
    name:       str,
    value:      float,
    unit:       str = "Count",
    dimensions: Optional[list[dict]] = None,
    dry_run:    bool = False,
) -> None:
    """
    Publish a single custom metric to CloudWatch.

    Args:
        name:       Metric name
        value:      Metric value
        unit:       CloudWatch unit (Count, Bytes, Seconds, Percent, None)
        dimensions: List of {Name, Value} dicts for filtering
        dry_run:    Log only — do not publish
    """
    dims = dimensions or []

    if dry_run:
        log.info("metrics.dry_run", name=name, value=value, unit=unit, dimensions=dims)
        return

    try:
        _get_client().put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[{
                "MetricName": name,
                "Value":      float(value),
                "Unit":       unit,
                "Timestamp":  datetime.now(timezone.utc),
                "Dimensions": dims,
            }]
        )
        log.debug("metrics.published", name=name, value=value)
    except Exception as e:
        log.warning("metrics.publish_failed", name=name, error=str(e))


def publish_engine_run(
    engine:     str,
    run_id:     str,
    succeeded:  int,
    failed:     int,
    skipped:    int,
    duration_s: float,
    domain:     Optional[str] = None,
    dry_run:    bool = False,
    **extra_metrics,
) -> None:
    """
    Publish engine run summary metrics.
    Called at the end of every engine run.
    """
    dims = [{"Name": "Engine", "Value": engine}]
    if domain:
        dims.append({"Name": "Domain", "Value": domain})

    total = succeeded + failed + skipped
    metrics = [
        ("TablesProcessed",  total,      "Count"),
        ("Succeeded",        succeeded,  "Count"),
        ("Failed",           failed,     "Count"),
        ("Skipped",          skipped,    "Count"),
        ("DurationSeconds",  duration_s, "Seconds"),
    ]

    # Extra optional metrics from run result
    for key, unit in [
        ("snapshots_expired",    "Count"),
        ("orphan_files_deleted", "Count"),
        ("bytes_rewritten",      "Bytes"),
        ("bytes_archived",       "Bytes"),
        ("total_partitions",     "Count"),
    ]:
        if key in extra_metrics and extra_metrics[key] is not None:
            metric_name = "".join(w.capitalize() for w in key.split("_"))
            metrics.append((metric_name, extra_metrics[key], unit))

    for name, value, unit in metrics:
        put_metric(name, value, unit, dims, dry_run=dry_run)

    log.info(
        "metrics.engine_run_published",
        engine=engine,
        run_id=run_id,
        succeeded=succeeded,
        failed=failed,
    )


def publish_circuit_breaker_trip(table_fqn: str, domain: str, dry_run: bool = False) -> None:
    """Publish a metric when a circuit breaker trips."""
    put_metric(
        name="CircuitBreakerTrips",
        value=1,
        unit="Count",
        dimensions=[
            {"Name": "Engine", "Value": "hk"},
            {"Name": "Domain", "Value": domain},
        ],
        dry_run=dry_run,
    )


def publish_fleet_health(
    total_tables:    int,
    hk_enabled:      int,
    failures_7d:     int,
    gb_reclaimed_30d:float,
    dry_run:         bool = False,
) -> None:
    """
    Publish fleet-level health metrics.
    Called by the monitoring cron job (or Streamlit home snapshot generation).
    """
    coverage_pct = round(hk_enabled / total_tables * 100, 1) if total_tables > 0 else 0

    metrics = [
        ("TablesRegistered",       total_tables,      "Count"),
        ("HKEnabled",              hk_enabled,        "Count"),
        ("HKCoverage",             coverage_pct,      "Percent"),
        ("TablesWithRecentFailures", failures_7d,     "Count"),
        ("GBReclaimed30d",         gb_reclaimed_30d,  "None"),
    ]

    for name, value, unit in metrics:
        put_metric(name, float(value), unit, dry_run=dry_run)

    log.info(
        "metrics.fleet_health_published",
        total=total_tables,
        enabled=hk_enabled,
        coverage_pct=coverage_pct,
    )
