# =============================================================================
# app/utils/logger.py
#
# Structured logging using structlog.
#
# WHY structlog over stdlib logging?
#   • Every log line is a JSON object → directly ingestible by Azure Monitor,
#     Datadog, Splunk, or any log aggregator without extra parsing.
#   • Context variables (session_id, user_id) bind once and appear on every
#     subsequent log line in the same async context — zero boilerplate.
#   • In development, output is pretty-printed for human readability.
# =============================================================================

from __future__ import annotations

import logging
import sys

import structlog

from app.config.settings import settings


def configure_logging() -> None:
    """
    Configure structlog and stdlib logging once at application startup.
    Call this from app/main.py before anything else.
    """

    log_level = getattr(logging, settings.app_log_level.upper(), logging.INFO)

    # ------------------------------------------------------------------
    # 1. Configure stdlib root logger so that third-party libraries
    #    (uvicorn, httpx, azure-sdk) also emit structured output.
    # ------------------------------------------------------------------
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # ------------------------------------------------------------------
    # 2. Choose renderer based on environment.
    #    • Development → colourful, human-friendly console output.
    #    • Production  → JSON lines for log aggregators.
    # ------------------------------------------------------------------
    renderer: structlog.types.Processor
    if settings.is_production:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # ------------------------------------------------------------------
    # 3. Build the shared processor chain.
    # ------------------------------------------------------------------
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,          # Merge bound context vars
        structlog.stdlib.add_log_level,                   # Add "level" key
        structlog.processors.TimeStamper(fmt="iso"),      # ISO-8601 timestamp
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,             # Pretty exception chain
    ]

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__) -> structlog.BoundLogger:
    """
    Return a named structlog logger.

    Usage::

        from app.utils.logger import get_logger
        logger = get_logger(__name__)
        logger.info("document_indexed", filename="HR_Policy.pdf", chunks=42)
    """
    return structlog.get_logger(name)
