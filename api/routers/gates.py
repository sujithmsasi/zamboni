from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import PageParams, get_current_user
from api.models import ConflictsRescanRequest, GatesUpdateRequest, MutationResult, envelope
from api.services import gates_svc
from engine.core.audit import AuditAction, AuditEvent, audit

router = APIRouter(tags=["gates"])


@router.get("/api/gates/{fqn:path}")
def get_gates(fqn: str):
    row = gates_svc.get_gates(fqn)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No gate config for '{fqn}'.")
    return envelope(row)


@router.put("/api/gates/{fqn:path}")
def update_gates(fqn: str, req: GatesUpdateRequest, actor: str = Depends(get_current_user)):
    fields = req.model_dump(exclude={"dry_run"})
    try:
        ok, override_set = gates_svc.update_gates(fqn, fields, actor=actor, dry_run=req.dry_run)
    except gates_svc.GatesValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    action = AuditAction.GATE0_OVERRIDE_SET if override_set else AuditAction.POLICY_CHANGE
    event = AuditEvent(
        actor=actor, action_type=action, page_source="api",
        target_type="table", target_id=fqn,
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
        reason=req.gate0_override_reason or "",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=event.audit_id).model_dump())


@router.get("/api/conflicts")
def get_conflicts(page_params: PageParams = Depends(), domain: str | None = None, export: str | None = None):
    export_all = export == "csv"
    result = gates_svc.get_conflicts(page_params.page, page_params.size, domain, export_all)
    return envelope(result["data"], pagination={"page": result["page"], "size": result["size"], "total": result["total"]})


@router.post("/api/conflicts/rescan")
def rescan_conflicts(req: ConflictsRescanRequest, actor: str = Depends(get_current_user)):
    result = gates_svc.rescan_conflicts(req.fqns)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.POLICY_CHANGE, page_source="api",
        target_type="fleet", target_id="conflicts_rescan",
        dry_run=False, status="SUCCESS",
        after_value=f"scanned={result.get('scanned')},conflicts={result.get('conflicts')}",
    )
    audit(event)
    return envelope({**result, "audit_id": event.audit_id})
