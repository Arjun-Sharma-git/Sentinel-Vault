"""Sentinel Vault API.  Run: uv run uvicorn app.main:app --port 8010"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .api import admin_routes, auth_routes, chat_routes, library_routes
from .authz.guard import FreshnessGuard
from .authz.openfga import AuthzUnavailable
from .config import get_settings
from .db import dispose, session_factory
from .embeddings import EmbeddingError
from .ingest.library import scan_library
from .ingest.worker import work_forever
from .services import build_services

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    sessions = session_factory()
    services = await build_services(settings, sessions)
    app.state.services = services

    if settings.scan_on_startup:
        async with sessions() as db:
            await scan_library(db, services.vectors, settings.library_dir)
        await services.authz.sync()

    stop = asyncio.Event()
    worker = (
        asyncio.create_task(work_forever(sessions, services.indexer, stop)) if settings.run_worker else None
    )
    syncer = (
        asyncio.create_task(services.authz.keep_synced(stop))
        if isinstance(services.authz, FreshnessGuard)
        else None
    )
    logger.info(
        "ready: authz=%s gate=%s llm=%s embeddings=%s reranker=%s",
        services.authz.name,
        services.gate.name,
        settings.llm_model if services.llm else "extractive",
        services.embedder.name,
        services.retriever.reranker.name,
    )
    yield
    stop.set()
    for task in (worker, syncer):
        if task:
            await task
    await services.close()
    await dispose()


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Cache-Control", "no-store")
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Sentinel Vault",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.app_env == "production" else "/api/docs",
        openapi_url=None if settings.app_env == "production" else "/api/openapi.json",
    )
    app.add_middleware(SecurityHeaders)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Vault-Request"],
    )

    @app.exception_handler(AuthzUnavailable)
    async def authz_down(_: Request, exc: AuthzUnavailable) -> JSONResponse:
        logger.error("authorization unavailable: %s", exc)  # fail closed
        return JSONResponse({"detail": "Authorization service unavailable"}, status_code=503)

    @app.exception_handler(EmbeddingError)
    async def embeddings_down(_: Request, exc: EmbeddingError) -> JSONResponse:
        logger.error("embeddings unavailable: %s", exc)
        return JSONResponse({"detail": "Embedding model unavailable (is Ollama running?)"}, status_code=503)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        services = request.app.state.services
        return {
            "status": "ok",
            "authz": services.authz.name,
            "gate": services.gate.name,
            "llm": services.llm.model if services.llm else "extractive",
            "embeddings": services.embedder.name,
            "reranker": services.retriever.reranker.name,
            "vectors": await services.vectors.count(),
        }

    for module in (auth_routes, chat_routes, library_routes, admin_routes):
        app.include_router(module.router)
    return app


app = create_app()
