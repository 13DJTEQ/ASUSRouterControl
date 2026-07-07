"""FastAPI web dashboard for ASUSRouterControl telemetry."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from asusroutercontrol.web.api import router as api_router

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(db_path: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    When called by uvicorn factory mode (no args), resolves db_path from
    ASUSROUTERCONTROL_DATA_DIR env var or falls back to ~/.asusroutercontrol.
    """
    if db_path is None:
        data_dir = Path(
            os.environ.get("ASUSROUTERCONTROL_DATA_DIR", "")
            or Path.home() / ".asusroutercontrol"
        )
        db_path = data_dir / "router.db"
    app = FastAPI(title="ASUSRouterControl Dashboard", version="0.1.0")
    app.state.db_path = db_path
    app.include_router(api_router, prefix="/api")
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    return app
