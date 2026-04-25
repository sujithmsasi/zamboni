"""
Unit tests for engine/core/window_evaluator.py
Pure datetime logic — no AWS required.
"""
import json
import pytest
from datetime import datetime
import pytz

from engine.core.window_evaluator import (
    evaluate,
    EXECUTE,
    SKIP_OUTSIDE_WINDOW,
    SKIP_BLACKOUT,
    SKIP_WRONG_DAY,
)

LA = pytz.timezone("America/Los_Angeles")
UTC = pytz.utc


def _la(year, month, day, hour, minute=0):
    """Build a timezone-aware datetime in LA time."""
    return LA.localize(datetime(year, month, day, hour, minute))


# ── post_batch window ─────────────────────────────────────────────────────────

POST_BATCH_CONFIG = json.dumps({
    "type": "post_batch",
    "timezone": "America/Los_Angeles",
    "delay_minutes": 30,
    "duration_hours": 4,
    "blackout_hours": [6, 7, 8, 9, 18, 19, 20, 21],
})


def test_post_batch_inside_window():
    # 01:00 LA — window opens at 00:30, closes at 04:30
    assert evaluate(POST_BATCH_CONFIG, now=_la(2026, 4, 1, 1, 0)) == EXECUTE


def test_post_batch_at_open():
    # Exactly at 00:30
    assert evaluate(POST_BATCH_CONFIG, now=_la(2026, 4, 1, 0, 30)) == EXECUTE


def test_post_batch_before_window():
    # 00:15 — before delay
    assert evaluate(POST_BATCH_CONFIG, now=_la(2026, 4, 1, 0, 15)) == SKIP_OUTSIDE_WINDOW


def test_post_batch_after_window():
    # 05:00 — after window closes at 04:30
    assert evaluate(POST_BATCH_CONFIG, now=_la(2026, 4, 1, 5, 0)) == SKIP_OUTSIDE_WINDOW


def test_post_batch_blackout_hour():
    # 07:00 — inside blackout
    assert evaluate(POST_BATCH_CONFIG, now=_la(2026, 4, 1, 7, 0)) == SKIP_BLACKOUT


def test_post_batch_force_override():
    # Blackout hour, but force=True
    assert evaluate(POST_BATCH_CONFIG, now=_la(2026, 4, 1, 7, 0), force=True) == EXECUTE


# ── scheduled window ──────────────────────────────────────────────────────────

SCHEDULED_CONFIG = json.dumps({
    "type": "scheduled",
    "timezone": "America/Los_Angeles",
    "days": ["saturday"],
    "start_time": "02:00",
    "duration_hours": 6,
})


def test_scheduled_correct_day_and_time():
    # Saturday 03:00 LA
    saturday = _la(2026, 3, 28, 3, 0)  # 28 March 2026 is a Saturday
    assert evaluate(SCHEDULED_CONFIG, now=saturday) == EXECUTE


def test_scheduled_correct_day_before_window():
    # Saturday 01:00 — before 02:00 start
    saturday_early = _la(2026, 3, 28, 1, 0)
    assert evaluate(SCHEDULED_CONFIG, now=saturday_early) == SKIP_OUTSIDE_WINDOW


def test_scheduled_correct_day_after_window():
    # Saturday 09:00 — after 08:00 close
    saturday_late = _la(2026, 3, 28, 9, 0)
    assert evaluate(SCHEDULED_CONFIG, now=saturday_late) == SKIP_OUTSIDE_WINDOW


def test_scheduled_wrong_day():
    # Friday — not in allowed days
    friday = _la(2026, 3, 27, 3, 0)
    assert evaluate(SCHEDULED_CONFIG, now=friday) == SKIP_WRONG_DAY


def test_scheduled_sunday_not_allowed():
    sunday = _la(2026, 3, 29, 3, 0)
    assert evaluate(SCHEDULED_CONFIG, now=sunday) == SKIP_WRONG_DAY


# ── edge cases ────────────────────────────────────────────────────────────────

def test_empty_config_defaults_to_execute():
    assert evaluate("") == EXECUTE


def test_invalid_json_defaults_to_execute():
    assert evaluate("{not valid json}") == EXECUTE


def test_none_now_uses_real_time():
    # Should not raise — just run with current time
    result = evaluate(POST_BATCH_CONFIG, now=None)
    assert result in (EXECUTE, SKIP_OUTSIDE_WINDOW, SKIP_BLACKOUT, SKIP_WRONG_DAY)


def test_multi_day_scheduled():
    config = json.dumps({
        "type": "scheduled",
        "timezone": "America/Los_Angeles",
        "days": ["saturday", "sunday"],
        "start_time": "02:00",
        "duration_hours": 4,
    })
    saturday = _la(2026, 3, 28, 3, 0)
    sunday   = _la(2026, 3, 29, 3, 0)
    assert evaluate(config, now=saturday) == EXECUTE
    assert evaluate(config, now=sunday)   == EXECUTE


def test_unknown_timezone_falls_back_to_utc():
    config = json.dumps({
        "type": "scheduled",
        "timezone": "Invalid/Timezone",
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
        "start_time": "00:00",
        "duration_hours": 23,
    })
    # Should not raise — should return a valid result
    result = evaluate(config)
    assert result in (EXECUTE, SKIP_OUTSIDE_WINDOW, SKIP_BLACKOUT, SKIP_WRONG_DAY)
