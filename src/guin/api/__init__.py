"""HTTP/WebSocket API (FastAPI, uvicorn)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from guin.api.routes import router as api_router
from guin.api.websocket import router as ws_router
from guin.core.config import GuinCLIConfig, apply_config_env


def create_app(*, config: GuinCLIConfig | None = None) -> FastAPI:
    """Create the GUIN web app with REST + WS + static frontend."""
    cfg = config or GuinCLIConfig.load()
    apply_config_env(cfg)

    app = FastAPI(title="GUIN Web Interface", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "guin"}

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": "error", "message": str(exc.detail)},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(exc)},
        )

    app.include_router(api_router)
    app.include_router(ws_router)

    frontend_dist = (
        Path(__file__).resolve().parents[3] / "frontend" / "dist"
    ).resolve()
    if frontend_dist.is_dir():
        assets_dir = frontend_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            target = frontend_dist / full_path
            if full_path and target.is_file():
                return FileResponse(target)
            return FileResponse(frontend_dist / "index.html")

    return app


__all__ = ["create_app"]
