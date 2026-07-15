from functools import lru_cache
import os

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
    # Fail-fast routing: a single hung/dead node must not stall a request for the
    # full request_timeout. We connect quickly (so unreachable nodes fail fast) and
    # cap each individual candidate attempt at attempt_timeout_seconds, letting the
    # router fail over to the next candidate instead of waiting on a zombie.
    attempt_timeout_seconds: float = 45.0
    connect_timeout_seconds: float = 5.0
    # Hard bounds on a single request so a fleet full of dead nodes can never make
    # a request hang for (attempt_timeout * candidate_count). Once the deadline is
    # hit or we've tried enough candidates, we stop and return 503 fast.
    request_deadline_seconds: float = 90.0
    max_candidate_attempts: int = 4
    # Persisted per-node latency EMA so the router remembers which nodes are snappy
    # across restarts (instead of re-learning from a cold start every boot). The
    # path is derived from the database location so it lands in the same (mounted,
    # durable) directory as router.sqlite3 in both docker and local runs.
    latency_persist_interval_seconds: int = 30

    @property
    def latency_cache_path(self) -> str:
        db = self.database_url
        if db.startswith("sqlite:///"):
            db_path = db[len("sqlite:///"):]
            if not os.path.isabs(db_path):
                db_path = os.path.join(os.getcwd(), db_path)
            return os.path.join(os.path.dirname(os.path.abspath(db_path)), "latency_ema.json")
        return os.path.join(os.getcwd(), "data", "latency_ema.json")
    live_model_cache_ttl_seconds: int = 3600
    live_model_poll_interval_seconds: float = 5.0
    assistx_event_sink_url: str | None = None
    assistx_event_dispatch_timeout_seconds: float = 10.0
    assistx_event_dispatch_max_attempts: int = 5
    assistx_event_dispatch_interval_seconds: float = 300.0
    assistx_event_dispatch_batch_max: int = 200
    assistx_outbox_warning_threshold: int = 25
    assistx_outbox_critical_threshold: int = 200
    assistx_tasks_url: str | None = None
    assistx_tasks_timeout_seconds: float = 10.0
    assistx_basic_auth_user: str = ""
    assistx_basic_auth_pass: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
