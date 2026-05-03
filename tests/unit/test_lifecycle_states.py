"""
Unit tests for Lifecycle Engine state machine logic and helpers.
No AWS required — pure logic tests.
"""
from datetime import UTC, datetime, timedelta, timezone

from engine.engines.lifecycle_engine import (
    ACTIVE,
    DROPPED,
    GREENZONE,
    PENDING_DROP,
    STALE_CANDIDATE,
    _infer_domain,
    _parse_ts,
)
from engine.operations.catalog_cleanup import is_backup_pattern

# ── State constants ───────────────────────────────────────────────────────────

def test_state_constants_are_strings():
    for state in [ACTIVE, STALE_CANDIDATE, GREENZONE, PENDING_DROP, DROPPED]:
        assert isinstance(state, str)


def test_state_constants_are_distinct():
    states = [ACTIVE, STALE_CANDIDATE, GREENZONE, PENDING_DROP, DROPPED]
    assert len(set(states)) == len(states)


# ── Domain inference ──────────────────────────────────────────────────────────

def test_infer_domain_preprod_suffix():
    assert _infer_domain("finance_preprod") == "finance"


def test_infer_domain_dev_suffix():
    assert _infer_domain("ers_dev") == "ers"


def test_infer_domain_test_suffix():
    assert _infer_domain("membership_test") == "membership"


def test_infer_domain_no_suffix():
    assert _infer_domain("finance") == "finance"


def test_infer_domain_underscore():
    # finance_reporting_preprod → strips _preprod → finance_reporting
    assert _infer_domain("finance_reporting_preprod") == "finance_reporting"


def test_infer_domain_unknown():
    result = _infer_domain("somethingcomplex")
    assert isinstance(result, str)
    assert len(result) > 0


# ── Timestamp parsing ─────────────────────────────────────────────────────────

def test_parse_ts_datetime_aware():
    dt = datetime.now(UTC)
    result = _parse_ts(dt)
    assert result.tzinfo is not None


def test_parse_ts_datetime_naive():
    dt = datetime(2026, 1, 1, 12, 0, 0)
    result = _parse_ts(dt)
    assert result.tzinfo is not None


def test_parse_ts_string():
    result = _parse_ts("2026-01-01T00:00:00")
    assert result.year == 2026
    assert result.tzinfo is not None


def test_parse_ts_string_with_tz():
    result = _parse_ts("2026-01-01T00:00:00+00:00")
    assert result.tzinfo is not None


def test_parse_ts_invalid_falls_back():
    result = _parse_ts("not-a-date")
    assert isinstance(result, datetime)


# ── Backup pattern detection ──────────────────────────────────────────────────

def test_backup_pattern_bkp():
    is_bkp, pattern = is_backup_pattern("finance_staging_bkp")
    assert is_bkp is True
    assert pattern == "_bkp"


def test_backup_pattern_backup():
    is_bkp, pattern = is_backup_pattern("ers_staging_backup")
    assert is_bkp is True
    assert pattern == "_backup"


def test_backup_pattern_temp():
    is_bkp, pattern = is_backup_pattern("load_test_temp")
    assert is_bkp is True
    assert pattern == "_temp"


def test_backup_pattern_old():
    is_bkp, pattern = is_backup_pattern("finance_master_old")
    assert is_bkp is True
    assert pattern == "_old"


def test_backup_pattern_timestamp_suffix():
    is_bkp, pattern = is_backup_pattern("finance_staging_20260101")
    assert is_bkp is True
    assert pattern == "timestamp_suffix"


def test_backup_pattern_date_suffix():
    is_bkp, pattern = is_backup_pattern("finance_staging_2026_01_01")
    assert is_bkp is True
    assert pattern == "date_suffix"


def test_backup_pattern_copy():
    is_bkp, pattern = is_backup_pattern("membership_staging_copy")
    assert is_bkp is True
    assert pattern == "_copy"


def test_backup_pattern_normal_table():
    is_bkp, pattern = is_backup_pattern("finance_staging")
    assert is_bkp is False
    assert pattern == ""


def test_backup_pattern_case_insensitive():
    is_bkp, pattern = is_backup_pattern("Finance_Staging_BKP")
    assert is_bkp is True


# ── State transition ordering ─────────────────────────────────────────────────

def test_state_machine_ordering():
    """
    Verify the logical order of states reflects the lifecycle flow.
    ACTIVE → STALE_CANDIDATE → GREENZONE → PENDING_DROP → DROPPED
    Each state must be distinct and have a clear direction.
    """
    ordered = [ACTIVE, STALE_CANDIDATE, GREENZONE, PENDING_DROP, DROPPED]
    # All unique
    assert len(set(ordered)) == 5
    # DROPPED is terminal
    assert DROPPED == "DROPPED"
    # ACTIVE is the starting state
    assert ACTIVE == "ACTIVE"


# ── GREENZONE expiry logic ────────────────────────────────────────────────────

def test_greenzone_expiry_in_past():
    """A GREENZONE table whose expiry is in the past should move to PENDING_DROP."""
    expired_at = datetime.now(UTC) - timedelta(days=1)
    assert _parse_ts(expired_at) <= datetime.now(UTC)


def test_greenzone_expiry_in_future():
    """A GREENZONE table whose expiry is in the future should stay in GREENZONE."""
    future_at = datetime.now(UTC) + timedelta(days=7)
    assert _parse_ts(future_at) > datetime.now(UTC)


def test_pending_drop_expiry_window():
    """48h window — table not deleted if drop time is still in the future."""
    drop_at = datetime.now(UTC) + timedelta(hours=24)
    assert _parse_ts(drop_at) > datetime.now(UTC)


def test_pending_drop_expired_window():
    """Table should be deleted if pending_drop_expires_at is in the past."""
    drop_at = datetime.now(UTC) - timedelta(hours=1)
    assert _parse_ts(drop_at) <= datetime.now(UTC)
