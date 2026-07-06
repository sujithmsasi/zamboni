"""
Zamboni API -- domains router.

> ADDED (Phase 4): see api/services/domains_svc.py for the REALITY note --
contracts.md §6 had no domains section; these 4 routes are net-new, added
for the DomainManagement page.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_user
from api.models import MutationResult, RegisterDomainRequest, UpdateDomainRequest, envelope
from api.services import domains_svc
from engine.core.audit import AuditAction, AuditEvent, audit

router = APIRouter(tags=["domains"])


@router.get("/api/domains")
def list_domains(active_only: bool = False):
    return envelope(domains_svc.list_domains(active_only=active_only))


@router.get("/api/domains/{name}")
def get_domain(name: str):
    row = domains_svc.get_domain(name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Domain '{name}' not found.")
    return envelope(row)


@router.post("/api/domains")
def create_domain(req: RegisterDomainRequest, actor: str = Depends(get_current_user)):
    fields = req.model_dump(exclude={"dry_run"})
    try:
        ok = domains_svc.create_domain(fields, actor=actor, dry_run=req.dry_run)
    except domains_svc.DomainValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    event = AuditEvent(
        actor=actor, action_type=AuditAction.DOMAIN_CREATE, page_source="api",
        target_type="domain", target_id=req.domain_name.strip().lower(),
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
        after_value=f"owner={req.owner_email},retention={req.hot_retention_days}",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=event.audit_id).model_dump())


@router.put("/api/domains/{name}")
def update_domain(name: str, req: UpdateDomainRequest, actor: str = Depends(get_current_user)):
    fields = req.model_dump(exclude={"dry_run"})
    try:
        ok = domains_svc.update_domain(name, fields, dry_run=req.dry_run)
    except domains_svc.DomainValidationError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    event = AuditEvent(
        actor=actor, action_type=AuditAction.DOMAIN_UPDATE, page_source="api",
        target_type="domain", target_id=name,
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
        reason=fields.get("notes") or "",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=event.audit_id).model_dump())
