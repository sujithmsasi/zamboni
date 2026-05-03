"""
Zamboni — Window Evaluator
Evaluates window_config JSON to decide whether now is a safe time to run HK.
100% pure Python — no AWS calls. Fully unit testable.

Window config JSON format:
    {
        "type": "post_batch",           -- post_batch | scheduled
        "timezone": "America/Los_Angeles",
        "delay_minutes": 30,            -- post_batch only: wait N mins after batch
        "duration_hours": 4,            -- how long the window stays open
        "blackout_hours": [6,7,8,9],   -- hours of day to never run (local time)
        "days": ["saturday"],           -- scheduled only: which days
        "start_time": "02:00"           -- scheduled only: HH:MM start
    }
"""
from __future__ import annotations

import json
from datetime import datetime, time

import pytz

from engine.utils.logger import get_logger

log = get_logger(__name__)

# Return values
EXECUTE             = "EXECUTE"
SKIP_OUTSIDE_WINDOW = "SKIP_OUTSIDE_WINDOW"
SKIP_BLACKOUT       = "SKIP_BLACKOUT"
SKIP_WRONG_DAY      = "SKIP_WRONG_DAY"


def evaluate(
    window_config_json: str,
    now: datetime | None = None,
    force: bool = False,
) -> str:
    """
    Evaluate a window_config and return EXECUTE or a SKIP_* reason.

    Args:
        window_config_json: JSON string from hk_config.window_config
        now:                Current datetime (UTC). Defaults to datetime.utcnow().
                            Override in tests for deterministic results.
        force:              If True, always return EXECUTE (override window).

    Returns:
        EXECUTE | SKIP_OUTSIDE_WINDOW | SKIP_BLACKOUT | SKIP_WRONG_DAY
    """
    if force:
        log.info("window_evaluator.forced")
        return EXECUTE

    if not window_config_json:
        log.warning("window_evaluator.no_config_defaulting_to_execute")
        return EXECUTE

    try:
        config = json.loads(window_config_json)
    except json.JSONDecodeError as e:
        log.error("window_evaluator.invalid_json", error=str(e))
        return EXECUTE  # Fail open — don't block HK on bad config

    utc_now  = now or datetime.now(pytz.utc)
    tz_name  = config.get("timezone", "UTC")
    try:
        tz       = pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        log.warning("window_evaluator.unknown_timezone", tz=tz_name)
        tz = pytz.utc

    local_now = utc_now.astimezone(tz)

    # ── Blackout check (applies to all window types) ──────────────────────────
    blackout_hours = config.get("blackout_hours", [])
    if local_now.hour in blackout_hours:
        log.info(
            "window_evaluator.blackout",
            hour=local_now.hour,
            blackout_hours=blackout_hours,
        )
        return SKIP_BLACKOUT

    window_type = config.get("type", "post_batch")

    if window_type == "post_batch":
        return _evaluate_post_batch(config, local_now)
    elif window_type == "scheduled":
        return _evaluate_scheduled(config, local_now)
    else:
        log.warning("window_evaluator.unknown_type", window_type=window_type)
        return EXECUTE


def _evaluate_post_batch(config: dict, local_now: datetime) -> str:
    """
    Post-batch window: open for duration_hours after the standard batch end.
    Batch is assumed to end at midnight — the window opens after delay_minutes.
    """
    delay_minutes  = config.get("delay_minutes", 30)
    duration_hours = config.get("duration_hours", 4)

    # Window opens at 00:XX after delay, closes after duration_hours
    window_open  = time(hour=0, minute=delay_minutes % 60)
    window_close_hour   = (delay_minutes // 60) + duration_hours
    window_close_minute = delay_minutes % 60
    window_close = time(
        hour=min(window_close_hour, 23),
        minute=window_close_minute,
    )

    current_time = local_now.time().replace(second=0, microsecond=0)

    in_window = window_open <= current_time <= window_close

    log.info(
        "window_evaluator.post_batch",
        current_time=str(current_time),
        window_open=str(window_open),
        window_close=str(window_close),
        in_window=in_window,
    )

    return EXECUTE if in_window else SKIP_OUTSIDE_WINDOW


def _evaluate_scheduled(config: dict, local_now: datetime) -> str:
    """
    Scheduled window: runs on specific days at a specific time for duration_hours.
    """
    allowed_days   = [d.lower() for d in config.get("days", [])]
    start_time_str = config.get("start_time", "02:00")
    duration_hours = config.get("duration_hours", 4)

    current_day    = local_now.strftime("%A").lower()  # e.g. "saturday"
    current_time   = local_now.time().replace(second=0, microsecond=0)

    # Day check
    if allowed_days and current_day not in allowed_days:
        log.info(
            "window_evaluator.wrong_day",
            current_day=current_day,
            allowed_days=allowed_days,
        )
        return SKIP_WRONG_DAY

    # Time window check
    try:
        start_h, start_m = map(int, start_time_str.split(":"))
        window_open      = time(hour=start_h, minute=start_m)
        close_h          = start_h + duration_hours
        window_close     = time(hour=min(close_h, 23), minute=start_m)
    except (ValueError, TypeError):
        log.warning("window_evaluator.bad_start_time", start_time=start_time_str)
        return EXECUTE

    in_window = window_open <= current_time <= window_close

    log.info(
        "window_evaluator.scheduled",
        current_day=current_day,
        current_time=str(current_time),
        window_open=str(window_open),
        window_close=str(window_close),
        in_window=in_window,
    )

    return EXECUTE if in_window else SKIP_OUTSIDE_WINDOW


def parse_window_config(window_config_json: str) -> dict:
    """Parse and return window config dict. Returns {} on failure."""
    if not window_config_json:
        return {}
    try:
        return json.loads(window_config_json)
    except json.JSONDecodeError:
        return {}
