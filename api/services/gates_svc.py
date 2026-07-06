"""
Zamboni API -- gates service (contracts.md §6 routers/gates.py, §4 Gate 0).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from config.settings import GATE0_OVERRIDE_MAX_HOURS, HK_CONFIG_TABLE
from engine.core import conflict_detector, governance
from engine.core.config import get_hk_config
from engine.utils.athena_client import run_query


def _esc(value: str) -> str:
    return str(value).replace("'", "''")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def get_gates(fqn: str) -> dict | None:
    cfg = get_hk_config(fqn)
    if cfg is None:
        return None
    conflict = conflict_detector.get_cached(fqn)
    return {
        "table_fqn": fqn,
        "gate1_enabled": bool(cfg.get("gate1_enabled", 0)),
        "gate2_enabled": bool(cfg.get("gate2_enabled", 1)),
        "gate3_enabled": bool(cfg.get("gate3_enabled", 1)),
        "gate0_override_until": cfg.get("gate0_override_until"),
        "gate0_override_reason": cfg.get("gate0_override_reason"),
        "gate0_override_by": cfg.get("gate0_override_by"),
        "conflict_cache": conflict,
    }


class GatesValidationError(ValueError):
    pass


def update_gates(fqn: str, fields: dict, actor: str, dry_run: bool) -> tuple[bool, bool]:
    """
    Returns (updated, override_set) -- override_set tells the router whether
    to audit GATE0_OVERRIDE_SET. Raises GatesValidationError on a missing
    reason when an override is being set, or a cap violation is silently
    clamped (contracts.md §6: "override capped at GATE0_OVERRIDE_MAX_HOURS").
    """
    sets = []
    override_set = False

    for key in ("gate1_enabled", "gate2_enabled", "gate3_enabled"):
        value = fields.get(key)
        if value is not None:
            sets.append(f"{key} = {1 if value else 0}")

    override_until = fields.get("gate0_override_until")
    if override_until is not None:
        reason = fields.get("gate0_override_reason")
        if not reason:
            raise GatesValidationError("gate0_override_reason is required when setting gate0_override_until.")
        try:
            requested = datetime.fromisoformat(str(override_until).replace("Z", "+00:00"))
        except ValueError as e:
            raise GatesValidationError(f"gate0_override_until is not a valid ISO datetime: {e}") from e
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=UTC)
        cap = datetime.now(UTC) + timedelta(hours=GATE0_OVERRIDE_MAX_HOURS)
        clamped = min(requested, cap)

        sets.append(f"gate0_override_until = '{clamped.strftime('%Y-%m-%d %H:%M:%S')}'")
        sets.append(f"gate0_override_reason = '{_esc(reason)}'")
        sets.append(f"gate0_override_by = '{_esc(actor)}'")
        override_set = True

    if not sets:
        return False, False

    sets.append(f"updated_at = '{_now()}'")
    sql = f"UPDATE {HK_CONFIG_TABLE} SET {', '.join(sets)} WHERE table_fqn = '{_esc(fqn)}'"
    run_query(sql, workgroup="app", dry_run=dry_run)
    return True, override_set


def get_conflicts(page: int, size: int, domain: str | None, export_all: bool) -> dict:
    return governance.dual_optimizer_report(page=page, size=size, domain=domain, export_all=export_all)


def rescan_conflicts(fqns: list[str] | None) -> dict:
    return conflict_detector.scan_fleet(fqns)
