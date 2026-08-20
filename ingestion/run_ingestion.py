# =============================================================================
# ingestion/run_ingestion.py
#
# Standalone CLI script for running the SharePoint ingestion pipeline.
#
# Usage:
#   python ingestion/run_ingestion.py --mode full
#   python ingestion/run_ingestion.py --mode incremental
#
# Can also be invoked by a cron job or Azure Function for scheduled indexing.
# =============================================================================

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add the project root to sys.path so app imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.ingestion_service import get_ingestion_service
from app.utils.logger import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def main(mode: str) -> None:
    logger.info("ingestion_script_started", mode=mode)
    service = get_ingestion_service()

    if mode == "full":
        result = await service.run_full_ingestion()
    else:
        result = await service.run_incremental_ingestion()

    logger.info(
        "ingestion_script_complete",
        total_files_found=result.total_files_found,
        files_processed=result.files_processed,
        files_skipped=result.files_skipped,
        chunks_created=result.chunks_created,
        chunks_upserted=result.chunks_upserted,
        errors=len(result.errors),
        duration_seconds=result.duration_seconds,
    )

    if result.errors:
        logger.warning("ingestion_completed_with_errors", error_count=len(result.errors))
        for err in result.errors:
            logger.error("ingestion_error_detail", **err)

    # Exit non-zero if there were errors so CI/CD pipelines can detect failures
    sys.exit(1 if result.errors else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SharePoint → Azure AI Search ingestion")
    parser.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="incremental",
        help="Ingestion mode: 'full' re-indexes everything; 'incremental' skips unchanged files",
    )
    args = parser.parse_args()
    asyncio.run(main(args.mode))
