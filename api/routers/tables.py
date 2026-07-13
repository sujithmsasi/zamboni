from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from api.deps import PageParams, get_current_user
from api.models import BulkControlMRequest, MutationResult, RegisterTableRequest, UpdateTableRequest, envelope
from api.services import tables_svc
from engine.core.audit import AuditAction, AuditEvent, audit

router = APIRouter(tags=["tables"])


@router.get("/api/tables")
def list_tables(
    page_params: PageParams = Depends(),
    domain: str | None = None, layer: str | None = None,
    tier: str | None = None, env: str | None = None, search: str | None = None,
    database_name: str | None = None,
):
    rows, total = tables_svc.list_tables(
        page_params.page, page_params.size, domain=domain, layer=layer, tier=tier, env=env, search=search,
        database_name=database_name,
    )
    return envelope(rows, pagination={"page": page_params.page, "size": page_params.size, "total": total})


@router.post("/api/tables/register")
def register_table(req: RegisterTableRequest, actor: str = Depends(get_current_user)):
    result = tables_svc.register_table(req.model_dump(exclude={"dry_run"}), registered_by=actor, dry_run=req.dry_run)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.TABLE_REGISTER, page_source="api",
        target_type="table", target_id=req.table_fqn, domain=req.domain, environment=req.environment,
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
        after_value=f"template={result['template']}",
    )
    audit(event)
    return envelope({
        "template": result["template"],
        **MutationResult(success=result["success"], dry_run=req.dry_run, audit_id=event.audit_id).model_dump(),
    })


@router.post("/api/tables/bulk-controlm")
def bulk_controlm(req: BulkControlMRequest, actor: str = Depends(get_current_user)):
    count = tables_svc.bulk_controlm(req.filters, req.set_fields, dry_run=req.dry_run)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.HK_ENABLE, page_source="api",
        target_type="domain", target_id=str(req.filters.get("domain", "")),
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
        after_value=f"count={count}",
    )
    audit(event)
    return envelope({"affected": count, **MutationResult(success=count > 0, dry_run=req.dry_run, audit_id=event.audit_id).model_dump()})


@router.post("/api/tables/job-mapping/import")
async def import_job_mapping(file: UploadFile, dry_run: bool = True, actor: str = Depends(get_current_user)):
    content = await file.read()
    try:
        report = tables_svc.import_job_mapping(content, dry_run=dry_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    event = AuditEvent(
        actor=actor, action_type=AuditAction.HK_ENABLE, page_source="api",
        target_type="job_mapping", target_id=file.filename or "upload",
        dry_run=dry_run, status="DRY_RUN" if dry_run else "SUCCESS",
        after_value=f"rows={len(report)}",
    )
    audit(event)
    return envelope({"rows": report, "audit_id": event.audit_id})


@router.get("/api/tables/job-mapping/export")
def export_job_mapping():
    csv_data = tables_svc.export_job_mapping()
    return envelope({"csv": csv_data})


@router.get("/api/glue/databases")
def list_glue_databases():
    return envelope(tables_svc.list_glue_databases())


@router.get("/api/glue/tables/{db}")
def list_glue_tables(db: str, pattern: str | None = None, unregistered_only: bool = False):
    return envelope(tables_svc.list_glue_tables(db, pattern=pattern, unregistered_only=unregistered_only))


@router.post("/api/glue/rescan/databases")
def rescan_glue_databases():
    """Force-bypass the 24h Glue catalog cache for the database list."""
    return envelope(tables_svc.rescan_glue_databases())


@router.post("/api/glue/rescan/tables/{db}")
def rescan_glue_tables(db: str, pattern: str | None = None, unregistered_only: bool = False):
    """Force-bypass the 24h Glue catalog cache for one database's tables."""
    return envelope(tables_svc.rescan_glue_tables(db, pattern=pattern, unregistered_only=unregistered_only))


# NOTE: the {fqn:path} catch-all routes below MUST be registered last -- they
# would otherwise shadow the literal routes above (e.g. GET
# /api/tables/job-mapping/export) since FastAPI matches GET routes in
# registration order and {fqn:path} greedily matches any remaining segments.

@router.get("/api/tables/{fqn:path}")
def get_table(fqn: str):
    row = tables_svc.get_table(fqn)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Table '{fqn}' not found.")
    return envelope(row)


@router.put("/api/tables/{fqn:path}")
def update_table(fqn: str, req: UpdateTableRequest, actor: str = Depends(get_current_user)):
    fields = req.model_dump(exclude={"dry_run"})
    ok = tables_svc.update_table(fqn, fields, dry_run=req.dry_run)
    event = AuditEvent(
        actor=actor, action_type=AuditAction.TABLE_REGISTER, page_source="api",
        target_type="table", target_id=fqn,
        dry_run=req.dry_run, status="DRY_RUN" if req.dry_run else "SUCCESS",
    )
    audit(event)
    return envelope(MutationResult(success=ok, dry_run=req.dry_run, audit_id=event.audit_id).model_dump())
