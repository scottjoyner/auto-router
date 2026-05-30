from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="AUTO_ROUTER_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8088
    provider_config: str = "config/providers.yaml"
    policy_config: str = "config/policies.yaml"
    agent_config: str = "config/agent_workers.yaml"
    context_config: str = "config/context.yaml"
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite:///./data/router.sqlite3"
    log_prompts: bool = False
    default_profile: str = "interactive_balanced"
    request_timeout_seconds: float = 120.0
    live_model_cache_ttl_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
