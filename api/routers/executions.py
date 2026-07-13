from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import PageParams
from api.models import envelope
from api.services import executions_svc

router = APIRouter(tags=["executions"])


@router.get("/api/executions")
def list_executions(
    page_params: PageParams = Depends(),
    fqn: str | None = None, engine: str | None = None, status: str | None = None,
    integrity_status: str | None = None,
    from_: str | None = Query(None, alias="from"), to: str | None = None,
):
    rows, total = executions_svc.list_executions(
        page_params.page, page_params.size, fqn=fqn, engine=engine, status=status,
        integrity_status=integrity_status, from_date=from_, to_date=to,
    )
    return envelope(rows, pagination={"page": page_params.page, "size": page_params.size, "total": total})


@router.get("/api/executions/{id}")
def get_execution(id: str):
    row = executions_svc.get_execution(id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Execution '{id}' not found.")
    return envelope(row)


@router.get("/api/dryrun/{fqn:path}")
def dry_run(fqn: str):
    row = executions_svc.dry_run_view(fqn)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Table '{fqn}' is not registered.")
    return envelope(row)


@router.get("/api/health/kpis")
def health_kpis(env: str = "prod", domain: str | None = None):
    return envelope(executions_svc.health_kpis(env=env, domain=domain))


@router.get("/api/costs")
def costs(group_by: str = "domain", from_: int = Query(30, alias="from"), to: str | None = None):
    return envelope(executions_svc.costs(group_by=group_by, from_days=from_))


@router.get("/api/stale")
def stale(
    kind: str = "hk", domain: str | None = None, days: int = 30, threshold: int = 0,
    environment: str | None = None, prefix: str | None = None,
):
    try:
        rows = executions_svc.stale(
            kind, domain=domain, days=days, threshold=threshold, environment=environment, prefix=prefix,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return envelope(rows)
