"""
Zamboni API -- FastAPI app (contracts.md §6 Phase 2).

Engine is called in-process (direct imports), never shelled. Streamlit stays
untouched and running on :8501; this app serves :8000.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.routers import controlm, domains, executions, gates, lifecycle, policies, settings_router, system, tables
from engine.utils.logger import get_logger

log = get_logger(__name__)

app = FastAPI(title="Zamboni API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _envelope_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"data": None, "pagination": None, "error": {"code": code, "message": message}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _envelope_error(exc.status_code, str(exc.status_code), str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _envelope_error(422, "422", str(exc.errors()))


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("api.unhandled_exception", path=str(request.url), error=str(exc))
    return _envelope_error(500, "500", str(exc))


for router in (
    tables.router, policies.router, gates.router, lifecycle.router,
    executions.router, controlm.router, settings_router.router, system.router,
    domains.router,
):
    app.include_router(router)


class _SPAStaticFiles(StaticFiles):
    """
    StaticFiles(html=True) serves real files/index.html for exact matches
    but 404s on unknown paths -- fine for asset requests, wrong for
    client-side routes (react-router paths like /health, /tables, ...).
    A direct GET to those 404s here even though clicking there from an
    already-loaded page works fine (pure client-side navigation, no
    server round-trip). Vite's dev server has this fallback built in by
    default, which is why the gap only shows up in prod-serve mode --
    found via the actual prod-serve check, not by inspection.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # starlette.staticfiles.StaticFiles.get_response() raises
            # Starlette's own HTTPException, not FastAPI's subclass of it --
            # `except HTTPException` (the fastapi one) silently never
            # matches a raised parent-class instance, so the fallback never
            # fired until this was narrowed to the actual raised type.
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


_UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"
if _UI_DIST.is_dir():
    app.mount("/", _SPAStaticFiles(directory=str(_UI_DIST), html=True), name="ui")
