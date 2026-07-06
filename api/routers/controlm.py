from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from api.deps import get_current_user
from api.models import JobUpsertRequest, MutationResult, envelope
from api.services import controlm_svc
from engine.core.audit import AuditAction, AuditEvent, audit

router = APIRouter(tags=["controlm"])


@router.get("/api/jobs")
def list_jobs(search: str | None = None):
    return envelope(controlm_svc.list_jobs(search=search))


@router.post("/api/jobs")
def upsert_job(req: JobUpsertRequest, actor: str = Depends(get_current_user)):
    ok = controlm_svc.upsert_job(req.model_dump(), registered_by=actor)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.TABLE_REGISTER, page_source="api",
        target_type="controlm_job", target_id=req.job_name,
        dry_run=False, status="SUCCESS",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=False, audit_id=event.audit_id).model_dump())


@router.post("/api/jobs/import")
async def import_jobs(file: UploadFile, actor: str = Depends(get_current_user)):
    content = await file.read()
    try:
        result = controlm_svc.import_jobs(content, registered_by=actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    event = AuditEvent(
        actor=actor, action_type=AuditAction.TABLE_REGISTER, page_source="api",
        target_type="controlm_job", target_id=file.filename or "upload",
        dry_run=False, status="SUCCESS",
        after_value=f"imported={result['imported']},failed={result['failed']}",
    )
    audit(event)
    return envelope({**result, "audit_id": event.audit_id})


@router.delete("/api/jobs/{name}")
def delete_job(name: str, actor: str = Depends(get_current_user)):
    ok = controlm_svc.delete_job(name)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.TABLE_UNREGISTER, page_source="api",
        target_type="controlm_job", target_id=name,
        dry_run=False, status="SUCCESS" if ok else "FAILURE",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=False, audit_id=event.audit_id).model_dump())
