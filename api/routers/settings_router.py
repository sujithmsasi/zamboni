from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from api.deps import PageParams, get_current_user
from api.models import EscalationUpsertRequest, MutationResult, SettingsUpdateRequest, envelope
from api.services import settings_svc

router = APIRouter(tags=["settings"])


@router.get("/api/settings")
def get_settings():
    return envelope(settings_svc.get_settings())


@router.put("/api/settings")
def update_settings(req: SettingsUpdateRequest, actor: str = Depends(get_current_user)):
    audit_id = settings_svc.update_settings(req.settings, actor=actor, dry_run=req.dry_run)
    return envelope(MutationResult(success=True, dry_run=req.dry_run, audit_id=audit_id).model_dump())


@router.get("/api/escalation")
def list_escalation():
    return envelope(settings_svc.get_escalation_matrix())


@router.post("/api/escalation")
def create_escalation(req: EscalationUpsertRequest, actor: str = Depends(get_current_user)):
    ok = settings_svc.upsert_escalation(req.key, req.entry, actor=actor, dry_run=req.dry_run)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=str(uuid.uuid4())).model_dump())


@router.put("/api/escalation/{key}")
def update_escalation(key: str, req: EscalationUpsertRequest, actor: str = Depends(get_current_user)):
    ok = settings_svc.upsert_escalation(key, req.entry, actor=actor, dry_run=req.dry_run)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=str(uuid.uuid4())).model_dump())


@router.delete("/api/escalation/{key}")
def delete_escalation(key: str, dry_run: bool = True, actor: str = Depends(get_current_user)):
    ok = settings_svc.delete_escalation(key, actor=actor, dry_run=dry_run)
    return envelope(MutationResult(success=ok, dry_run=dry_run, audit_id=str(uuid.uuid4())).model_dump())


@router.get("/api/audit")
def list_audit(
    page_params: PageParams = Depends(),
    actor: str | None = None, action: str | None = None,
    from_: str | None = Query(None, alias="from"), to: str | None = None,
):
    rows, total = settings_svc.list_audit(page_params.page, page_params.size, actor=actor, action=action, from_date=from_, to_date=to)
    return envelope(rows, pagination={"page": page_params.page, "size": page_params.size, "total": total})
