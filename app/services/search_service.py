# =============================================================================
# app/services/search_service.py
#
# Azure AI Search client — handles index management, document upsert,
# and hybrid (vector + keyword) retrieval.
#
# WHY Azure AI Search for RAG?
#   • Native vector search (HNSW algorithm) with no external vector DB needed.
#   • Hybrid search merges BM25 (keyword) + vector scores via RRF fusion —
#     better recall than pure vector search for short HR queries.
#   • Built-in semantic re-ranking (L2 model) for further precision boost.
#   • Managed service — no infrastructure to maintain.
# =============================================================================

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.aio import SearchClient as AsyncSearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import QueryType, VectorizedQuery

from app.config.settings import settings
from app.models.chat import DocumentMetadata, IndexedDocument, SourceDocument
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Index field names (constants avoid typos across the codebase)
# ---------------------------------------------------------------------------
FIELD_ID = "document_id"
FIELD_CONTENT = "content"
FIELD_EMBEDDING = "embedding"
FIELD_FILENAME = "filename"
FIELD_FILE_TYPE = "file_type"
FIELD_TITLE = "title"
FIELD_SECTION = "section"
FIELD_PAGE_NUMBER = "page_number"
FIELD_URL = "url"
FIELD_LAST_MODIFIED = "last_modified"
FIELD_CONTENT_HASH = "content_hash"
FIELD_CHUNK_INDEX = "chunk_index"
FIELD_TOTAL_CHUNKS = "total_chunks"


def _build_index_definition() -> SearchIndex:
    """
    Define the Azure AI Search index schema.

    Key design choices:
    - `document_id` is the unique key (filename + chunk index).
    - `content` is searchable for BM25 keyword matching.
    - `embedding` is the dense vector field for semantic search.
    - All metadata fields are retrievable for source citation.
    - SemanticSearch configuration enables Azure's L2 re-ranker.
    """
    fields = [
        SimpleField(name=FIELD_ID, type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name=FIELD_CONTENT, type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SearchField(
            name=FIELD_EMBEDDING,
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=settings.azure_openai_embedding_dimensions,
            vector_search_profile_name="hr-vector-profile",
        ),
        SimpleField(name=FIELD_FILENAME, type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name=FIELD_FILE_TYPE, type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name=FIELD_TITLE, type=SearchFieldDataType.String),
        SearchableField(name=FIELD_SECTION, type=SearchFieldDataType.String),
        SimpleField(name=FIELD_PAGE_NUMBER, type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name=FIELD_URL, type=SearchFieldDataType.String),
        SimpleField(name=FIELD_LAST_MODIFIED, type=SearchFieldDataType.DateTimeOffset, filterable=True),
        SimpleField(name=FIELD_CONTENT_HASH, type=SearchFieldDataType.String, filterable=True),
        SimpleField(name=FIELD_CHUNK_INDEX, type=SearchFieldDataType.Int32),
        SimpleField(name=FIELD_TOTAL_CHUNKS, type=SearchFieldDataType.Int32),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="hr-hnsw",
                parameters=HnswParameters(
                    m=4,                    # Number of bi-directional links per node
                    ef_construction=400,    # Higher = better quality index, slower build
                    ef_search=500,          # Higher = better recall, slower query
                    metric="cosine",
                ),
            )
        ],
        profiles=[
            VectorSearchProfile(
                name="hr-vector-profile",
                algorithm_configuration_name="hr-hnsw",
            )
        ],
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=settings.azure_search_semantic_config,
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name=FIELD_CONTENT)],
                    title_field=SemanticField(field_name=FIELD_TITLE),
                    keywords_fields=[SemanticField(field_name=FIELD_SECTION)],
                ),
            )
        ]
    )

    return SearchIndex(
        name=settings.azure_search_index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )


class SearchService:
    """
    Manages Azure AI Search index lifecycle and document operations.
    """

    def __init__(self) -> None:
        credential = AzureKeyCredential(settings.azure_search_api_key.get_secret_value())

        self._index_client = SearchIndexClient(
            endpoint=settings.azure_search_endpoint_str,
            credential=credential,
        )
        self._search_client = AsyncSearchClient(
            endpoint=settings.azure_search_endpoint_str,
            index_name=settings.azure_search_index_name,
            credential=credential,
        )

    async def ping(self) -> None:
        """
        Verify connectivity to Azure AI Search and create the index if it
        does not already exist.  Called at application startup.
        """
        existing = [idx.name for idx in self._index_client.list_indexes()]
        if settings.azure_search_index_name not in existing:
            logger.info("search_index_not_found_creating", index=settings.azure_search_index_name)
            index_def = _build_index_definition()
            self._index_client.create_index(index_def)
            logger.info("search_index_created", index=settings.azure_search_index_name)
        else:
            logger.info("search_index_exists", index=settings.azure_search_index_name)

    async def upsert_documents(self, documents: list[IndexedDocument]) -> int:
        """
        Upsert a batch of indexed document chunks into Azure AI Search.
        Returns the number of documents successfully merged/uploaded.
        """
        batch = [
            {
                FIELD_ID: doc.metadata.document_id,
                FIELD_CONTENT: doc.content,
                FIELD_EMBEDDING: doc.embedding,
                FIELD_FILENAME: doc.metadata.filename,
                FIELD_FILE_TYPE: doc.metadata.file_type,
                FIELD_TITLE: doc.metadata.title,
                FIELD_SECTION: doc.metadata.section,
                FIELD_PAGE_NUMBER: doc.metadata.page_number,
                FIELD_URL: doc.metadata.url,
                FIELD_LAST_MODIFIED: doc.metadata.last_modified.isoformat()
                if doc.metadata.last_modified
                else None,
                FIELD_CONTENT_HASH: doc.metadata.content_hash,
                FIELD_CHUNK_INDEX: doc.metadata.chunk_index,
                FIELD_TOTAL_CHUNKS: doc.metadata.total_chunks,
            }
            for doc in documents
        ]

        result = await self._search_client.merge_or_upload_documents(documents=batch)

        succeeded = sum(1 for r in result if r.succeeded)
        logger.info("documents_upserted", total=len(batch), succeeded=succeeded)
        return succeeded

    async def hybrid_search(
        self,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        filter_expr: str | None = None,
    ) -> list[SourceDocument]:
        """
        Perform a hybrid search combining:
          1. BM25 keyword search on the `content` field.
          2. Vector similarity search on the `embedding` field.
          3. Azure Semantic re-ranking for final precision boost.

        Results are fused via Reciprocal Rank Fusion (RRF) automatically by
        Azure AI Search when both `search_text` and `vector_queries` are set.
        """
        vector_query = VectorizedQuery(
            vector=query_embedding,
            k_nearest_neighbors=top_k,
            fields=FIELD_EMBEDDING,
        )

        results = await self._search_client.search(
                search_text=query,           # BM25 component
                vector_queries=[vector_query],  # Vector component
                query_type=QueryType.SEMANTIC,
                semantic_configuration_name=settings.azure_search_semantic_config,
                top=top_k,
                filter=filter_expr,
                select=[
                    FIELD_ID,
                    FIELD_CONTENT,
                    FIELD_FILENAME,
                    FIELD_TITLE,
                    FIELD_SECTION,
                    FIELD_PAGE_NUMBER,
                    FIELD_URL,
                ],
                query_caption="extractive",   # Returns highlighted passages
            )

        sources: list[SourceDocument] = []
        async for result in results:
                score = result.get("@search.reranker_score") or result.get("@search.score", 0.0)
                # Apply minimum score threshold to filter low-confidence chunks
                if score < settings.rag_score_threshold:
                    continue
                sources.append(
                    SourceDocument(
                        document_id=result[FIELD_ID],
                        filename=result[FIELD_FILENAME],
                        title=result.get(FIELD_TITLE),
                        page_number=result.get(FIELD_PAGE_NUMBER),
                        section=result.get(FIELD_SECTION),
                        url=result.get(FIELD_URL),
                        relevance_score=round(float(score), 4),
                        excerpt=result[FIELD_CONTENT],  # full chunk; UI truncates for display
                    )
                )

        logger.info(
            "hybrid_search_complete",
            query_length=len(query),
            results_returned=len(sources),
        )
        return sources

    async def document_exists(self, content_hash: str, filename: str) -> bool:
        """
        Check if a chunk with the given content hash already exists in the
        index.  Used by the incremental ingestion pipeline to skip unchanged
        documents.

        Filters only on `filename` (always filterable) and compares
        `content_hash` client-side, so this also works against older indexes
        where `content_hash` was created without `filterable=True`.
        """
        escaped_filename = filename.replace("'", "''")
        results = await self._search_client.search(
            search_text="*",
            filter=f"{FIELD_FILENAME} eq '{escaped_filename}'",
            select=[FIELD_ID, FIELD_CONTENT_HASH],
        )
        async for result in results:
            if result.get(FIELD_CONTENT_HASH) == content_hash:
                return True
        return False

    async def delete_documents_by_filename(self, filename: str) -> int:
        """
        Delete all chunks for a given filename.
        Used when a document is deleted from SharePoint.
        """
        results = await self._search_client.search(
            search_text="*",
            filter=f"{FIELD_FILENAME} eq '{filename}'",
            select=[FIELD_ID],
            top=1000,
        )
        ids = []
        async for result in results:
            ids.append({FIELD_ID: result[FIELD_ID]})

        if ids:
            await self._search_client.delete_documents(documents=ids)

        logger.info("documents_deleted_by_filename", filename=filename, count=len(ids))
        return len(ids)


@lru_cache(maxsize=1)
def get_search_service() -> SearchService:
    """Return a cached singleton SearchService."""
    return SearchService()
