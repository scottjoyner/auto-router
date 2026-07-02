from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router.backlog_routes import register_backlog_routes
from auto_router.backlog_scheduler import BacklogTaskCandidate
from auto_router.context import ContextSnapshot
from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.event_outbox import EventOutbox
from auto_router.models import ModelConfig, PolicyProfile, PolicyStage, ProviderConfig, StagePurpose
from auto_router.policy import PolicyEngine
from auto_router.quota import InMemoryQuotaManager

class _FakePolicyState:
    def __init__(self, event_outbox: EventOutbox) -> None:
        provider = ProviderConfig(
            name="cerebras",
            type="openai_compatible",
            base_url="https://api.cerebras.ai/v1",
            quota_class="fast_free",
            priority=10,
            models=[
                ModelConfig(
                    alias="cerebras/flash-reasoner",
                    provider_model="gpt-oss-120b",
                    capabilities={"chat", "flash_planning", "low_latency"},
                    quota={"rpd": 100, "tpd": 1000000},
                )
            ],
        )
        self.policy_engine = PolicyEngine(
            ProviderRegistry(providers=[provider]),
            PolicyRegistry(
                profiles={
                    "backlog_burn": PolicyProfile(
                        description="test backlog",
                        stages=[
                            PolicyStage(
                                purpose=StagePurpose.draft,
                                provider_classes=["fast_free"],
                                required_capabilities={"chat"},
                            )
                        ],
                    )
                }
            ),
            "backlog_burn",
            ContextSnapshot(),
        )
        self.quota = InMemoryQuotaManager()
        self.context = ContextSnapshot(revision="rev-backlog", source="unit-test")
        self.event_outbox = event_outbox


def test_backlog_burn_down_uses_real_dispatch_path(monkeypatch, tmp_path) -> None:
    state = _FakePolicyState(EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}"))
    app = FastAPI()
    register_backlog_routes(app, state)
    client = TestClient(app)

    async def fake_fetch(self, limit: int = 25, queue: str = "backlog", dry_run: bool = True):
        assert limit == 1
        assert queue == "backlog"
        assert dry_run is False
        return [
            BacklogTaskCandidate(
                task_id="burn-1",
                title="Burn down backlog",
                prompt="Actually enqueue the backlog decision",
            )
        ]

    async def fake_dispatch(state_arg, limit: int = 25, dry_run: bool = False, reason: str = "scheduled"):
        assert state_arg is state
        assert limit == 1
        assert dry_run is False
        assert reason == "manual-backlog-burn-down"
        return {"summary": {"pending": 0, "retry": 0, "delivered": 1, "dead_letter": 0, "total": 1}, "results": []}

    monkeypatch.setattr("auto_router.backlog_routes.AssistXTaskClient.fetch_backlog_candidates", fake_fetch)
    monkeypatch.setattr("auto_router.backlog_routes.dispatch_outbox_cycle", fake_dispatch)

    response = client.post(
        "/admin/backlog/burn-down?source=assistx&limit=1&dispatch_limit=1",
        json={"tasks": []},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assistx"] == {"configured": True, "fetched": 1, "queue": "backlog", "limit": 1}
    assert body["summary"] == {"total": 1, "selected": 1, "skipped": 0}
    assert body["decisions"][0]["task_id"] == "burn-1"
    assert body["decisions"][0]["status"] == "selected"
    assert body["decisions"][0]["event_id"] is not None
    assert body["decisions"][0]["reason"] == "candidate available without reserving quota"
    assert body["dispatch"]["summary"]["delivered"] == 1
