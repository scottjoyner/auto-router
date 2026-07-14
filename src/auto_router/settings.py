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
    live_model_poll_interval_seconds: float = 5.0
    assistx_event_sink_url: str | None = None
    assistx_event_dispatch_timeout_seconds: float = 10.0
    assistx_event_dispatch_max_attempts: int = 5
    assistx_event_dispatch_interval_seconds: float = 300.0
    assistx_tasks_url: str | None = None
    assistx_tasks_timeout_seconds: float = 10.0
    assistx_basic_auth_user: str = ""
    assistx_basic_auth_pass: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
