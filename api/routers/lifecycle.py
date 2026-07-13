from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import PageParams, get_current_user
from api.models import MutationResult, NonprodClaimRequest, NonprodExemptRequest, envelope
from api.services import lifecycle_svc
from engine.core.audit import AuditAction, AuditEvent, audit

router = APIRouter(tags=["lifecycle"])


@router.get("/api/lifecycle/config")
def get_config():
    return envelope(lifecycle_svc.get_config())


@router.get("/api/nonprod")
def list_nonprod(page_params: PageParams = Depends(), env: str | None = None, state: str | None = None):
    rows, total = lifecycle_svc.list_nonprod(env, state, page_params.page, page_params.size)
    return envelope(rows, pagination={"page": page_params.page, "size": page_params.size, "total": total})


@router.post("/api/nonprod/exempt")
def exempt(req: NonprodExemptRequest, actor: str = Depends(get_current_user)):
    count = lifecycle_svc.exempt(req.fqns, req.reason, actor, dry_run=req.dry_run)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.LIFECYCLE_EXEMPTION, page_source="api",
        target_type="table", target_id=",".join(req.fqns),
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS", reason=req.reason,
    )
    audit(event)
    return envelope({"affected": count, **MutationResult(success=count > 0, dry_run=req.dry_run, audit_id=event.audit_id).model_dump()})


@router.post("/api/nonprod/claim")
def claim(req: NonprodClaimRequest, actor: str = Depends(get_current_user)):
    count = lifecycle_svc.claim(req.fqns, req.reason, actor, dry_run=req.dry_run)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.CLAIM_TABLE, page_source="api",
        target_type="table", target_id=",".join(req.fqns),
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS", reason=req.reason,
        after_value=f"owner={actor}",
    )
    audit(event)
    return envelope({"affected": count, **MutationResult(success=count > 0, dry_run=req.dry_run, audit_id=event.audit_id).model_dump()})


@router.get("/api/nonprod/deletions")
def list_deletions(page_params: PageParams = Depends(), env: str | None = None):
    rows, total = lifecycle_svc.list_deletions(env, page_params.page, page_params.size)
    return envelope(rows, pagination={"page": page_params.page, "size": page_params.size, "total": total})
