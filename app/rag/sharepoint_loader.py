# =============================================================================
# app/rag/sharepoint_loader.py
#
# Downloads Knowledge Base documents from a SharePoint folder using the
# Office365-REST-Python-Client library with client credentials (app-only)
# authentication — the correct approach for background/automated services
# that have no interactive user context.
#
# Authentication flow:
#   App Registration (Azure AD)
#     → Client ID + Secret
#     → OAuth2 token for SharePoint REST API
#     → Download files from the configured folder
#
# Required Azure AD permissions for the App Registration:
#   • Sites.Read.All (application permission, not delegated)
#   Granted by a tenant admin via the Azure Portal.
# =============================================================================

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from office365.runtime.auth.client_credential import ClientCredential
from office365.sharepoint.client_context import ClientContext

from app.config.settings import settings
from app.rag.document_processor import SUPPORTED_EXTENSIONS
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SharePointFile:
    """Represents a file downloaded from SharePoint."""

    filename: str                  # e.g. "GL-Month-End-Checklist.pdf"
    file_bytes: bytes              # Raw binary content
    url: str                       # Direct SharePoint URL
    last_modified: datetime | None


class SharePointLoader:
    """
    Lists and downloads files from a configured SharePoint folder.

    Supports:
    - Authentication via app-only client credentials
    - Folder traversal (non-recursive by default; set recursive=True)
    - File type filtering (SUPPORTED_EXTENSIONS only)
    - Lazy iteration — files are yielded one at a time to avoid loading
      the entire folder into memory at once
    """

    def __init__(self) -> None:
        self._ctx: ClientContext | None = None

    def _get_context(self) -> ClientContext:
        """
        Build (and cache) the authenticated SharePoint client context.
        Uses Client Credentials flow (app-only) — suitable for automation.
        """
        if self._ctx is None:
            credentials = ClientCredential(
                settings.sharepoint_client_id,
                settings.sharepoint_client_secret.get_secret_value(),
            )
            self._ctx = ClientContext(str(settings.sharepoint_site_url)).with_credentials(
                credentials
            )
            logger.info(
                "sharepoint_authenticated",
                site_url=str(settings.sharepoint_site_url),
            )
        return self._ctx

    def list_files(self, recursive: bool = False) -> list[dict[str, str]]:
        """
        Return a list of file metadata dicts from the configured SharePoint folder.
        Does NOT download file content — use `download_files` for that.

        Each dict contains: filename, server_relative_url, time_last_modified
        """
        ctx = self._get_context()
        folder = ctx.web.get_folder_by_server_relative_url(
            settings.sharepoint_folder_path
        )
        ctx.load(folder)
        ctx.execute_query()

        files_info: list[dict[str, str]] = []
        self._collect_files(ctx, folder, files_info, recursive=recursive)
        logger.info(
            "sharepoint_files_listed",
            folder=settings.sharepoint_folder_path,
            count=len(files_info),
        )
        return files_info

    def _collect_files(
        self,
        ctx: ClientContext,
        folder,
        result: list[dict[str, str]],
        recursive: bool,
    ) -> None:
        """Recursively (or not) collect file metadata from a folder."""
        files = folder.files
        ctx.load(files)
        ctx.execute_query()

        for f in files:
            ext = Path(f.properties["Name"]).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                logger.debug("sharepoint_file_skipped_unsupported_type", filename=f.properties["Name"])
                continue
            result.append(
                {
                    "filename": f.properties["Name"],
                    "server_relative_url": f.properties["ServerRelativeUrl"],
                    "time_last_modified": f.properties.get("TimeLastModified", ""),
                    "url": f"{str(settings.sharepoint_site_url).rstrip('/')}/_layouts/15/download.aspx?SourceUrl={f.properties['ServerRelativeUrl']}",
                }
            )

        if recursive:
            subfolders = folder.folders
            ctx.load(subfolders)
            ctx.execute_query()
            for subfolder in subfolders:
                ctx.load(subfolder)
                ctx.execute_query()
                self._collect_files(ctx, subfolder, result, recursive=True)

    def download_files(
        self, recursive: bool = False
    ) -> Iterator[SharePointFile]:
        """
        Download and yield each supported file from SharePoint one at a time.
        Using a generator avoids loading all files into memory simultaneously.
        """
        ctx = self._get_context()
        file_list = self.list_files(recursive=recursive)

        for file_info in file_list:
            try:
                logger.info("sharepoint_downloading", filename=file_info["filename"])

                # Download to an in-memory buffer
                buffer = io.BytesIO()
                (
                    ctx.web.get_file_by_server_relative_url(
                        file_info["server_relative_url"]
                    )
                    .download(buffer)
                    .execute_query()
                )
                buffer.seek(0)
                file_bytes = buffer.read()

                last_modified: datetime | None = None
                if file_info.get("time_last_modified"):
                    try:
                        last_modified = datetime.fromisoformat(
                            file_info["time_last_modified"].rstrip("Z")
                        )
                    except ValueError:
                        pass

                yield SharePointFile(
                    filename=file_info["filename"],
                    file_bytes=file_bytes,
                    url=file_info["url"],
                    last_modified=last_modified,
                )

                logger.info(
                    "sharepoint_downloaded",
                    filename=file_info["filename"],
                    size_bytes=len(file_bytes),
                )

            except Exception as exc:
                logger.error(
                    "sharepoint_download_error",
                    filename=file_info.get("filename", "unknown"),
                    error=str(exc),
                )
                # Continue processing remaining files


def get_sharepoint_loader() -> SharePointLoader:
    """Return a new SharePointLoader instance (not cached — stateless)."""
    return SharePointLoader()
