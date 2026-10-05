"""FastAPI application factory."""

from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from .api import entries as entries_api
from .api import tasks as tasks_api
from .auth import require_api_key
from .config import get_config
from .db import Database
from .mcp_server import build_mcp, mcp_asgi_app
from .migrate import upgrade_to_head
from .services.errors import SebastianError
from .ui.routes import _Redirect
from .ui.routes import router as ui_router


def app_version() -> str:
    try:
        return version("sebastian")
    except PackageNotFoundError:  # pragma: no cover
        return "0.0.0"


def create_app(database_url: str | None = None) -> FastAPI:
    cfg = get_config()
    url = database_url or cfg.database_url
    if database_url is None:
        cfg.database_path.parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if cfg.auto_migrate:
            upgrade_to_head(url)
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Sebastian",
        version=app_version(),
        description="Personal tasks, reminders, logs and notes for the Hermes agent.",
        lifespan=lifespan,
    )
    app.state.db = Database(url)
    mcp = build_mcp(app.state.db)

    @app.exception_handler(SebastianError)
    async def _domain_error(_: Request, exc: SebastianError):
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message, **exc.extra}
        )

    @app.exception_handler(_Redirect)
    async def _redirect(_: Request, exc: _Redirect):
        return RedirectResponse(exc.url, status_code=303)

    @app.get("/health", tags=["ops"])
    def health(request: Request):
        with request.app.state.db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        return {"status": "ok", "version": app_version(), "schema": rev}

    api = [Depends(require_api_key)]
    app.include_router(tasks_api.router, prefix="/api/v1", dependencies=api)
    app.include_router(entries_api.router, prefix="/api/v1", dependencies=api)
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.session_secret or cfg.api_key or "dev-only-insecure",
        session_cookie="sebastian_session",
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=False,  # served on localhost / over an SSH tunnel; the Pi isn't TLS-terminating
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "ui" / "static")),
        name="static",
    )
    app.include_router(ui_router)
    # MCP over streamable HTTP at /mcp (bearer-protected); mounted last so API routes win.
    app.mount("/", mcp_asgi_app(mcp))
    return app
