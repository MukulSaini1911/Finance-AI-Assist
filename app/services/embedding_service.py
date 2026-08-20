# =============================================================================
# app/services/embedding_service.py
#
# Azure OpenAI embedding service used by both:
#   1. The ingestion pipeline  — to embed document chunks before indexing.
#   2. The retrieval pipeline  — to embed the user query before searching.
#
# Using the same model for both guarantees the vectors live in the same
# semantic space.  Mixing models is a common source of poor retrieval quality.
# =============================================================================

from __future__ import annotations

import hashlib
import random
from functools import lru_cache

from langchain_openai import AzureOpenAIEmbeddings

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _local_fallback_embedding(text: str, dimensions: int) -> list[float]:
    """Deterministic pseudo-embedding for local functional testing."""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dimensions)]


class EmbeddingService:
    """
    Wraps AzureOpenAIEmbeddings with project-specific configuration.
    """

    def __init__(self) -> None:
        self._embeddings: AzureOpenAIEmbeddings | None = None

    @property
    def embeddings(self) -> AzureOpenAIEmbeddings:
        """Lazy-initialised embedding model."""
        if self._embeddings is None:
            self._embeddings = AzureOpenAIEmbeddings(
                azure_endpoint=settings.azure_openai_endpoint_str,
                api_key=settings.azure_openai_api_key.get_secret_value(),
                api_version=settings.azure_openai_api_version,
                azure_deployment=settings.azure_openai_embedding_deployment,
                dimensions=settings.azure_openai_embedding_dimensions,
                # Chunk large batches automatically — Azure allows 16 texts per call
                chunk_size=16,
            )
            logger.info(
                "embedding_model_initialised",
                deployment=settings.azure_openai_embedding_deployment,
                dimensions=settings.azure_openai_embedding_dimensions,
            )
        return self._embeddings

    async def embed_query(self, text: str) -> list[float]:
        """
        Embed a single query string.
        Runs in the default executor to avoid blocking the event loop since
        the underlying openai client is synchronous.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None,
                self.embeddings.embed_query,
                text,
            )
        except Exception as exc:
            if not settings.use_local_embedding_fallback:
                raise
            logger.warning("embedding_query_fallback_used", error=str(exc))
            return _local_fallback_embedding(text, settings.azure_openai_embedding_dimensions)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunks."""
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None,
                self.embeddings.embed_documents,
                texts,
            )
        except Exception as exc:
            if not settings.use_local_embedding_fallback:
                raise
            logger.warning(
                "embedding_documents_fallback_used",
                error=str(exc),
                count=len(texts),
            )
            return [
                _local_fallback_embedding(text, settings.azure_openai_embedding_dimensions)
                for text in texts
            ]


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    """Return a cached singleton EmbeddingService."""
    return EmbeddingService()
