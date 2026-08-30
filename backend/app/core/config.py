from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_api_key: str = Field(default="change-me", alias="APP_API_KEY")
    database_url: str = Field(
        default="postgresql+asyncpg://groundwork:groundwork@localhost:5432/groundwork",
        alias="DATABASE_URL",
    )
    database_pool_size: int = Field(default=5, alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=5, alias="DATABASE_MAX_OVERFLOW")
    database_pool_timeout_seconds: float = Field(
        default=10.0, alias="DATABASE_POOL_TIMEOUT_SECONDS"
    )

    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    # Synthesis reuses the grader model, and the reason is residency rather than quality.
    #
    # On quality alone qwen3:8b is the better writer: asked why local embeddings were dropped, it
    # cited only the retrieved text, while llama3.2:3b added "reduce maintenance burden", which
    # appears nowhere in the evidence. 4.0s against 2.6s warm would be worth that.
    #
    # But the two chat models plus the embedder are ~9.1GB, and Ollama will not hold that much on a
    # 16GB Mac — loading one evicts another even with OLLAMA_MAX_LOADED_MODELS=3. Alternating a 3B
    # grader with an 8B writer then pays a model load on nearly every request: measured end to end,
    # a RAG turn went 4.5s with one shared model to 18-22s with the split, of which ~8s was
    # reloading. Four times the latency to avoid one ungrounded adjective is the wrong trade, and
    # the grader plus citation validation already guard the failure modes that matter.
    #
    # Set OLLAMA_MODEL=qwen3:8b on a machine with enough memory to keep both resident.
    ollama_model: str = Field(default="llama3.2:3b", alias="OLLAMA_MODEL")
    # Sized for a warm generation plus one cold model load. Synthesis measures ~3s warm; a first
    # request that has to page the model in costs roughly 10s more. Too low a timeout silently
    # degrades every answer to the deterministic fallback, so this keeps real headroom.
    llm_timeout_seconds: float = Field(default=60.0, alias="LLM_TIMEOUT_SECONDS")
    ollama_think: bool = Field(default=False, alias="OLLAMA_THINK")
    llm_fallback_enabled: bool = Field(default=True, alias="LLM_FALLBACK_ENABLED")

    # Grading — llama3.2:3b, and this is the direction where bigger lost. Measured over
    # evals/retrieval_dataset.jsonl with Ollama on Metal:
    #
    #                     accuracy   refused   paraphrase    mean     max
    #   llama3.2:3b         0.950      2/3         2/2       3.4s    4.7s
    #   qwen3:8b            0.900      3/3         1/2      15.0s   35.8s
    #
    # qwen3:8b refuses one more unanswerable question and loses more elsewhere, at 4.4x the
    # latency. Grading is a long-prompt classification over 8-16 chunks that yields one bit of
    # signal, and prompt throughput is exactly where the small model dominates: 362 tok/s
    # against 109 tok/s.
    grader_enabled: bool = Field(default=True, alias="GRADER_ENABLED")
    grader_model: str = Field(default="llama3.2:3b", alias="GRADER_MODEL")
    grader_timeout_seconds: float = Field(default=30.0, alias="GRADER_TIMEOUT_SECONDS")
    corrective_max_attempts: int = Field(default=2, alias="CORRECTIVE_MAX_ATTEMPTS")
    # How many earlier turns feed follow-up resolution. Bounded so the prompt cannot grow with the
    # conversation; five is well past the point where a pronoun still refers backwards.
    conversation_history_turns: int = Field(default=5, alias="CONVERSATION_HISTORY_TURNS")

    # Hosted chat provider, used only where local inference is not viable. Ollama stays the default
    # and the only required provider; nothing here is needed to run the project.
    #
    # It exists because cloud CPU inference was measured and rejected: a RAG turn takes 67.9s on the
    # largest instance the AWS Free plan allows, against 8.1s on Metal. Exact-answer questions are
    # unaffected -- they make no model call at all -- so only generation moves.
    # See evals/baselines/deployment_inference.md.
    hosted_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="HOSTED_BASE_URL"
    )
    hosted_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    # Model ids are configuration, never literals. Groq retired llama-3.1-8b-instant and
    # llama-3.3-70b-versatile in August 2026, mid-plan; the next retirement should be an env change.
    # Qwen was available but is preview-only ("may be discontinued with limited notice"), which
    # rules it out for anything that has to keep working.
    hosted_model: str = Field(default="openai/gpt-oss-120b", alias="HOSTED_MODEL")
    hosted_grader_model: str = Field(default="openai/gpt-oss-20b", alias="HOSTED_GRADER_MODEL")
    # The counterpart of `ollama_think`, and the same trap. Measured on gpt-oss-20b for a
    # grading-shaped call: default effort spent 190 of 205 completion tokens on reasoning against
    # 25 of 63 at "low", for 3x the latency on a job that returns one bit. Free-tier tokens are
    # capped per minute, so the waste costs budget as well as time.
    hosted_reasoning_effort: str = Field(default="low", alias="HOSTED_REASONING_EFFORT")

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
    slack_bot_token: str | None = Field(default=None, alias="SLACK_BOT_TOKEN")
    slack_workspace_domain: str | None = Field(default=None, alias="SLACK_WORKSPACE_DOMAIN")
    slack_sync_overlap_minutes: int = Field(default=10, alias="SLACK_SYNC_OVERLAP_MINUTES")
    slack_sync_stale_after_minutes: int = Field(default=60, alias="SLACK_SYNC_STALE_AFTER_MINUTES")
    slack_sync_max_messages: int = Field(default=500, alias="SLACK_SYNC_MAX_MESSAGES")
    slack_sync_running_timeout_minutes: int = Field(
        default=15, alias="SLACK_SYNC_RUNNING_TIMEOUT_MINUTES"
    )

    # Web-search fallback, used only as the last corrective step when the project's own corpus has
    # been graded insufficient. Absent by default, which disables it.
    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")
    tavily_max_results: int = Field(default=4, alias="TAVILY_MAX_RESULTS")
    tavily_timeout_seconds: float = Field(default=15.0, alias="TAVILY_TIMEOUT_SECONDS")

    @property
    def web_fallback_enabled(self) -> bool:
        return bool(self.tavily_api_key)

    # Rate limiting. Two ceilings, because the scarce resource is not this machine.
    #
    # Groq's free tier is counted per organization and its binding limit is 8,000 tokens per minute
    # on the grading model. A grading call carries 8-16 chunks, so the budget sustains roughly two
    # to three RAG questions a minute; the eval runner exhausted it after 8 of 20 back-to-back
    # gradings. Twenty callers each under a per-client limit would drain it between them, which is
    # what the global ceiling exists for.
    #
    # The numbers are set to be invisible to a person clicking through a demo and restrictive to a
    # script. Exact-answer questions make no model call at all and are counted the same, which is
    # deliberate: the alternative is classifying the query in middleware to decide how to count it,
    # duplicating routing work to save a budget that is not actually under pressure from them.
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_per_client: int = Field(default=20, alias="RATE_LIMIT_PER_CLIENT")
    rate_limit_global: int = Field(default=60, alias="RATE_LIMIT_GLOBAL")
    rate_limit_window_seconds: float = Field(default=60.0, alias="RATE_LIMIT_WINDOW_SECONDS")

    backend_cors_origins: str = Field(default="http://localhost:3000", alias="BACKEND_CORS_ORIGINS")

    @cached_property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


settings = Settings()
