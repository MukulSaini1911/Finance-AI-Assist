# =============================================================================
# app/services/llm_service.py
#
# Thin wrapper around LangChain's AzureChatOpenAI.
#
# WHY a wrapper service?
#   • Centralises retry configuration, timeout, and model parameters.
#   • The rest of the application never imports langchain_openai directly —
#     swapping the LLM vendor only requires changing this file.
#   • Enables mocking in tests.
# =============================================================================

from __future__ import annotations

from functools import lru_cache

from langchain_openai import AzureChatOpenAI

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class LLMService:
    """
    Provides the configured AzureChatOpenAI instance.
    Also provides a separate instance with streaming enabled.
    """

    def __init__(self) -> None:
        self._chat_model: AzureChatOpenAI | None = None
        self._streaming_model: AzureChatOpenAI | None = None

    def _build_model(self, streaming: bool = False) -> AzureChatOpenAI:
        """Build and return an AzureChatOpenAI instance."""
        return AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint_str,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_chat_deployment,
            max_tokens=4096,
            streaming=streaming,
            # Retry configuration — handles transient Azure OpenAI rate limits
            max_retries=3,
            request_timeout=60,
        )

    @property
    def chat(self) -> AzureChatOpenAI:
        """Non-streaming model for batch responses."""
        if self._chat_model is None:
            self._chat_model = self._build_model(streaming=False)
            logger.info(
                "llm_initialised",
                deployment=settings.azure_openai_chat_deployment,
                streaming=False,
            )
        return self._chat_model

    @property
    def streaming_chat(self) -> AzureChatOpenAI:
        """Streaming model for SSE responses."""
        if self._streaming_model is None:
            self._streaming_model = self._build_model(streaming=True)
            logger.info(
                "llm_initialised",
                deployment=settings.azure_openai_chat_deployment,
                streaming=True,
            )
        return self._streaming_model


@lru_cache(maxsize=1)
def get_llm_service() -> LLMService:
    """Return a cached singleton LLMService."""
    return LLMService()
