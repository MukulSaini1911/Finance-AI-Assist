# =============================================================================
# app/services/rag_service.py
#
# The core RAG orchestration service.
#
# Pipeline stages executed per query:
#   1. Condense follow-up question into a standalone query (if history exists)
#   2. Embed the standalone query
#   3. Hybrid search Azure AI Search
#   4. Build context string from retrieved chunks
#   5. Construct the chat prompt (system + history + question + context)
#   6. Call GPT-4o
#   7. Return answer + source citations
#
# WHY a separate RAG service?
#   The API router handles HTTP concerns; this service handles AI logic.
#   The separation makes unit testing the AI pipeline trivial — no HTTP layer
#   needed.
# =============================================================================

from __future__ import annotations

import re
from random import choice
from functools import lru_cache
from typing import AsyncGenerator, Any
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

from app.config.settings import settings
from app.models.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    MessageRole,
    SourceDocument,
)
from app.prompts.finance_prompts import (
    CONDENSE_QUESTION_PROMPT,
    HR_CHAT_PROMPT,
    OUT_OF_SCOPE_RESPONSE,
)
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.guardrails import PROMPT_INJECTION_RESPONSE, contains_prompt_injection
from app.services.llm_service import LLMService, get_llm_service
from app.services.search_service import SearchService, get_search_service
from app.utils.logger import get_logger

logger = get_logger(__name__)
GREETING_RESPONSES: dict[str, list[str]] = {
    "good morning": [
        "Good morning! How can I help you today?",
        "Good morning! What can I do for you today?",
        "Good morning! How may I assist you today?",
    ],
    "good afternoon": [
        "Good afternoon! How can I help you today?",
        "Good afternoon! What can I do for you today?",
        "Good afternoon! How may I assist you today?",
    ],
    "good evening": [
        "Good evening! How can I help you today?",
        "Good evening! What can I do for you today?",
        "Good evening! How may I assist you today?",
    ],
    "hi": [
        "Hi there! How can I help you today?",
        "Hi! What would you like to know today?",
    ],
    "hello": [
        "Hello! What can I do for you today?",
        "Hello there! How may I assist you today?",
    ],
    "hey": [
        "Hey! How can I support you today?",
        "Hey there! What can I help you with today?",
    ],
    "generic": [
        "Hello! How can I help you today?",
        "Hi there! What can I do for you today?",
    ],
}


def _to_langchain_messages(
    history: list[ChatMessage],
) -> list[HumanMessage | AIMessage]:
    """
    Convert our internal ChatMessage list to LangChain message objects.
    LangChain's MessagesPlaceholder expects this format.
    """
    lc_messages: list[HumanMessage | AIMessage] = []
    for msg in history:
        if msg.role == MessageRole.USER:
            lc_messages.append(HumanMessage(content=msg.content))
        elif msg.role == MessageRole.ASSISTANT:
            lc_messages.append(AIMessage(content=msg.content))
    return lc_messages


def _build_context(sources: list[SourceDocument]) -> str:
    """
    Format retrieved source documents into a context block injected into
    the system prompt.

    Each chunk is numbered and includes its filename so the LLM can
    naturally reference sources in its answer.
    """
    if not sources:
        return "No relevant documents were found in the knowledge base."

    parts: list[str] = []
    for i, src in enumerate(sources, start=1):
        header = f"[{i}] Source: {src.filename}"
        if src.page_number:
            header += f", Page {src.page_number}"
        if src.section:
            header += f", Section: {src.section}"
        parts.append(f"<source>\n{header}\n{src.excerpt}\n</source>")

    return "\n\n---\n\n".join(parts)


def _is_greeting_only(message: str) -> bool:
    """
    Return True when the user message is a bare greeting.

    These turns should not trigger retrieval or surface citations because the
    response is handled directly by the prompt guidance.
    """
    normalized = re.sub(r"[^\w\s]", "", message.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized in {
        "hi",
        "hi there",
        "hello",
        "hello there",
        "hey",
        "hey there",
        "hiya",
        "good morning",
        "good afternoon",
        "good evening",
    }


def _greeting_key(message: str) -> str:
    """Map a greeting message to the closest response bucket."""
    normalized = re.sub(r"[^\w\s]", "", message.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)

    if normalized.startswith("good morning"):
        return "good morning"
        return "good afternoon"
    if normalized.startswith("good evening"):
        return "good evening"
    if normalized.startswith("hello"):
        return "hello"
    if normalized.startswith("hey"):
        return "hey"
    if normalized.startswith("hi"):
        return "hi"
    return "generic"


async def _yield_text_as_stream(text: str) -> AsyncGenerator[dict[str, Any], None]:
    """Yield a single token event followed by a terminal event."""
    if text:
        yield {"token": text}
    yield {"done": True, "session_id": None, "sources": []}


def _greeting_response() -> str:
    """Return a friendly greeting-only response."""
    return choice(GREETING_RESPONSES["generic"])


def _greeting_response_for_message(message: str) -> str:
    """Return a greeting response that mirrors the user's salutation."""
    greeting_key = _greeting_key(message)
    return choice(GREETING_RESPONSES.get(greeting_key, GREETING_RESPONSES["generic"]))


def _request_contains_injection(request: ChatRequest) -> bool:
    """Check user-controlled text before it can reach retrieval or the LLM."""
    return contains_prompt_injection(
        [request.message, *(message.content for message in request.conversation_history)]
    )


class RAGService:
    """
    Orchestrates the end-to-end RAG pipeline for a single user query.
    """

    def __init__(
        self,
        llm_service: LLMService,
        embedding_service: EmbeddingService,
        search_service: SearchService,
    ) -> None:
        self._llm = llm_service
        self._embedding = embedding_service
        self._search = search_service

    async def _condense_question(
        self,
        question: str,
        history: list[ChatMessage],
    ) -> str:
        """
        Stage 1 — Condense follow-up questions.

        If there is no conversation history, the original question is returned
        unchanged (avoids an unnecessary LLM call).
        """
        if not history:
            return question

        lc_history = _to_langchain_messages(history)
        chain = CONDENSE_QUESTION_PROMPT | self._llm.chat | StrOutputParser()
        condensed: str = await chain.ainvoke(
            {"question": question, "chat_history": lc_history}
        )
        logger.debug(
            "question_condensed",
            original=question,
            condensed=condensed,
        )
        return condensed.strip()

    async def answer(self, request: ChatRequest) -> ChatResponse:
        """
        Non-streaming RAG pipeline.
        Returns the full answer once all stages are complete.
        """
        greeting_only = _is_greeting_only(request.message)

        if _request_contains_injection(request):
            return ChatResponse(
                session_id=request.session_id,
                answer=PROMPT_INJECTION_RESPONSE,
                sources=[],
                tokens_used=None,
            )

        if greeting_only:
            answer_text = _greeting_response_for_message(request.message)
            logger.info(
                "rag_pipeline_complete",
                session_id=str(request.session_id),
                sources_used=0,
                tokens_used=None,
                out_of_scope=False,
                greeting_only=True,
            )
            return ChatResponse(
                session_id=request.session_id,
                answer=answer_text,
                sources=[],
                tokens_used=None,
            )

        # Stage 1: Condense follow-up question
        standalone_query = await self._condense_question(
            request.message, request.conversation_history
        )

        sources: list[SourceDocument] = []
        context = ""

        if not greeting_only:
            # Stage 2: Embed the standalone query
            query_embedding = await self._embedding.embed_query(standalone_query)

            # Stage 3: Hybrid search
            sources = await self._search.hybrid_search(
                query=standalone_query,
                query_embedding=query_embedding,
                top_k=settings.rag_top_k,
            )

            # Stage 4: Build context string
            context = _build_context(sources)

        # Stage 5: Build LangChain message list
        lc_history = _to_langchain_messages(request.conversation_history)

        # Stage 6: Invoke GPT-4o
        prompt_value = await HR_CHAT_PROMPT.ainvoke(
            {
                "context": context,
                "chat_history": lc_history,
                "question": request.message,
            }
        )

        ai_message: AIMessage = await self._llm.chat.ainvoke(prompt_value)
        answer_text: str = ai_message.content

        # Stage 7: Count tokens (approximate via usage metadata if available)
        tokens_used: int | None = None
        if hasattr(ai_message, "usage_metadata") and ai_message.usage_metadata:
            tokens_used = ai_message.usage_metadata.get("total_tokens")

        # If the model returned the out-of-scope sentinel, clear sources
        # so the UI does not display misleading citations.
        final_sources = [] if greeting_only or OUT_OF_SCOPE_RESPONSE in answer_text else sources

        logger.info(
            "rag_pipeline_complete",
            session_id=str(request.session_id),
            sources_used=len(final_sources),
            tokens_used=tokens_used,
            out_of_scope=len(final_sources) == 0 and bool(sources),
        )

        return ChatResponse(
            session_id=request.session_id,
            answer=answer_text,
            sources=final_sources,
            tokens_used=tokens_used,
        )

    async def answer_stream(
        self, request: ChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Streaming RAG pipeline.
        Yields SSE-compatible dicts:
          {"token": "<partial text>"}  — incremental tokens
          {"done": true, "session_id": "...", "sources": [...]}  — final event
        """
        # Stages 1-4 are identical — retrieval must complete before streaming
        greeting_only = _is_greeting_only(request.message)

        if _request_contains_injection(request):
            async for event in _yield_text_as_stream(PROMPT_INJECTION_RESPONSE):
                if event.get("done"):
                    event["session_id"] = str(request.session_id)
                yield event
            return

        if greeting_only:
            async for event in _yield_text_as_stream(_greeting_response_for_message(request.message)):
                if event.get("done"):
                    event["session_id"] = str(request.session_id)
                yield event
            return

        standalone_query = await self._condense_question(
            request.message, request.conversation_history
        )

        sources: list[SourceDocument] = []
        context = ""

        if not greeting_only:
            query_embedding = await self._embedding.embed_query(standalone_query)
            sources = await self._search.hybrid_search(
                query=standalone_query,
                query_embedding=query_embedding,
                top_k=settings.rag_top_k,
            )
            context = _build_context(sources)

        lc_history = _to_langchain_messages(request.conversation_history)

        prompt_value = await HR_CHAT_PROMPT.ainvoke(
            {
                "context": context,
                "chat_history": lc_history,
                "question": request.message,
            }
        )

        full_answer = ""
        async for chunk in self._llm.streaming_chat.astream(prompt_value):
            token = chunk.content
            if token:
                full_answer += token
                yield {"token": token}

        final_sources = [] if greeting_only or OUT_OF_SCOPE_RESPONSE in full_answer else sources

        yield {
            "done": True,
            "session_id": str(request.session_id),
            "sources": [s.model_dump() for s in final_sources],
        }


@lru_cache(maxsize=1)
def get_rag_service() -> RAGService:
    """Return a cached singleton RAGService."""
    return RAGService(
        llm_service=get_llm_service(),
        embedding_service=get_embedding_service(),
        search_service=get_search_service(),
    )
