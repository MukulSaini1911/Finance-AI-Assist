# =============================================================================
# app/api/v1/chat.py
#
# Chat endpoint — the primary interface for OFA AI Assist.
#
# Routes:
#   POST /api/v1/chat         → non-streaming response
#   POST /api/v1/chat/stream  → server-sent events (SSE) streaming response
#
# Design decisions:
#   • Streaming uses FastAPI's StreamingResponse with SSE format so any HTTP
#     client (Streamlit, browser EventSource, curl) can consume it without
#     websockets.
#   • Session management is stateless on the server side: the client sends the
#     full conversation_history on each request.  This makes horizontal scaling
#     trivial and avoids server-side session storage complexity.
# =============================================================================

from __future__ import annotations

import json
from typing import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.models.chat import ChatRequest, ChatResponse
from app.services.rag_service import RAGService, get_rag_service
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Non-streaming chat
# ---------------------------------------------------------------------------

@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask OFA AI Assist (non-streaming)",
    description=(
        "Submit a question and optional conversation history. "
        "Returns a single JSON response with the answer and source citations."
    ),
)
async def chat(
    request: ChatRequest,
    rag_service: RAGService = Depends(get_rag_service),
) -> ChatResponse:
    """
    Non-streaming chat endpoint.
    Suitable for simple integrations or testing.
    """
    logger.info(
        "chat_request_received",
        session_id=str(request.session_id),
        message_length=len(request.message),
        history_turns=len(request.conversation_history),
    )

    try:
        response: ChatResponse = await rag_service.answer(request)
        logger.info(
            "chat_response_sent",
            session_id=str(response.session_id),
            sources_count=len(response.sources),
            tokens_used=response.tokens_used,
        )
        return response
    except Exception as exc:
        logger.error("chat_endpoint_error", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing your request.",
        ) from exc


# ---------------------------------------------------------------------------
# Streaming chat (SSE)
# ---------------------------------------------------------------------------

@router.post(
    "/chat/stream",
    summary="Ask OFA AI Assist (streaming)",
    description=(
        "Submit a question and receive a server-sent events stream. "
        "Each event is a JSON object with a `token` key for incremental text "
        "and a final `done` event with `sources` and `session_id`."
    ),
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat_stream(
    request: ChatRequest,
    rag_service: RAGService = Depends(get_rag_service),
) -> StreamingResponse:
    """
    Streaming chat endpoint using Server-Sent Events.

    SSE format::

        data: {"token": "Hello"}\\n\\n
        data: {"token": " World"}\\n\\n
        data: {"done": true, "session_id": "...", "sources": [...]}\\n\\n
    """
    logger.info(
        "stream_request_received",
        session_id=str(request.session_id),
        message_length=len(request.message),
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for event in rag_service.answer_stream(request):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.error("stream_error", error=str(exc), exc_info=True)
            error_event = {"error": "An error occurred while streaming the response."}
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disables nginx buffering for SSE
        },
    )
