# =============================================================================
# app/models/chat.py
#
# Pydantic v2 data models shared across the entire application.
#
# WHY centralise models here?
#   • Single source of truth — every layer (API, service, RAG) uses the same
#     schema. Changing a field in one place propagates everywhere.
#   • FastAPI auto-generates OpenAPI docs from these models.
#   • LangChain outputs get validated before being sent to the client.
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MessageRole(str, Enum):
    """Chat message roles aligned with OpenAI convention."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# ---------------------------------------------------------------------------
# Source / Citation models
# ---------------------------------------------------------------------------

class SourceDocument(BaseModel):
    """
    Metadata for a document chunk retrieved from Azure AI Search.
    Displayed in the Streamlit UI as a citation card.
    """

    document_id: str = Field(..., description="Unique chunk ID in the search index")
    filename: str = Field(..., description="Original filename on SharePoint")
    title: str | None = Field(default=None, description="Document title (from metadata)")
    page_number: int | None = Field(default=None, description="Page number within the source file")
    section: str | None = Field(default=None, description="Section heading if extractable")
    url: str | None = Field(default=None, description="SharePoint direct link to the document")
    relevance_score: float | None = Field(
        default=None,
        ge=0.0,
        description="Semantic relevance score returned by Azure AI Search",
    )
    excerpt: str = Field(..., description="The retrieved text chunk (shown as a quote)")


# ---------------------------------------------------------------------------
# Chat message models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    """A single turn in the conversation."""

    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ChatRequest(BaseModel):
    """
    Request body for POST /api/v1/chat.
    session_id ties together multiple turns of the same conversation.
    """

    session_id: UUID = Field(
        default_factory=uuid4,
        description="Client-generated or server-assigned session identifier",
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="User's question",
    )

    @field_validator("message")
    @classmethod
    def message_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be whitespace-only")
        return v
    conversation_history: list[ChatMessage] = Field(
        default_factory=list,
        description=(
            "Previous turns sent by the client. "
            "Allows the LLM to resolve follow-up questions."
        ),
    )
    stream: bool = Field(
        default=False,
        description="If true, response is server-sent events (SSE) stream",
    )


class ChatResponse(BaseModel):
    """
    Response body for POST /api/v1/chat (non-streaming).
    """

    session_id: UUID
    answer: str = Field(..., description="OFA AI Assist answer")
    sources: list[SourceDocument] = Field(
        default_factory=list,
        description="Documents used to generate the answer",
    )
    tokens_used: int | None = Field(
        default=None,
        description="Total tokens consumed by this request (prompt + completion)",
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Ingestion / indexing models
# ---------------------------------------------------------------------------

class DocumentMetadata(BaseModel):
    """
    Metadata attached to every chunk stored in Azure AI Search.
    This drives metadata filtering and source citation.
    """

    document_id: str = Field(..., description="Unique chunk ID: <filename>_chunk_<n>")
    filename: str
    file_type: str = Field(..., description="pdf | docx | txt | html")
    title: str | None = None
    section: str | None = None
    page_number: int | None = None
    url: str | None = None
    last_modified: datetime | None = None
    content_hash: str = Field(
        ...,
        description="SHA-256 of the raw chunk text — used for deduplication",
    )
    chunk_index: int = Field(..., ge=0, description="Zero-based index of this chunk within the document")
    total_chunks: int = Field(..., description="Total number of chunks produced from the document")


class IndexedDocument(BaseModel):
    """
    A chunk ready for upsert into Azure AI Search.
    Combines metadata, raw text, and the embedding vector.
    """

    metadata: DocumentMetadata
    content: str = Field(..., description="Clean chunk text sent to the LLM as context")
    embedding: list[float] = Field(
        ...,
        description="Dense embedding vector from Azure OpenAI",
    )


class IngestionResult(BaseModel):
    """Summary returned after running the ingestion pipeline."""

    total_files_found: int
    files_processed: int
    files_skipped: int  # Already indexed (hash match)
    chunks_created: int
    chunks_upserted: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    duration_seconds: float
