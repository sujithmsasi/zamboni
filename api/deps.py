"""
Zamboni API -- shared FastAPI dependencies (contracts.md §6/§10 D5).

get_current_user() is the env-var auth stub -- the seam get_current_user()
FastAPI dependency is exactly where SSO/OIDC plugs in later (D5). No auth
libraries here by design.
"""
from __future__ import annotations

import os

from fastapi import Query

from config.settings import DRY_RUN_DEFAULT


def get_current_user() -> str:
    """Env-var user stub -- default 'local-dev'. OIDC seam for later."""
    return os.getenv("ZAMBONI_USER", "local-dev")


def get_dry_run_default() -> bool:
    return DRY_RUN_DEFAULT


class PageParams:
    """Pagination dependency: page (>=1), size (<=250)."""

    def __init__(
        self,
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=250),
    ):
        self.page = page
        self.size = size
        self.offset = (page - 1) * size
