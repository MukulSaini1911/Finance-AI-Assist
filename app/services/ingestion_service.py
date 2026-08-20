# =============================================================================
# app/services/ingestion_service.py
#
# Orchestrates the full SharePoint → Azure AI Search ingestion pipeline.
#
# Pipeline:
#   SharePoint folder
#     → Download files (SharePointLoader)
#     → Extract & chunk text (DocumentProcessor)
#     → Generate embeddings (EmbeddingService)
#     → Upsert to Azure AI Search (SearchService)
#
# Two modes:
#   full       — Re-index every document (useful for initial setup or full refresh)
#   incremental — Skip documents whose SHA-256 hash already exists in the index
#
# Batch processing:
#   Embeddings are generated in batches of EMBED_BATCH_SIZE to avoid
#   Azure OpenAI rate-limit errors and to parallelise efficiently.
# =============================================================================

from __future__ import annotations

import asyncio
import time
from functools import lru_cache

from app.models.chat import IndexedDocument, IngestionResult
from app.rag.document_processor import DocumentProcessor, get_document_processor
from app.rag.sharepoint_loader import SharePointLoader, get_sharepoint_loader
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.search_service import SearchService, get_search_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Number of chunks to embed in a single batch.
# Azure OpenAI text-embedding-3-large: max 16 inputs per request.
EMBED_BATCH_SIZE = 16


class IngestionService:
    """
    Orchestrates the full document ingestion pipeline from SharePoint to
    Azure AI Search.
    """

    def __init__(
        self,
        loader: SharePointLoader,
        processor: DocumentProcessor,
        embedding_service: EmbeddingService,
        search_service: SearchService,
    ) -> None:
        self._loader = loader
        self._processor = processor
        self._embedding = embedding_service
        self._search = search_service

    async def run_full_ingestion(self) -> IngestionResult:
        """
        Download and index ALL documents from SharePoint.
        Existing index entries are overwritten (merge-or-upload semantics).
        """
        return await self._run(incremental=False)

    async def run_incremental_ingestion(self) -> IngestionResult:
        """
        Download documents from SharePoint and index only those that are
        new or have changed (detected via SHA-256 content hash).
        """
        return await self._run(incremental=True)

    async def _run(self, incremental: bool) -> IngestionResult:
        start_time = time.perf_counter()
        result = IngestionResult(
            total_files_found=0,
            files_processed=0,
            files_skipped=0,
            chunks_created=0,
            chunks_upserted=0,
            duration_seconds=0.0,
        )

        logger.info("ingestion_started", mode="incremental" if incremental else "full")

        # ---------------------------------------------------------------
        # Step 1 — List and download files from SharePoint
        # ---------------------------------------------------------------
        try:
            file_list = self._loader.list_files(recursive=True)
        except Exception as exc:
            logger.error("sharepoint_list_error", error=str(exc))
            result.errors.append({"stage": "list_files", "error": str(exc)})
            result.duration_seconds = time.perf_counter() - start_time
            return result

        result.total_files_found = len(file_list)
        logger.info("files_found_on_sharepoint", count=result.total_files_found)

        # ---------------------------------------------------------------
        # Step 2 — Process each file
        # ---------------------------------------------------------------
        all_indexed_docs: list[IndexedDocument] = []

        for file_info in file_list:
            filename = file_info["filename"]
            try:
                # Download
                sp_file = next(
                    f
                    for f in self._loader.download_files(recursive=True)
                    if f.filename == filename
                )

                # Extract & chunk
                chunks_with_meta = self._processor.process_with_content(
                    file_bytes=sp_file.file_bytes,
                    filename=sp_file.filename,
                    url=sp_file.url,
                    last_modified=sp_file.last_modified,
                )

                result.chunks_created += len(chunks_with_meta)

                # Incremental check — filter chunks already in the index
                if incremental:
                    new_chunks = []
                    for meta, text in chunks_with_meta:
                        exists = await self._search.document_exists(
                            meta.content_hash, meta.filename
                        )
                        if not exists:
                            new_chunks.append((meta, text))
                    skipped = len(chunks_with_meta) - len(new_chunks)
                    if skipped:
                        logger.debug(
                            "chunks_skipped_unchanged",
                            filename=filename,
                            skipped=skipped,
                        )
                    chunks_with_meta = new_chunks

                if not chunks_with_meta:
                    result.files_skipped += 1
                    continue

                # Build IndexedDocument placeholders (embedding filled below)
                for meta, text in chunks_with_meta:
                    all_indexed_docs.append(
                        IndexedDocument(
                            metadata=meta,
                            content=text,
                            embedding=[],  # Filled in Step 3
                        )
                    )

                result.files_processed += 1

            except Exception as exc:
                logger.error(
                    "file_processing_error",
                    filename=filename,
                    error=str(exc),
                )
                result.errors.append({"filename": filename, "error": str(exc)})

        # ---------------------------------------------------------------
        # Step 3 — Generate embeddings in batches
        # ---------------------------------------------------------------
        logger.info("embedding_generation_started", total_chunks=len(all_indexed_docs))

        for batch_start in range(0, len(all_indexed_docs), EMBED_BATCH_SIZE):
            batch = all_indexed_docs[batch_start : batch_start + EMBED_BATCH_SIZE]
            texts = [doc.content for doc in batch]

            try:
                embeddings = await self._embedding.embed_documents(texts)
                for doc, emb in zip(batch, embeddings):
                    doc.embedding = emb
            except Exception as exc:
                logger.error(
                    "embedding_error",
                    batch_start=batch_start,
                    error=str(exc),
                )
                result.errors.append(
                    {"stage": "embedding", "batch_start": batch_start, "error": str(exc)}
                )
                # Remove failed batch from upsert candidates
                for doc in batch:
                    all_indexed_docs.remove(doc)

        # ---------------------------------------------------------------
        # Step 4 — Upsert to Azure AI Search
        # ---------------------------------------------------------------
        logger.info("upsert_started", total_documents=len(all_indexed_docs))

        UPSERT_BATCH_SIZE = 100
        for batch_start in range(0, len(all_indexed_docs), UPSERT_BATCH_SIZE):
            batch = all_indexed_docs[batch_start : batch_start + UPSERT_BATCH_SIZE]
            try:
                upserted = await self._search.upsert_documents(batch)
                result.chunks_upserted += upserted
            except Exception as exc:
                logger.error(
                    "upsert_error",
                    batch_start=batch_start,
                    error=str(exc),
                )
                result.errors.append(
                    {"stage": "upsert", "batch_start": batch_start, "error": str(exc)}
                )

        result.duration_seconds = round(time.perf_counter() - start_time, 2)
        logger.info(
            "ingestion_complete",
            mode="incremental" if incremental else "full",
            **result.model_dump(exclude={"errors"}),
        )
        return result


@lru_cache(maxsize=1)
def get_ingestion_service() -> IngestionService:
    """Return a cached singleton IngestionService."""
    return IngestionService(
        loader=get_sharepoint_loader(),
        processor=get_document_processor(),
        embedding_service=get_embedding_service(),
        search_service=get_search_service(),
    )
