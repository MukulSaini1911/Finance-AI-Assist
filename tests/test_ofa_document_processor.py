from __future__ import annotations

import re

import pytest

from app.rag.document_processor import DocumentProcessor, _clean_text


def test_clean_text_normalizes_unicode_whitespace() -> None:
    cleaned = _clean_text("OTL\u00a0submission\u2009deadline")
    assert "\u00a0" not in cleaned
    assert "\u2009" not in cleaned


def test_process_with_content_generates_safe_ids_for_kb_names() -> None:
    processor = DocumentProcessor()
    content = ("GL close checklist step. " * 100).encode("utf-8")

    chunks = processor.process_with_content(
        file_bytes=content,
        filename="GL- General Ledger - Close Checklist.txt",
    )

    assert len(chunks) > 0
    first_id = chunks[0][0].document_id
    assert re.match(r"^[a-zA-Z0-9_\-=]+$", first_id)
    assert " " not in first_id


def test_process_with_content_rejects_unsupported_extension() -> None:
    processor = DocumentProcessor()
    with pytest.raises(ValueError, match="Unsupported file type"):
        processor.process_with_content(
            file_bytes=b"spreadsheet-bytes",
            filename="PA_Project_Accounting_Mapping.xlsx",
        )