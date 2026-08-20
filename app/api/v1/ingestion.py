# =============================================================================
# app/api/v1/ingestion.py
#
# Ingestion endpoint — triggers SharePoint → Azure AI Search pipeline.
#
# Routes:
#   POST /api/v1/ingestion/run      → full re-index
#   POST /api/v1/ingestion/incremental → only new / changed documents
#
# Access control note:
#   In production, protect these endpoints with Azure AD Bearer token auth
#   (OAuth2 / MSAL).  A basic API-key guard is wired in here as a starting
#   point.
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from app.models.chat import IngestionResult
from app.services.ingestion_service import IngestionService, get_ingestion_service
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Simple API-key guard (replace with Azure AD in production)
# ---------------------------------------------------------------------------

def _require_ingestion_key(x_ingestion_key: str = Header(...)) -> None:
    """
    Validate a static API key supplied in the X-Ingestion-Key header.
    Replace this with proper RBAC / Azure AD token validation for production.
    """
    from app.config.settings import settings

    # In prod, store the key in Key Vault and load via SecretStr
    expected = settings.azure_search_api_key.get_secret_value()
    if x_ingestion_key != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid ingestion API key.",
        )


# ---------------------------------------------------------------------------
# Full ingestion
# ---------------------------------------------------------------------------

@router.post(
    "/ingestion/run",
    response_model=IngestionResult,
    summary="Full SharePoint ingestion",
    dependencies=[Depends(_require_ingestion_key)],
)
async def run_ingestion(
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> IngestionResult:
    """
    Download all documents from the configured SharePoint folder,
    chunk, embed, and upsert them into Azure AI Search.
    """
    logger.info("ingestion_full_run_triggered")
    result = await ingestion_service.run_full_ingestion()
    logger.info("ingestion_full_run_complete", result=result.model_dump())
    return result


# ---------------------------------------------------------------------------
# Incremental ingestion
# ---------------------------------------------------------------------------

@router.post(
    "/ingestion/incremental",
    response_model=IngestionResult,
    summary="Incremental SharePoint ingestion",
    dependencies=[Depends(_require_ingestion_key)],
)
async def run_incremental_ingestion(
    background_tasks: BackgroundTasks,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> IngestionResult:
    """
    Process only documents that are new or have changed since the last run.
    Uses SHA-256 content hashing to detect changes without requiring
    SharePoint change notifications.
    """
    logger.info("ingestion_incremental_run_triggered")
    result = await ingestion_service.run_incremental_ingestion()
    logger.info("ingestion_incremental_run_complete", result=result.model_dump())
    return result
