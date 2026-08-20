from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.chat import ChatRequest, DocumentMetadata, IngestionResult, SourceDocument


def test_chat_request_rejects_whitespace_only_message() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(session_id=uuid4(), message="   ")


def test_source_document_accepts_reference_score_bounds() -> None:
    item = SourceDocument(
        document_id="fa_policy_chunk_0004",
        filename="FA-Depreciation-Policy.pdf",
        excerpt="Depreciation begins from the in-service date.",
        relevance_score=1.0,
    )
    assert item.relevance_score == 1.0


def test_document_metadata_handles_kb_style_filenames() -> None:
    metadata = DocumentMetadata(
        document_id="GL_General_Ledger_chunk_0001",
        filename="GL- General Ledger - Close Checklist.pdf",
        file_type="pdf",
        title="General Ledger Close Checklist",
        content_hash="hash123",
        chunk_index=0,
        total_chunks=3,
        last_modified=datetime(2026, 7, 1),
    )
    assert metadata.filename.startswith("GL-")
    assert metadata.total_chunks == 3


def test_ingestion_result_collects_errors() -> None:
    result = IngestionResult(
        total_files_found=2,
        files_processed=1,
        files_skipped=1,
        chunks_created=8,
        chunks_upserted=6,
        errors=[{"filename": "OTL-Corrupt.docx", "error": "decode failure"}],
        duration_seconds=1.7,
    )
    assert len(result.errors) == 1