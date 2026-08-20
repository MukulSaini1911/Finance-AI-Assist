# =============================================================================
# ingestion/ingest_local.py
#
# Ingest documents from a local folder into Azure AI Search.
# Use this instead of run_ingestion.py when SharePoint is not yet configured.
#
# Usage:
#   python ingestion/ingest_local.py --folder "Knowledge Base"
#   python ingestion/ingest_local.py --folder "Knowledge Base" --mode incremental
# =============================================================================

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import sys
import time
from pathlib import Path

# Add project root to sys.path so app imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.settings import settings
from app.models.chat import DocumentMetadata, IndexedDocument, IngestionResult
from app.rag.document_processor import DocumentProcessor, get_document_processor
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.search_service import SearchService, get_search_service
from app.utils.logger import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".html", ".htm"}
EMBED_BATCH_SIZE = 16


def _local_fallback_embedding(text: str, dimensions: int) -> list[float]:
    """
    Deterministic local embedding for test-mode ingestion.
    Uses a seeded PRNG from text hash so vectors are stable across runs.
    """
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dimensions)]


async def ingest_local(
    folder: str,
    incremental: bool,
    use_local_embeddings_fallback: bool = False,
) -> IngestionResult:
    folder_path = Path(folder)
    if not folder_path.exists():
        logger.error("folder_not_found", folder=str(folder_path.resolve()))
        sys.exit(1)

    search: SearchService = get_search_service()
    processor: DocumentProcessor = get_document_processor()
    embedding: EmbeddingService = get_embedding_service()

    # Ensure the index exists (creates it if missing)
    await search.ping()

    files = sorted(
        [
            f
            for f in folder_path.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ],
        key=lambda p: p.as_posix().lower(),
    )

    if not files:
        logger.warning("no_supported_files_found", folder=str(folder_path))
        sys.exit(0)

    logger.info("files_found", count=len(files), folder=str(folder_path), recursive=True)

    result = IngestionResult(
        total_files_found=len(files),
        files_processed=0,
        files_skipped=0,
        chunks_created=0,
        chunks_upserted=0,
        duration_seconds=0.0,
    )
    start = time.perf_counter()

    all_docs: list[IndexedDocument] = []

    for file_path in files:
        relative_name = file_path.relative_to(folder_path).as_posix()
        logger.info("processing_file", filename=relative_name)
        try:
            file_bytes = file_path.read_bytes()
            chunks = processor.process_with_content(
                file_bytes=file_bytes,
                filename=relative_name,
            )

            result.chunks_created += len(chunks)

            if incremental:
                new_chunks = []
                for meta, text in chunks:
                    if not await search.document_exists(meta.content_hash, meta.filename):
                        new_chunks.append((meta, text))
                skipped = len(chunks) - len(new_chunks)
                if skipped:
                    logger.info("chunks_skipped_unchanged", filename=relative_name, skipped=skipped)
                chunks = new_chunks

            if not chunks:
                result.files_skipped += 1
                logger.info("file_skipped_already_indexed", filename=relative_name)
                continue

            for meta, text in chunks:
                all_docs.append(IndexedDocument(metadata=meta, content=text, embedding=[]))

            result.files_processed += 1

        except Exception as exc:
            logger.error("file_processing_error", filename=relative_name, error=str(exc))
            result.errors.append({"filename": relative_name, "error": str(exc)})

    if not all_docs:
        logger.info("nothing_to_index")
        result.duration_seconds = time.perf_counter() - start
        return result

    # Generate embeddings in batches
    logger.info("generating_embeddings", total_chunks=len(all_docs))
    for i in range(0, len(all_docs), EMBED_BATCH_SIZE):
        batch = all_docs[i : i + EMBED_BATCH_SIZE]
        texts = [doc.content for doc in batch]
        try:
            vectors = await embedding.embed_documents(texts)
            for doc, vec in zip(batch, vectors):
                doc.embedding = vec
        except Exception as exc:
            error_text = str(exc)
            if use_local_embeddings_fallback:
                logger.warning(
                    "embedding_fallback_used",
                    batch_start=i,
                    reason=error_text,
                    dimensions=settings.azure_openai_embedding_dimensions,
                )
                for doc in batch:
                    doc.embedding = _local_fallback_embedding(
                        doc.content, settings.azure_openai_embedding_dimensions
                    )
            else:
                logger.error("embedding_error", batch_start=i, error=error_text)
                result.errors.append(
                    {"stage": "embedding", "batch_start": i, "error": error_text}
                )

    # Filter out docs where embedding failed (still empty)
    ready = [d for d in all_docs if d.embedding]
    if not ready:
        logger.error("no_documents_with_embeddings")
        result.duration_seconds = time.perf_counter() - start
        return result

    # Upsert to Azure AI Search
    logger.info("upserting_to_search", count=len(ready))
    try:
        upserted = await search.upsert_documents(ready)
        result.chunks_upserted = upserted
        logger.info("upsert_complete", upserted=upserted)
    except Exception as exc:
        logger.error("upsert_error", error=str(exc))
        result.errors.append({"stage": "upsert", "error": str(exc)})

    result.duration_seconds = round(time.perf_counter() - start, 2)

    logger.info(
        "ingestion_complete",
        total_files_found=result.total_files_found,
        files_processed=result.files_processed,
        files_skipped=result.files_skipped,
        chunks_created=result.chunks_created,
        chunks_upserted=result.chunks_upserted,
        errors=len(result.errors),
        duration_seconds=result.duration_seconds,
    )
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest local documents into Azure AI Search")
    parser.add_argument(
        "--folder",
        default="Knowledge Base",
        help="Path to the local folder containing documents (default: 'Knowledge Base')",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="full",
        help="full = re-index everything; incremental = skip unchanged files",
    )
    parser.add_argument(
        "--use-local-embeddings-fallback",
        action="store_true",
        help=(
            "If Azure embeddings fail, generate deterministic local test vectors "
            "to keep ingestion testable. Do not use for production relevance tuning."
        ),
    )
    args = parser.parse_args()

    result = await ingest_local(
        folder=args.folder,
        incremental=(args.mode == "incremental"),
        use_local_embeddings_fallback=args.use_local_embeddings_fallback,
    )
    sys.exit(1 if result.errors else 0)


if __name__ == "__main__":
    asyncio.run(main())
