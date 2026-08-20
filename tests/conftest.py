from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import structlog

from app.models.chat import SourceDocument


def pytest_configure(config) -> None:  # noqa: ARG001
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    from unittest.mock import patch as _patch

    _patch("app.utils.logger.configure_logging", lambda: None).start()


@pytest.fixture
def knowledge_base_root() -> Path:
    return Path("Knowledge Base")


@pytest.fixture
def sample_source() -> SourceDocument:
    return SourceDocument(
        document_id="gl_month_end_chunk_0001",
        filename="GL-Month-End-Checklist.pdf",
        title="GL Month End Checklist",
        page_number=1,
        section="Closing Entries",
        url="https://contoso.sharepoint.com/gl-checklist.pdf",
        relevance_score=0.88,
        excerpt="Post accruals before final trial balance extraction.",
    )


@pytest.fixture
def mock_search_service(sample_source: SourceDocument) -> AsyncMock:
    service = AsyncMock()
    service.ping.return_value = None
    service.hybrid_search.return_value = [sample_source]
    service.upsert_documents.return_value = 1
    service.document_exists.return_value = False
    return service


@pytest.fixture
def mock_embedding_service() -> AsyncMock:
    service = AsyncMock()
    service.embed_query.return_value = [0.1] * 3072
    service.embed_documents.return_value = [[0.1] * 3072]
    return service


@pytest.fixture
def mock_llm_service() -> MagicMock:
    service = MagicMock()
    ai_message = MagicMock()
    ai_message.content = "The GL close requires accrual posting before finalization."
    ai_message.usage_metadata = {"total_tokens": 120}
    service.chat.ainvoke = AsyncMock(return_value=ai_message)
    service.streaming_chat.astream = AsyncMock(return_value=[])
    return service


@pytest.fixture
def api_client(mock_search_service, mock_llm_service, mock_embedding_service):
    with (
        patch("app.services.search_service.get_search_service", return_value=mock_search_service),
        patch("app.services.llm_service.get_llm_service", return_value=mock_llm_service),
        patch("app.services.embedding_service.get_embedding_service", return_value=mock_embedding_service),
        patch("app.services.rag_service.get_search_service", return_value=mock_search_service),
        patch("app.services.rag_service.get_llm_service", return_value=mock_llm_service),
        patch("app.services.rag_service.get_embedding_service", return_value=mock_embedding_service),
    ):
        from fastapi.testclient import TestClient
        from app.main import create_app

        app = create_app()
        with TestClient(app) as client:
            yield client
