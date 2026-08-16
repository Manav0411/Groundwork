from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_api_key: str = Field(default="change-me", alias="APP_API_KEY")
    database_url: str = Field(
        default="postgresql+asyncpg://agentic_rag:agentic_rag@localhost:5432/agentic_rag",
        alias="DATABASE_URL",
    )
    database_pool_size: int = Field(default=5, alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=5, alias="DATABASE_MAX_OVERFLOW")
    database_pool_timeout_seconds: float = Field(
        default=10.0, alias="DATABASE_POOL_TIMEOUT_SECONDS"
    )

    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen3:8b", alias="OLLAMA_MODEL")
    llm_timeout_seconds: float = Field(default=30.0, alias="LLM_TIMEOUT_SECONDS")
    llm_fallback_enabled: bool = Field(default=True, alias="LLM_FALLBACK_ENABLED")

    embedding_provider: str = Field(default="ollama", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="embeddinggemma", alias="EMBEDDING_MODEL")
    embedding_dimension: int = Field(default=768, alias="EMBEDDING_DIMENSION")

    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    github_sync_overlap_minutes: int = Field(default=10, alias="GITHUB_SYNC_OVERLAP_MINUTES")
    github_sync_stale_after_minutes: int = Field(
        default=60, alias="GITHUB_SYNC_STALE_AFTER_MINUTES"
    )
    github_sync_max_commits: int = Field(default=500, alias="GITHUB_SYNC_MAX_COMMITS")
    github_sync_running_timeout_minutes: int = Field(
        default=15, alias="GITHUB_SYNC_RUNNING_TIMEOUT_MINUTES"
    )
    jira_site_url: str | None = Field(default=None, alias="JIRA_SITE_URL")
    jira_cloud_id: str | None = Field(default=None, alias="JIRA_CLOUD_ID")
    jira_project_key: str | None = Field(default=None, alias="JIRA_PROJECT_KEY")
    jira_email: str | None = Field(default=None, alias="JIRA_EMAIL")
    jira_api_token: str | None = Field(default=None, alias="JIRA_API_TOKEN")
    jira_sync_overlap_minutes: int = Field(default=10, alias="JIRA_SYNC_OVERLAP_MINUTES")
    jira_sync_stale_after_minutes: int = Field(default=60, alias="JIRA_SYNC_STALE_AFTER_MINUTES")
    jira_sync_max_issues: int = Field(default=500, alias="JIRA_SYNC_MAX_ISSUES")
    jira_sync_running_timeout_minutes: int = Field(
        default=15, alias="JIRA_SYNC_RUNNING_TIMEOUT_MINUTES"
    )
    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")

    backend_cors_origins: str = Field(default="http://localhost:3000", alias="BACKEND_CORS_ORIGINS")

    @cached_property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


settings = Settings()
