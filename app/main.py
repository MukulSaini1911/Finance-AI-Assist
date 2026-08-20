# =============================================================================
# app/main.py
#
# FastAPI application entry point.
#
# Responsibilities:
#   • Create and configure the FastAPI app instance.
#   • Register startup / shutdown lifecycle hooks.
#   • Mount API routers.
#   • Configure CORS, middleware, and global exception handlers.
# =============================================================================

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.utils.logger import configure_logging, get_logger

# Configure logging before any other imports that might log.
configure_logging()

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan context manager (replaces deprecated on_event startup/shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Code before `yield` runs at startup; code after runs at shutdown.
    Use this to initialise expensive resources (search client, LLM client)
    and to gracefully close them.
    """
    logger.info(
        "application_startup",
        env=settings.app_env,
        log_level=settings.app_log_level,
        chat_deployment=settings.azure_openai_chat_deployment,
        search_index=settings.azure_search_index_name,
    )

    # Lazy imports to avoid circular dependency during module loading.
    from app.services.search_service import get_search_service
    from app.services.llm_service import get_llm_service

    # Warm up clients (validates credentials & connectivity at startup).
    await get_search_service().ping()
    get_llm_service()  # Instantiates and caches the LLM client

    logger.info("application_ready")
    yield

    # Shutdown
    logger.info("application_shutdown")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """
    Factory function — separates app creation from global state.
    Makes the app easily testable (import create_app, not the module-level `app`).
    """

    _app = FastAPI(
        title="OFA AI Assist — Finance Knowledge Assistant",
        description=(
            "RAG-powered finance assistant that answers user questions "
            "strictly from approved Knowledge Base documents."
        ),
        version="2.0.0",
        docs_url="/docs" if not settings.is_production else None,   # Hide Swagger in prod
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # -----------------------------------------------------------------------
    # CORS
    # In production, replace ["*"] with the exact Streamlit origin.
    # -----------------------------------------------------------------------
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [str(settings.streamlit_backend_url)],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -----------------------------------------------------------------------
    # Request timing middleware
    # -----------------------------------------------------------------------
    @_app.middleware("http")
    async def add_process_time_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        response.headers["X-Process-Time"] = f"{elapsed:.4f}s"
        return response

    # -----------------------------------------------------------------------
    # Global exception handler — never leak stack traces to the client
    # -----------------------------------------------------------------------
    @_app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred. Please try again later."},
        )

    # -----------------------------------------------------------------------
    # Routers
    # -----------------------------------------------------------------------
    from app.api.v1.chat import router as chat_router
    from app.api.v1.ingestion import router as ingestion_router
    from app.api.v1.health import router as health_router

    _app.include_router(health_router, prefix="/api/v1", tags=["Health"])
    _app.include_router(chat_router, prefix="/api/v1", tags=["Chat"])
    _app.include_router(ingestion_router, prefix="/api/v1", tags=["Ingestion"])

    return _app


# Module-level app instance used by uvicorn / gunicorn
app: FastAPI = create_app()
