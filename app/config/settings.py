# =============================================================================
# app/config/settings.py
#
# Central configuration management using pydantic-settings.
#
# WHY pydantic-settings?
#   • All env vars are validated at application startup — fail fast if anything
#     is missing rather than discovering it at runtime.
#   • Full type annotations give IDEs and type checkers accurate completions.
#   • One canonical import (`from app.config.settings import settings`) replaces
#     dozens of scattered `os.getenv()` calls.
# =============================================================================

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables / .env file.
    All fields are validated at import time via the `get_settings()` factory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # AZURE_OPENAI_ENDPOINT == azure_openai_endpoint
        extra="ignore",        # Silently ignore unknown env vars
    )

    # -------------------------------------------------------------------------
    # Azure OpenAI
    # -------------------------------------------------------------------------
    azure_openai_endpoint: AnyHttpUrl = Field(..., description="Azure OpenAI resource endpoint")
    azure_openai_api_key: SecretStr = Field(..., description="Azure OpenAI API key")
    azure_openai_api_version: str = Field(default="2024-02-01")
    azure_openai_chat_deployment: str = Field(default="gpt-4o")
    azure_openai_embedding_deployment: str = Field(default="text-embedding-3-large")
    azure_openai_embedding_dimensions: int = Field(default=3072)

    # -------------------------------------------------------------------------
    # Azure AI Search
    # -------------------------------------------------------------------------
    azure_search_endpoint: AnyHttpUrl = Field(..., description="Azure AI Search endpoint")
    azure_search_api_key: SecretStr = Field(..., description="Azure AI Search admin key")
    azure_search_index_name: str = Field(default="ofa-finance-kb")
    azure_search_semantic_config: str = Field(default="ofa-finance-semantic-config")

    # -------------------------------------------------------------------------
    # SharePoint
    # -------------------------------------------------------------------------
    sharepoint_tenant_id: str = Field(..., description="Azure AD tenant ID")
    sharepoint_client_id: str = Field(..., description="App registration client ID")
    sharepoint_client_secret: SecretStr = Field(..., description="App registration client secret")
    sharepoint_site_url: AnyHttpUrl = Field(..., description="SharePoint site root URL")
    sharepoint_folder_path: str = Field(
        default="/Shared Documents/Knowledge Base",
        description="Server-relative path to the finance Knowledge Base folder",
    )

    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    app_env: Literal["development", "staging", "production"] = Field(default="development")
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)

    # -------------------------------------------------------------------------
    # RAG Tuning
    # -------------------------------------------------------------------------
    rag_chunk_size: int = Field(default=800, ge=200, le=4000)
    rag_chunk_overlap: int = Field(default=150, ge=0, le=500)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_score_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    use_local_embedding_fallback: bool = Field(default=False)

    # -------------------------------------------------------------------------
    # Streamlit
    # -------------------------------------------------------------------------
    streamlit_backend_url: AnyHttpUrl = Field(default="http://localhost:8000")  # type: ignore[assignment]
    streamlit_page_title: str = Field(default="OFA AI Assist — Finance Assistant")

    # -------------------------------------------------------------------------
    # Derived helpers (not env vars)
    # -------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def azure_openai_endpoint_str(self) -> str:
        """Return endpoint as plain string (strips pydantic AnyHttpUrl wrapper)."""
        return str(self.azure_openai_endpoint)

    @property
    def azure_search_endpoint_str(self) -> str:
        return str(self.azure_search_endpoint)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached singleton Settings instance.
    Using lru_cache means .env is only parsed once per process lifetime,
    which is important for performance and predictability.
    """
    return Settings()


# Module-level convenience alias — import as: `from app.config.settings import settings`
settings: Settings = get_settings()
