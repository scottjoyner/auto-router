from types import SimpleNamespace

from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.context import ContextService, ContextSnapshot
from auto_router.models import ModelConfig, PolicyProfile, PolicyStage, ProviderConfig, StagePurpose
from auto_router.preflight import build_preflight_report
from auto_router.settings import Settings


class EmptyOutbox:
    def summary(self):
        return {"pending": 0, "retry": 0, "delivered": 0, "dead_letter": 0, "total": 0}


class DeadLetterOutbox:
    def summary(self):
        return {"pending": 0, "retry": 0, "delivered": 0, "dead_letter": 1, "total": 1}


class ModelRegistry:
    def summary(self):
        return {"providers": 1, "ok": 1, "error": 0, "models": 3, "stale": 0}


def _providers(local: bool = True) -> ProviderRegistry:
    providers = []
    if local:
        providers.append(
            ProviderConfig(
                name="lmstudio-local",
                type="lmstudio",
                base_url="http://localhost:1234/v1",
                quota_class="local",
                models=[ModelConfig(alias="local/model", provider_model="local/model")],
            )
        )
    providers.append(
        ProviderConfig(
            name="cerebras",
            type="openai_compatible",
            base_url="https://api.cerebras.ai/v1",
            quota_class="fast_free",
            models=[ModelConfig(alias="cerebras/flash", provider_model="gpt-oss-120b")],
        )
    )
    return ProviderRegistry(providers=providers)


def _policies() -> PolicyRegistry:
    stage = PolicyStage(purpose=StagePurpose.draft, provider_classes=["fast_free"])
    return PolicyRegistry(
        profiles={
            "backlog_burn": PolicyProfile(description="backlog", stages=[stage]),
            "flash_start_planner": PolicyProfile(description="flash", stages=[stage]),
            "sophia_realtime": PolicyProfile(description="sophia", stages=[stage]),
        }
    )


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        providers=_providers(),
        policies=_policies(),
        agents=SimpleNamespace(workers=[]),
        context=ContextSnapshot(
            revision="rev",
            source="assistx",
            providers=[],
            nodes=[],
            services=[ContextService(service_id="router", name="Router", url="http://localhost:8088", status="online")],
        ),
        model_registry=ModelRegistry(),
        event_outbox=EmptyOutbox(),
        cli_discovery=[{"name": "gemini-cli", "installed": True, "runnable": True}],
    )


def test_preflight_ready_when_production_basics_present() -> None:
    report = build_preflight_report(
        _state(),
        Settings(
            log_prompts=False,
            context_config="http://assistx:8000/api/router/context-projection",
            assistx_tasks_url="http://assistx:8000/api/router/backlog-candidates",
            assistx_event_sink_url="http://assistx:8000/api/events",
        ),
    )

    assert report["status"] == "ready"
    assert report["summary"]["failed"] == 0


def test_preflight_fails_when_prompt_logging_enabled() -> None:
    report = build_preflight_report(_state(), Settings(log_prompts=True))

    assert report["status"] == "not_ready"
    prompt_check = next(check for check in report["checks"] if check["name"] == "prompt_logging")
    assert prompt_check["status"] == "fail"


def test_preflight_warns_on_no_local_provider_and_dead_letter() -> None:
    state = _state()
    state.providers = _providers(local=False)
    state.event_outbox = DeadLetterOutbox()

    report = build_preflight_report(state, Settings(log_prompts=False))

    assert report["summary"]["warned"] >= 2
    provider_check = next(check for check in report["checks"] if check["name"] == "providers")
    outbox_check = next(check for check in report["checks"] if check["name"] == "outbox")
    assert provider_check["status"] == "warn"
    assert outbox_check["status"] == "warn"
