"""
Zamboni API -- system service (contracts.md §6 routers/system.py).
"""
from __future__ import annotations

from api.deps import get_current_user
from config.settings import (
    APP_ENV,
    DRY_RUN_DEFAULT,
    GATE0_OVERRIDE_MAX_HOURS,
    STREAM_REGISTRY_TABLE,
    ZAMBONI_LOCAL_MODE,
    get_mode,
)
from engine.core.audit import AuditAction, AuditEvent, audit
from engine.core.lock_service import LockService
from engine.utils.athena_client import read_sql


def system_mode() -> dict:
    return {
        "mode": get_mode(),
        "app_env": APP_ENV,
        "dry_run_default": DRY_RUN_DEFAULT,
        "user": get_current_user(),
        # Phase 5a: Policy Configuration's Gate 0 override DatePicker needs
        # this cap client-side (contracts.md §6 gates router already
        # enforces it server-side; this just lets the UI disable dates
        # beyond the cap instead of round-tripping a 400).
        "gate0_override_max_hours": GATE0_OVERRIDE_MAX_HOURS,
    }


def system_health() -> dict:
    db_status = "ok"
    try:
        read_sql(f"SELECT 1 AS ok FROM {STREAM_REGISTRY_TABLE} LIMIT 1", workgroup="app")
    except Exception as e:
        db_status = f"error: {e}"

    return {
        "engine": "ok",
        "db": db_status,
        "aws": "n/a (local mode)" if ZAMBONI_LOCAL_MODE else "assumed reachable",
        "mode": get_mode(),
    }


def list_locks() -> list[dict]:
    return LockService().list_locks()


def release_lock(fqn: str, actor: str) -> bool:
    released = LockService().release_force(fqn)
    audit(AuditEvent(
        actor=actor, action_type=AuditAction.KILL_SWITCH,
        page_source="api", target_type="lock", target_id=fqn,
        dry_run=False, status="SUCCESS" if released else "FAILURE",
        reason="admin force-release via DELETE /api/locks/{fqn}",
    ))
    return released
