from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_current_user
from api.models import envelope
from api.services import system_svc

router = APIRouter(tags=["system"])


@router.get("/api/system/mode")
def system_mode():
    return envelope(system_svc.system_mode())


@router.get("/api/system/health")
def system_health():
    return envelope(system_svc.system_health())


@router.get("/api/locks")
def list_locks():
    return envelope(system_svc.list_locks())


@router.delete("/api/locks/{fqn:path}")
def release_lock(fqn: str, actor: str = Depends(get_current_user)):
    released = system_svc.release_lock(fqn, actor)
    return envelope({"released": released})
