"""FastAPI application factory for the localhost product UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from deeprhetor.web.routes import router
from deeprhetor.web.state import AppState

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


def create_app(
    *,
    projects_dir: Path | str | None = None,
) -> FastAPI:
    """Build the localhost DeepRhetor web application."""
    root = Path(projects_dir) if projects_dir else Path.cwd() / "projects"
    state = AppState(projects_dir=root)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.app_state = state
        app.state.templates = templates
        state.ensure_projects_dir()
        try:
            yield
        finally:
            for key in list(state.opened):
                await state.close(key)

    app = FastAPI(title="DeepRhetor", lifespan=lifespan)
    app.state.app_state = state
    app.state.templates = templates
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)
    return app


__all__ = ["TEMPLATES_DIR", "STATIC_DIR", "create_app"]
