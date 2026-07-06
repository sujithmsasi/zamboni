"""
Zamboni API -- settings service (contracts.md §6 routers/settings_router.py).

Lifts the General/Enforcement/Escalation Matrix save patterns from
app/pages/11_Settings.py and the audit trail query from
app/pages/12_Audit_Log.py.
"""
from __future__ import annotations

import json
from datetime import date, datetime

from config.platform_settings import get_settings as _get_settings
from config.platform_settings import save_settings as _save_settings
from engine.core.audit import AuditAction, AuditEvent, audit, get_recent_events
from engine.core.escalation import delete_entry, list_matrix, upsert_entry


def get_settings() -> dict:
    return _get_settings()


def update_settings(new_settings: dict, actor: str, dry_run: bool) -> str:
    before = _get_settings()
    if not dry_run:
        _save_settings({**before, **new_settings})
    event = AuditEvent(
        actor=actor, action_type=AuditAction.SETTINGS_CHANGE,
        page_source="api", target_type="setting", target_id="general",
        dry_run=dry_run, status="DRY_RUN" if dry_run else "SUCCESS",
        before_value=json.dumps(before, default=str), after_value=json.dumps(new_settings, default=str),
    )
    audit(event)
    return event.audit_id


def get_escalation_matrix() -> list[dict]:
    return list_matrix()


def upsert_escalation(key: str, entry: dict, actor: str, dry_run: bool) -> bool:
    return upsert_entry(key, entry, actor, dry_run=dry_run)


def delete_escalation(key: str, actor: str, dry_run: bool) -> bool:
    return delete_entry(key, actor, dry_run=dry_run)


def list_audit(
    page: int, size: int, actor: str | None = None, action: str | None = None,
    from_date: str | None = None, to_date: str | None = None,
) -> tuple[list[dict], int]:
    days = 7
    if from_date:
        try:
            days = max((date.today() - datetime.fromisoformat(from_date).date()).days, 1)
        except ValueError:
            days = 7

    offset = max(page - 1, 0) * size
    df = get_recent_events(limit=offset + size, actor=actor, action_type=action, days=days)
    total = len(df)
    if to_date:
        df = df[df["timestamp"].astype(str) <= f"{to_date} 23:59:59"] if "timestamp" in df.columns else df
    page_df = df.iloc[offset:offset + size]
    return page_df.to_dict(orient="records"), total
