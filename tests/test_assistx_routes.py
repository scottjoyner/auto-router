from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router.assistx_routes import register_assistx_routes, _select_lane_and_provider, _build_route_decision
from auto_router.models import RouteIntent, RouteRequest


class FakeContext:
    pass


class FakeProvider:
    def __init__(self, name: str, quota_class: str = "local", base_url: str = "http://localhost:1234/v1") -> None:
        self.name = name
        self.quota_class = quota_class
        self.base_url = base_url
        self.node_id = f"node:{name}"
        self.models = [SimpleNamespace(alias=f"{name}/model", provider_model=f"{name}-model")]


class FakeProviders:
    def __init__(self) -> None:
        self._providers = [FakeProvider("local")]

    def enabled(self):
        return self._providers


class FakeAgentJobs:
    def __init__(self) -> None:
        self.submissions = []

    def submit(self, request):
        self.submissions.append(request)
        return SimpleNamespace(request=request)


def test_tool_capable_request_routes_to_agent_jobs() -> None:
    state = SimpleNamespace(context=FakeContext(), providers=FakeProviders(), agent_jobs=FakeAgentJobs())
    request = RouteRequest(
        correlation_id="corr-1",
        task_id="task-1",
        intent=RouteIntent(text="Research the tool-capable orchestration lane"),
        metadata={"task_kind": "research", "evidence_required": True},
    )

    selection = _select_lane_and_provider(request, state)

    assert selection["lane"] == "paperclip"
    assert selection["provider"] == "agent-jobs"
    assert selection["target_service"] == "/jobs/agent"
    assert selection["target_worker"] == "hermes"
    assert selection["job_request"].preferred_workers[0] == "hermes"
    assert selection["plan_steps"][0] == "Gather the relevant evidence or context." or selection["plan_steps"][0] == "Review the current state one slice at a time."


def test_finalized_request_routes_to_agent_jobs_with_handoff_metadata() -> None:
    state = SimpleNamespace(context=FakeContext(), providers=FakeProviders(), agent_jobs=FakeAgentJobs())
    request = RouteRequest(
        correlation_id="corr-1",
        task_id="task-1",
        intent=RouteIntent(text="Finalize the reviewed workflow"),
        metadata={"task_kind": "review", "finalized": True},
    )

    selection = _select_lane_and_provider(request, state)

    assert selection["lane"] == "paperclip"
    assert selection["job_request"].workflow_stage == "handoff"
    assert selection["job_request"].plan_steps
    assert selection["job_request"].validation_metrics


def test_route_decision_uses_router_route_decision_envelope() -> None:
    request = RouteRequest(correlation_id="corr-2", task_id="task-2")

    decision = _build_route_decision(
        request,
        lane="local",
        provider="lmstudio",
        model="local/default",
        rationale="defaulted to local",
    )

    assert decision["event_type"] == "router.route_decision"
    assert decision["status"] == "selected"
    assert decision["correlation_id"] == "corr-2"
    assert decision["task_id"] == "task-2"
