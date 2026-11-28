from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

import auto_router.main as main_module
from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.models import (
    ExecutionPlan,
    ExecutionStage,
    ModelConfig,
    PolicyProfile,
    PolicyStage,
    ProviderCandidate,
    ProviderConfig,
    RouterRequest,
    StagePurpose,
)
from auto_router.policy import PolicyEngine
from auto_router.providers import ProviderError


class FakeCircuits:
    def __init__(self) -> None:
        self.failures: list[tuple[str, str, int | None]] = []

    def allowed(self, owner: str) -> bool:
        return True

    def record_failure(self, owner: str, error: str, retry_after: int | None = None) -> None:
        self.failures.append((owner, error, retry_after))


class FakeQuota:
    def estimate(self, model: object, body: object) -> object:
        return SimpleNamespace(dimensions={})

    def reserve(self, provider: object, model: object, estimate: object) -> bool:
        return True

    def release(self, provider: object, model: object, estimate: object) -> None:
        return None


def _candidate() -> ProviderCandidate:
    provider = ProviderConfig(
        name="npu-xwing-qwen-0.5b",
        type="openai_compatible",
        node_id="xwing",
        runtime_instance_id="xwing-npu-qwen2.5-0.5b",
        parallel_slots=1,
        base_url="http://host.docker.internal:1236/v1",
        quota_class="local",
        models=[
            ModelConfig(
                alias="local/xwing-npu-fast",
                provider_model="xwing-npu-qwen2.5-0.5b",
                capabilities={"chat"},
            )
        ],
    )
    return ProviderCandidate(provider=provider, model=provider.models[0])


@pytest.mark.parametrize(
    ("status_code", "expected_failures"),
    [(429, 0), (503, 1)],
)
def test_provider_error_circuit_accounting(
    monkeypatch,
    status_code: int,
    expected_failures: int,
) -> None:
    """429 admission rejects are back-pressure, not outages; real errors still trip."""
    circuits = FakeCircuits()
    monkeypatch.setattr(
        main_module,
        "state",
        SimpleNamespace(
            circuits=circuits,
            quota=FakeQuota(),
            context=SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(main_module, "_candidate_allowed", lambda *args: True)
    monkeypatch.setattr(main_module, "enqueue_route_decision_event", lambda *args, **kwargs: "")
    monkeypatch.setattr(main_module, "_record_usage", lambda *args, **kwargs: None)
    error = ProviderError("admission rejected", status_code=status_code)

    async def reject(*args, **kwargs) -> None:
        raise error

    # Patch dispatch itself: importing main_live elsewhere in the suite installs
    # an admission wrapper around main._dispatch, which would mask the error.
    monkeypatch.setattr(main_module, "_dispatch", reject)

    candidate = _candidate()
    plan = ExecutionPlan(
        profile_name="interactive_balanced",
        stages=[
            ExecutionStage(
                purpose=StagePurpose.final,
                candidates=[candidate],
            )
        ],
    )
    policy_engine = SimpleNamespace(
        mark_inflight_start=lambda owner: None,
        mark_inflight_end=lambda owner: None,
    )
    request = RouterRequest(request_id="rate-limit-1", route="chat_completions")

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            main_module._execute_plan(request, plan, policy_engine, lambda index: None)
        )

    assert raised.value.status_code == 503
    assert len(circuits.failures) == expected_failures
    if expected_failures:
        assert circuits.failures[0][0] == "npu-xwing-qwen-0.5b/local/xwing-npu-fast"


def _two_stage_engine() -> PolicyEngine:
    model = ModelConfig(
        alias="local/worker",
        provider_model="worker-model",
        capabilities={"chat"},
    )
    provider = ProviderConfig(
        name="local-worker",
        type="lmstudio",
        base_url="http://localhost:1234/v1",
        quota_class="local",
        models=[model],
    )
    policies = PolicyRegistry(
        profiles={
            "interactive_balanced": PolicyProfile(
                stages=[
                    PolicyStage(
                        purpose=StagePurpose.draft,
                        provider_classes=["local"],
                        required_capabilities={"chat"},
                    ),
                    PolicyStage(
                        purpose=StagePurpose.final,
                        provider_classes=["local"],
                        required_capabilities={"chat"},
                    ),
                ]
            )
        }
    )
    return PolicyEngine(
        ProviderRegistry(providers=[provider]),
        policies,
        "interactive_balanced",
    )


@pytest.mark.parametrize("fail", [False, True])
def test_all_stage_reservations_released_on_early_completion(
    monkeypatch,
    fail: bool,
) -> None:
    """The planner reserves every stage; finishing early must release them all."""
    engine = _two_stage_engine()
    monkeypatch.setattr(main_module, "_fleet_health_map", lambda: {})
    monkeypatch.setattr(main_module, "state", SimpleNamespace(policy_engine=engine))

    observed: dict[str, int] = {}

    async def fake_execute_plan(router_request, plan, policy_engine, release):
        del router_request, plan, release
        observed["reserved"] = sum(policy_engine._lb_planned.values())
        # Simulate completion in the first stage: intentionally do not call the
        # stage release callback for either stage.
        if fail:
            raise HTTPException(status_code=503, detail="all providers failed")
        return JSONResponse({"ok": True})

    monkeypatch.setattr(main_module, "_execute_plan", fake_execute_plan)
    request = RouterRequest(request_id="early-1", route="chat_completions")

    if fail:
        with pytest.raises(HTTPException):
            asyncio.run(main_module._execute(request))
    else:
        response = asyncio.run(main_module._execute(request))
        assert response.status_code == 200

    assert observed["reserved"] == 2
    assert not any(engine._lb_planned.values())


def test_cancelled_dispatch_releases_inflight_and_quota(monkeypatch) -> None:
    events: list[str] = []

    class RecordingQuota(FakeQuota):
        def release(self, provider, model, estimate) -> None:  # noqa: ANN001
            events.append("release")

    circuits = FakeCircuits()
    monkeypatch.setattr(
        main_module,
        "state",
        SimpleNamespace(circuits=circuits, quota=RecordingQuota(), context=SimpleNamespace()),
    )
    monkeypatch.setattr(main_module, "_candidate_allowed", lambda *args: True)
    monkeypatch.setattr(main_module, "enqueue_route_decision_event", lambda *args, **kwargs: "")

    async def cancel(*args, **kwargs) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(main_module, "_dispatch", cancel)

    candidate = _candidate()
    plan = ExecutionPlan(
        profile_name="interactive_balanced",
        stages=[ExecutionStage(purpose=StagePurpose.final, candidates=[candidate])],
    )
    policy_engine = SimpleNamespace(
        mark_inflight_start=lambda owner: events.append("start"),
        mark_inflight_end=lambda owner: events.append("end"),
    )
    request = RouterRequest(request_id="cancelled-1", route="chat_completions")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            main_module._execute_plan(request, plan, policy_engine, lambda index: None)
        )

    assert events == ["start", "end", "release"]
    assert circuits.failures == []
