# =============================================================================
# app/api/v1/health.py
#
# Health-check endpoint consumed by:
#   • Kubernetes liveness / readiness probes
#   • Docker healthcheck
#   • Azure App Service health monitoring
# =============================================================================

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str = "2.0.0"


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health_check() -> HealthResponse:
    """Returns 200 OK while the application process is alive."""
    return HealthResponse(status="healthy", timestamp=datetime.utcnow())
