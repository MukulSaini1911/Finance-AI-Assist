# =============================================================================
# app/rag/document_processor.py
#
# Handles all stages of document pre-processing:
#   1. Text extraction  (PDF, DOCX, TXT, HTML)
#   2. Text cleaning    (remove noise, normalize whitespace)
#   3. Chunking         (RecursiveCharacterTextSplitter)
#   4. Metadata extraction
#   5. Content hashing  (SHA-256 for deduplication)
#
# WHY RecursiveCharacterTextSplitter?
#   It tries to split on natural boundaries (paragraphs → sentences → words)
#   which keeps semantic meaning intact within each chunk.  A fixed-character
#   splitter would naively cut mid-sentence.
# =============================================================================

from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.settings import settings
from app.models.chat import DocumentMetadata
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Supported file extensions
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".html", ".htm"}


# ---------------------------------------------------------------------------
# Text extractors
# ---------------------------------------------------------------------------

def _extract_pdf(file_bytes: bytes) -> tuple[list[str], list[int]]:
    """
    Extract text page-by-page from a PDF.
    Returns a list of page texts and corresponding page numbers (1-based).
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    texts: list[str] = []
    page_numbers: list[int] = []
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            texts.append(text)
            page_numbers.append(page_num)
    return texts, page_numbers


def _extract_docx(file_bytes: bytes) -> tuple[list[str], list[int]]:
    """
    Extract text from a DOCX file.
    Returns all paragraphs as a single text block (page tracking is not
    reliable in python-docx — we return page 1 for all content).
    """
    from docx import Document  # type: ignore[import-untyped]

    doc = Document(io.BytesIO(file_bytes))

    parts: list[str] = []

    # Paragraph text in body
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)

    # Table cell text (many KB docs keep Q/A content in tables)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text:
                    parts.append(text)

    # Header/footer text per section
    for section in doc.sections:
        for para in section.header.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)
        for para in section.footer.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)

    full_text = "\n".join(parts)
    return [full_text], [1]


def _extract_txt(file_bytes: bytes) -> tuple[list[str], list[int]]:
    """Decode plain text files with UTF-8 fallback."""
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")
    return [text], [1]


def _extract_html(file_bytes: bytes) -> tuple[list[str], list[int]]:
    """Strip HTML tags using BeautifulSoup and return clean text."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(file_bytes, "lxml")
    # Remove script and style blocks
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return [text], [1]


# ---------------------------------------------------------------------------
# Text cleaner
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """
    Normalise text to remove artefacts introduced during PDF/DOCX extraction:
    - Collapse multiple blank lines to one
    - Remove non-printable characters
    - Normalise unicode spaces to ASCII space
    - Strip leading/trailing whitespace per line
    """
    # Replace unicode whitespace variants
    text = re.sub(r"[\u00a0\u2002\u2003\u2009\u200b]", " ", text)
    # Remove non-printable control characters (except newline/tab)
    text = re.sub(r"[^\S\n\t ]+", " ", text)
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip trailing spaces on each line
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Document processor
# ---------------------------------------------------------------------------

class DocumentProcessor:
    """
    Extracts, cleans, and chunks a raw document file.
    Produces a list of (chunk_text, metadata) tuples ready for embedding.
    """

    def __init__(self) -> None:
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
            # Split on paragraph boundaries first, then sentences, then words
            separators=["\n\n", "\n", ". ", "? ", "! ", " ", ""],
            length_function=len,
            is_separator_regex=False,
        )

    def process(
        self,
        file_bytes: bytes,
        filename: str,
        url: str | None = None,
        last_modified: datetime | None = None,
    ) -> list[DocumentMetadata]:
        """
        Full processing pipeline for a single document.

        Returns:
            List of DocumentMetadata objects (one per chunk).
            The caller uses `metadata.document_id` as the index key and
            `metadata.content_hash` for deduplication.

        Note: The actual chunk text is NOT stored in DocumentMetadata —
        it is stored in the parallel `contents` list returned alongside it.
        Call `process_with_content` to get both.
        """
        chunks_with_meta, _ = self._process_internal(
            file_bytes, filename, url, last_modified
        )
        return [meta for meta, _ in chunks_with_meta]

    def process_with_content(
        self,
        file_bytes: bytes,
        filename: str,
        url: str | None = None,
        last_modified: datetime | None = None,
    ) -> list[tuple[DocumentMetadata, str]]:
        """
        Full processing pipeline — returns (metadata, chunk_text) pairs.
        Used by the ingestion pipeline which needs both.
        """
        chunks_with_meta, _ = self._process_internal(
            file_bytes, filename, url, last_modified
        )
        return chunks_with_meta

    def _process_internal(
        self,
        file_bytes: bytes,
        filename: str,
        url: str | None,
        last_modified: datetime | None,
    ) -> tuple[list[tuple[DocumentMetadata, str]], str]:
        """Internal processing — returns (chunks, file_type)."""
        ext = Path(filename).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext}")

        # Extract raw pages
        extractor = {
            ".pdf": _extract_pdf,
            ".docx": _extract_docx,
            ".txt": _extract_txt,
            ".html": _extract_html,
            ".htm": _extract_html,
        }[ext]

        raw_pages, page_numbers = extractor(file_bytes)
        file_type = ext.lstrip(".")

        # Combine all pages into a single clean text, track page per section
        all_chunks: list[tuple[DocumentMetadata, str]] = []
        chunk_global_index = 0

        for page_text, page_num in zip(raw_pages, page_numbers):
            cleaned = _clean_text(page_text)
            if not cleaned:
                continue

            # Chunk the cleaned page text
            page_chunks = self._splitter.split_text(cleaned)

            for chunk_text in page_chunks:
                if not chunk_text.strip():
                    continue

                content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
                # Include the logical path (not just basename) to avoid key
                # collisions when same filenames exist in different folders.
                logical_name = Path(filename).with_suffix("").as_posix()
                safe_stem = re.sub(r"[^a-zA-Z0-9_\-=]", "_", logical_name)
                doc_id = f"{safe_stem}_chunk_{chunk_global_index:04d}"

                meta = DocumentMetadata(
                    document_id=doc_id,
                    filename=filename,
                    file_type=file_type,
                    title=Path(filename).stem.replace("_", " ").replace("-", " ").title(),
                    section=None,  # Section detection can be added via regex on headings
                    page_number=page_num,
                    url=url,
                    last_modified=last_modified,
                    content_hash=content_hash,
                    chunk_index=chunk_global_index,
                    total_chunks=0,  # Patched below after all chunks are collected
                )
                all_chunks.append((meta, chunk_text))
                chunk_global_index += 1

        # Patch total_chunks now that we know the final count
        total = len(all_chunks)
        for meta, _ in all_chunks:
            meta.total_chunks = total

        logger.info(
            "document_processed",
            filename=filename,
            file_type=file_type,
            pages=len(raw_pages),
            chunks=total,
        )

        return all_chunks, file_type


# Module-level singleton
_processor: DocumentProcessor | None = None


def get_document_processor() -> DocumentProcessor:
    global _processor
    if _processor is None:
        _processor = DocumentProcessor()
    return _processor
