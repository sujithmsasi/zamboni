"""
Zamboni -- AWS Cost Explorer Helper
Phase 2.1: real Athena billing data via Cost Explorer API.

Design decisions:
  - Controlled by cost_explorer_enabled setting. Falls back to
    bytes_scanned estimate when disabled or unavailable.
  - Results cached to S3 (24h TTL) to avoid $0.01/request charges.
  - If cost allocation tags are not set up, shows account-level
    Athena cost with a clear warning.
  - IAM requirement: ce:GetCostAndUsage on resource *.
  - Granularity: DAILY. Date range: last 30 days by default.

Tag strategy (recommended):
  Add tag  zamboni:managed=true  to the zamboni-* Athena workgroups.
  Then Cost Explorer can filter to Zamboni-specific spend.
  Without this tag, costs reflect ALL Athena in the account.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from config.settings import AWS_REGION, ZAMBONI_METADATA_BUCKET
from engine.utils.logger import get_logger

log = get_logger(__name__)

_CACHE_KEY_PREFIX = "cost_cache/cost_explorer"
_CACHE_TTL_HOURS  = 24

# Zamboni cost allocation tag — set this on zamboni-* Athena workgroups
_ZAMBONI_TAG_KEY   = "zamboni:managed"
_ZAMBONI_TAG_VALUE = "true"


def is_enabled() -> bool:
    """Return True if Cost Explorer integration is configured and enabled."""
    from config.platform_settings import get_settings
    return bool(get_settings().get("cost_explorer_enabled", False))


def get_athena_cost(
    days:        int  = 30,
    use_tag:     bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Return Athena cost data for the last N days.

    Returns a dict:
      {
        "source":       "cost_explorer" | "estimate" | "error",
        "total_usd":    float,
        "daily":        [{"date": "2026-04-01", "amount": 0.45}, ...],
        "tag_filtered": bool,   # True = Zamboni-only, False = all Athena
        "warning":      str | None,
        "period_start": str,
        "period_end":   str,
      }

    Falls back to estimate mode if Cost Explorer is disabled, unavailable,
    or caching fails.
    """
    if not is_enabled():
        return _estimate_fallback(days, reason="Cost Explorer disabled in settings")

    # Try cache first
    if not force_refresh:
        cached = _read_cache(days)
        if cached:
            log.debug("cost_explorer.cache_hit", days=days)
            return cached

    try:
        result = _fetch_from_ce(days, use_tag=use_tag)
        _write_cache(days, result)
        return result
    except Exception as e:
        log.error("cost_explorer.fetch_failed", error=str(e))
        return _estimate_fallback(
            days,
            reason=f"Cost Explorer API error: {str(e)[:100]}",
        )


def get_cost_summary_for_settings() -> dict[str, Any]:
    """
    Lightweight summary for the Settings page — last 7 days.
    Does not update cache.
    """
    if not is_enabled():
        return {"source": "disabled", "total_usd": None, "warning": None}
    cached = _read_cache(7)
    if cached:
        return {"source": "cache", "total_usd": cached.get("total_usd"),
                "warning": cached.get("warning")}
    return {"source": "uncached", "total_usd": None,
            "warning": "Click Refresh to load live cost data."}


# ── Cost Explorer API ─────────────────────────────────────────────────────────

def _fetch_from_ce(days: int, use_tag: bool) -> dict[str, Any]:
    """Call ce:GetCostAndUsage and return structured results."""
    import boto3

    client = boto3.client("ce", region_name="us-east-1")  # CE API is global

    end_dt   = datetime.now(UTC).date()
    start_dt = end_dt - timedelta(days=days)

    # Build filter
    filter_expr: dict[str, Any] = {
        "Dimensions": {
            "Key":    "SERVICE",
            "Values": ["Amazon Athena"],
        }
    }

    tag_filtered = False
    if use_tag:
        try:
            tag_filter = {
                "Tags": {
                    "Key":    _ZAMBONI_TAG_KEY,
                    "Values": [_ZAMBONI_TAG_VALUE],
                }
            }
            # Try with tag filter first — if no results, fall back to service only
            resp = client.get_cost_and_usage(
                TimePeriod={"Start": start_dt.isoformat(), "End": end_dt.isoformat()},
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
                Filter={"And": [filter_expr, tag_filter]},
            )
            total = _sum_results(resp)
            if total > 0:
                tag_filtered = True
                filter_expr = {"And": [filter_expr, tag_filter]}
            else:
                log.info("cost_explorer.tag_filter_returned_zero_falling_back")
        except Exception as te:
            log.warning("cost_explorer.tag_filter_failed", error=str(te))

    resp = client.get_cost_and_usage(
        TimePeriod={"Start": start_dt.isoformat(), "End": end_dt.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter=filter_expr,
    )

    daily  = _parse_daily(resp)
    total  = sum(d["amount"] for d in daily)
    warning = (
        None if tag_filtered else
        f"Showing all Athena cost for this AWS account (last {days}d). "
        f"Add tag {_ZAMBONI_TAG_KEY}={_ZAMBONI_TAG_VALUE} to zamboni-* "
        "workgroups for Zamboni-specific costs."
    )

    return {
        "source":       "cost_explorer",
        "total_usd":    round(total, 4),
        "daily":        daily,
        "tag_filtered": tag_filtered,
        "warning":      warning,
        "period_start": start_dt.isoformat(),
        "period_end":   end_dt.isoformat(),
        "fetched_at":   datetime.now(UTC).isoformat(),
    }


def _sum_results(resp: dict) -> float:
    total = 0.0
    for result in resp.get("ResultsByTime", []):
        for _metric, data in result.get("Total", {}).items():
            total += float(data.get("Amount", 0))
    return total


def _parse_daily(resp: dict) -> list[dict]:
    daily = []
    for result in resp.get("ResultsByTime", []):
        date_str = result["TimePeriod"]["Start"]
        amount   = 0.0
        for _metric, data in result.get("Total", {}).items():
            amount += float(data.get("Amount", 0))
        daily.append({"date": date_str, "amount": round(amount, 6)})
    return sorted(daily, key=lambda x: x["date"])


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_key(days: int) -> str:
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    bucket   = ZAMBONI_METADATA_BUCKET.rstrip("/")
    return f"{bucket}/{_CACHE_KEY_PREFIX}_{days}d_{date_str}.json"


def _read_cache(days: int) -> dict | None:
    """Read cached result from S3. Returns None on miss or expiry."""
    try:
        import boto3
        s3_uri = _cache_key(days)
        bucket, key = _parse_s3(s3_uri)
        s3   = boto3.client("s3", region_name=AWS_REGION)
        resp = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(resp["Body"].read().decode("utf-8"))
        # Validate TTL
        fetched = datetime.fromisoformat(data.get("fetched_at", "2000-01-01"))
        if fetched.tzinfo is None:
            from pytz import utc
            fetched = utc.localize(fetched)
        age_hours = (datetime.now(UTC) - fetched).total_seconds() / 3600
        if age_hours > _CACHE_TTL_HOURS:
            return None
        return data
    except Exception:
        return None


def _write_cache(days: int, data: dict) -> None:
    """Write result to S3 cache. Best-effort."""
    try:
        import boto3
        s3_uri = _cache_key(days)
        bucket, key = _parse_s3(s3_uri)
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.put_object(
            Bucket=bucket, Key=key,
            Body=json.dumps(data).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as e:
        log.warning("cost_explorer.cache_write_failed", error=str(e))


def _parse_s3(uri: str) -> tuple[str, str]:
    assert uri.startswith("s3://")
    rest = uri[5:]
    bucket, _, key = rest.partition("/")
    return bucket, key


# ── Estimate fallback ─────────────────────────────────────────────────────────

def _estimate_fallback(days: int, reason: str = "") -> dict[str, Any]:
    """
    Return a placeholder result using bytes_scanned estimate.
    Called when Cost Explorer is disabled or unavailable.
    """
    return {
        "source":       "estimate",
        "total_usd":    None,
        "daily":        [],
        "tag_filtered": False,
        "warning":      (
            reason or
            "Using bytes_scanned estimate ($5/TB). "
            "Enable Cost Explorer in Settings for real billing data."
        ),
        "period_start": (datetime.now(UTC).date()
                         - timedelta(days=days)).isoformat(),
        "period_end":   datetime.now(UTC).date().isoformat(),
        "fetched_at":   datetime.now(UTC).isoformat(),
    }
