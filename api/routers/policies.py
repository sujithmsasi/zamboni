from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import PageParams, get_current_user
from api.models import (
    MutationResult,
    PolicyUpdateRequest,
    TemplateApplyRequest,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    envelope,
)
from api.services import policies_svc
from engine.core.audit import AuditAction, AuditEvent, audit

router = APIRouter(tags=["policies"])


@router.get("/api/policies")
def list_policies(
    page_params: PageParams = Depends(),
    domain: str | None = None, layer: str | None = None, tier: str | None = None, search: str | None = None,
):
    rows, total = policies_svc.list_policies(
        page_params.page, page_params.size, domain=domain, layer=layer, tier=tier, search=search,
    )
    return envelope(rows, pagination={"page": page_params.page, "size": page_params.size, "total": total})


@router.get("/api/policies/{fqn:path}")
def get_policy(fqn: str):
    row = policies_svc.get_policy(fqn)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No policy config for '{fqn}'.")
    return envelope(row)


@router.put("/api/policies/{fqn:path}")
def update_policy(fqn: str, req: PolicyUpdateRequest, actor: str = Depends(get_current_user)):
    fields = req.model_dump(exclude={"dry_run"})
    ok = policies_svc.update_policy(fqn, fields, dry_run=req.dry_run)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.POLICY_CHANGE, page_source="api",
        target_type="table", target_id=fqn,
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
        reason=req.override_notes or "",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=event.audit_id).model_dump())


@router.get("/api/templates")
def list_templates():
    return envelope(policies_svc.list_templates())


@router.put("/api/templates/{name}")
def update_template(name: str, req: TemplateUpdateRequest, actor: str = Depends(get_current_user)):
    try:
        ok = policies_svc.update_template(name, req.model_dump(exclude={"dry_run"}), dry_run=req.dry_run)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    event = AuditEvent(
        actor=actor, action_type=AuditAction.POLICY_CHANGE, page_source="api",
        target_type="template", target_id=name,
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=event.audit_id).model_dump())


@router.post("/api/templates")
def create_template(req: TemplateCreateRequest, actor: str = Depends(get_current_user)):
    try:
        ok = policies_svc.create_template(req.name, req.model_dump(exclude={"dry_run", "name"}), dry_run=req.dry_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    event = AuditEvent(
        actor=actor, action_type=AuditAction.POLICY_CHANGE, page_source="api",
        target_type="template", target_id=req.name.strip().upper(),
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
        after_value=f"new template: strategy={req.compaction_strategy}",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=event.audit_id).model_dump())


@router.delete("/api/templates/{name}")
def delete_template(name: str, dry_run: bool = True, actor: str = Depends(get_current_user)):
    try:
        ok = policies_svc.delete_template(name, dry_run=dry_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    event = AuditEvent(
        actor=actor, action_type=AuditAction.POLICY_CHANGE, page_source="api",
        target_type="template", target_id=name,
        dry_run=dry_run, status="DRY_RUN" if dry_run else "SUCCESS", after_value="DELETED",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=dry_run, audit_id=event.audit_id).model_dump())


@router.post("/api/templates/{name}/apply")
def apply_template(name: str, req: TemplateApplyRequest, actor: str = Depends(get_current_user)):
    count = policies_svc.apply_template_bulk(
        name, req.domain, req.layer, req.tier, dry_run=req.dry_run, skip_overridden=req.skip_overridden,
    )
    event = AuditEvent(
        actor=actor, action_type=AuditAction.BULK_TEMPLATE_APPLY, page_source="api",
        target_type="domain", target_id=f"{req.domain}/{req.layer}",
        domain=req.domain or "", dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
        after_value=f"template={name},count={count}",
    )
    audit(event)
    return envelope({"affected": count, **MutationResult(success=count > 0, dry_run=req.dry_run, audit_id=event.audit_id).model_dump()})
