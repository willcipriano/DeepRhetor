"""Pydantic settings for user-level DeepRhetor configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class OpenRouterConfig(BaseModel):
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://openrouter.ai/api/v1"


class TavilyConfig(BaseModel):
    api_key: SecretStr = SecretStr("")


class ProvidersConfig(BaseModel):
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    tavily: TavilyConfig = Field(default_factory=TavilyConfig)


class ModelPreset(BaseModel):
    provider: str = "openrouter"
    model: str
    temperature: float = 0.2


class LimitsConfig(BaseModel):
    max_search_results_per_assignment: int = 25
    max_follow_up_searches: int = 5
    max_model_retries: int = 3
    max_tool_retries: int = 3
    max_critic_passes: int = 5
    max_document_bytes: int = 52_428_800
    max_project_bytes: int = 1_073_741_824
    fetch_timeout_seconds: int = 60
    model_timeout_seconds: int = 300
    provider_concurrency: int = 4
    max_run_duration_seconds: int = 7_200
    # Provider request rate limits (requests per minute). arXiv is intentionally strict.
    tavily_rate_limit_per_minute: int = 30
    openalex_rate_limit_per_minute: int = 100
    crossref_rate_limit_per_minute: int = 50
    arxiv_rate_limit_per_minute: int = 20  # ~1 request / 3s polite pool


class AppConfig(BaseModel):
    """Effective user configuration (credentials never written to project DBs)."""

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    models: dict[str, ModelPreset] = Field(
        default_factory=lambda: {
            "cheap": ModelPreset(model="openai/gpt-4o-mini"),
            "mid": ModelPreset(model="openai/gpt-4o"),
            "frontier": ModelPreset(model="anthropic/claude-opus-4", temperature=0.4),
        }
    )
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    def credential_paths(self) -> list[str]:
        return [
            "providers.openrouter.api_key",
            "providers.tavily.api_key",
        ]
