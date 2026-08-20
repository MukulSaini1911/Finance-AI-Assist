from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.models.chat import ChatResponse, IngestionResult
from app.services.ingestion_service import get_ingestion_service
from app.services.rag_service import get_rag_service


def test_health_endpoint_reports_healthy(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["version"] == "2.0.0"


def test_chat_endpoint_uses_rag_response(api_client: TestClient) -> None:
    sid = uuid4()
    mock_rag = MagicMock()
    mock_rag.answer = AsyncMock(
        return_value=ChatResponse(
            session_id=sid,
            answer="Refer to GL month-end checklist for accrual workflow.",
            sources=[],
            tokens_used=55,
        )
    )

    api_client.app.dependency_overrides[get_rag_service] = lambda: mock_rag
    try:
        response = api_client.post(
            "/api/v1/chat",
            json={"session_id": str(sid), "message": "How to close GL month-end?"},
        )
    finally:
        api_client.app.dependency_overrides.pop(get_rag_service, None)

    assert response.status_code == 200
    assert response.json()["session_id"] == str(sid)


def test_chat_stream_returns_sse_events(api_client: TestClient) -> None:
    sid = uuid4()
    mock_rag = MagicMock()

    async def _stream(_request):
        yield {"token": "Check OTL submission cut-off."}
        yield {"done": True, "session_id": str(sid), "sources": []}

    mock_rag.answer_stream = _stream
    api_client.app.dependency_overrides[get_rag_service] = lambda: mock_rag

    try:
        with api_client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"session_id": str(sid), "message": "OTL deadline?"},
        ) as response:
            payload = "\n".join(response.iter_text())
            assert response.status_code == 200
    finally:
        api_client.app.dependency_overrides.pop(get_rag_service, None)

    assert "data:" in payload
    assert "done" in payload


def test_ingestion_endpoint_requires_key(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/ingestion/run")
    assert response.status_code in (403, 422)


def test_ingestion_run_returns_summary_with_valid_key(api_client: TestClient) -> None:
    expected = IngestionResult(
        total_files_found=4,
        files_processed=4,
        files_skipped=0,
        chunks_created=26,
        chunks_upserted=26,
        errors=[],
        duration_seconds=2.3,
    )

    mock_ingestion = AsyncMock()
    mock_ingestion.run_full_ingestion = AsyncMock(return_value=expected)
    api_client.app.dependency_overrides[get_ingestion_service] = lambda: mock_ingestion

    try:
        response = api_client.post(
            "/api/v1/ingestion/run",
            headers={"X-Ingestion-Key": settings.azure_search_api_key.get_secret_value()},
        )
    finally:
        api_client.app.dependency_overrides.pop(get_ingestion_service, None)

    assert response.status_code == 200
    assert response.json()["chunks_upserted"] == 26