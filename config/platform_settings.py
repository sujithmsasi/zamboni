"""
Zamboni -- Platform Settings Accessor
Reads and writes config/zamboni_settings.json.

All platform-wide configuration (retention, dry-run defaults, budget alerts,
escalation matrix, approval flags) goes through here. Pages and engine code
must never read zamboni_settings.json directly.

Thread safety: get_settings() re-reads the file on every call in production
to pick up changes made via the Settings page without restarting Streamlit.
For test mode, a cached in-memory dict is returned.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from engine.utils.logger import get_logger

log = get_logger(__name__)

_SETTINGS_PATH = Path(__file__).parent / "zamboni_settings.json"

# In-memory override for tests (set via inject_test_settings())
_TEST_OVERRIDE: dict | None = None


def get_settings() -> dict[str, Any]:
    """
    Return the current platform settings dict.
    Re-reads from disk on every call (cheap JSON parse, < 1ms).
    Returns safe defaults if file is missing or corrupt.
    """
    global _TEST_OVERRIDE
    if _TEST_OVERRIDE is not None:
        return dict(_TEST_OVERRIDE)

    _TEST = os.getenv("ZAMBONI_TEST_MODE", "false").lower() == "true"
    if _TEST:
        return _safe_defaults()

    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # Strip comment keys
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except FileNotFoundError:
        log.warning("platform_settings.file_not_found",
                    path=str(_SETTINGS_PATH))
        return _safe_defaults()
    except json.JSONDecodeError as e:
        log.error("platform_settings.json_parse_error",
                  path=str(_SETTINGS_PATH), error=str(e))
        return _safe_defaults()


def save_settings(settings: dict[str, Any]) -> bool:
    """
    Persist settings to disk.
    Returns True on success. Never raises.
    """
    global _TEST_OVERRIDE
    if os.getenv("ZAMBONI_TEST_MODE", "false").lower() == "true":
        _TEST_OVERRIDE = dict(settings)
        return True

    try:
        existing = {}
        if _SETTINGS_PATH.exists():
            with open(_SETTINGS_PATH, encoding="utf-8") as f:
                existing = json.load(f)
        # Preserve _comment key
        comment = existing.get("_comment", "")
        out = {}
        if comment:
            out["_comment"] = comment
        out.update(settings)
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        log.info("platform_settings.saved", path=str(_SETTINGS_PATH))
        return True
    except Exception as e:
        log.error("platform_settings.save_failed", error=str(e))
        return False


def get_setting(key: str, default: Any = None) -> Any:
    """Get a single setting value by key."""
    return get_settings().get(key, default)


def set_setting(
    key:     str,
    value:   Any,
    actor:   str,
    dry_run: bool = True,
) -> bool:
    """
    Update a single setting and audit the change.
    Returns True on success.
    """
    if dry_run:
        log.info("platform_settings.set.dry_run", key=key, value=value)
        return True

    settings   = get_settings()
    before_val = settings.get(key)
    settings[key] = value

    if not save_settings(settings):
        return False

    try:
        from engine.core.audit import AuditAction, audit_action
        audit_action(
            actor=actor, action_type=AuditAction.SETTINGS_CHANGE,
            page_source="11_Settings", target_type="setting",
            target_id=key, dry_run=False, status="SUCCESS",
            before_value=json.dumps(before_val, default=str),
            after_value=json.dumps(value, default=str),
        )
    except Exception:
        pass  # audit failure never blocks

    return True


def inject_test_settings(overrides: dict | None) -> None:
    """
    Inject test settings. Pass None to reset to file-read mode.
    Used exclusively in unit tests.
    """
    global _TEST_OVERRIDE
    _TEST_OVERRIDE = overrides


def _safe_defaults() -> dict[str, Any]:
    """Return minimal safe defaults when settings file is unavailable."""
    return {
        "execution_log_retention_days":          90,
        "audit_log_retention_days":              365,
        "default_dry_run_ramp_days":              14,
        "default_dry_run":                       {"prod": True, "preprod": True,
                                                  "dev": True, "test": True},
        "live_activity_refresh_interval_seconds": 30,
        "budget_alert_threshold_usd_monthly":    0,
        "require_reason_in_preprod":             True,
        "require_reason_in_dev":                 False,
        "require_ticket_in_prod":                False,
        "approval_required_for":                 {},
        "backup_stale_name_patterns":            ["_bkp", "_backup", "_bak",
                                                  "_copy", "_temp", "_tmp", "_old"],
        "notification_defaults":                 {},
        "escalation_matrix":                     {},
        "teams_enabled":                          False,
        "teams_webhook_url":                      "",
        "cost_explorer_enabled":                  False,
        "cost_explorer_cache_hours":              24,
        "control_plane_sync_interval_seconds":         300,
        "control_plane_backup_interval_seconds":       300,
        "control_plane_backup_hourly_retention_hours": 24,
        "control_plane_backup_daily_retention_days":   30,
    }
