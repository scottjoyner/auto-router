from types import SimpleNamespace

from auto_router.backlog_scheduler import (
    BacklogDryRunRequest,
    BacklogTaskCandidate,
    backlog_summary,
    dry_run_backlog_selection,
)
from auto_router.config import PolicyRegistry, ProviderRegistry
from auto_router.context import ContextSnapshot
from auto_router.event_outbox import EventOutbox
from auto_router.models import ModelConfig, PolicyProfile, PolicyStage, Priority, ProviderConfig, StagePurpose
from auto_router.policy import PolicyEngine
from auto_router.quota import InMemoryQuotaManager


def _policy_engine() -> PolicyEngine:
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
    providers = ProviderRegistry(providers=[provider])
    policies = PolicyRegistry(
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
    )
    return PolicyEngine(providers, policies, "backlog_burn", ContextSnapshot())


def test_dry_run_backlog_selection_selects_safe_task(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    request = BacklogDryRunRequest(
        tasks=[BacklogTaskCandidate(title="Summarize docs", prompt="Summarize the design docs")]
    )

    decisions = dry_run_backlog_selection(
        request,
        policy_engine=_policy_engine(),
        quota=InMemoryQuotaManager(),
        outbox=outbox,
        context=ContextSnapshot(revision="rev-backlog", source="unit-test"),
    )

    assert decisions[0].status == "selected"
    assert decisions[0].provider == "cerebras"
    assert decisions[0].event_id is not None
    events = outbox.pending()
    assert events[0]["event_type"] == "router.backlog_job.selected"
    assert events[0]["payload"]["dry_run"] is True


def test_dry_run_backlog_selection_skips_sensitive_and_local_only(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    request = BacklogDryRunRequest(
        tasks=[
            BacklogTaskCandidate(title="Sensitive", sensitive=True),
            BacklogTaskCandidate(title="Local only", local_only=True),
        ]
    )

    decisions = dry_run_backlog_selection(
        request,
        policy_engine=_policy_engine(),
        quota=InMemoryQuotaManager(),
        outbox=outbox,
        context=ContextSnapshot(),
    )

    assert [decision.status for decision in decisions] == ["skipped", "skipped"]
    assert "sensitive" in decisions[0].reason
    assert "local-only" in decisions[1].reason
    assert all(event["event_type"] == "router.backlog_job.skipped" for event in outbox.pending())


def test_dry_run_backlog_selection_skips_interactive_priority() -> None:
    request = BacklogDryRunRequest(
        tasks=[BacklogTaskCandidate(title="Interactive", priority=Priority.interactive, queue_class="interactive")]
    )

    decisions = dry_run_backlog_selection(
        request,
        policy_engine=_policy_engine(),
        quota=InMemoryQuotaManager(),
    )

    assert decisions[0].status == "skipped"
    assert "batch/background" in decisions[0].reason


def test_dry_run_backlog_selection_skips_non_backlog_queue_class() -> None:
    request = BacklogDryRunRequest(
        tasks=[BacklogTaskCandidate(title="Critical", priority=Priority.background, queue_class="critical")]
    )

    decisions = dry_run_backlog_selection(
        request,
        policy_engine=_policy_engine(),
        quota=InMemoryQuotaManager(),
    )

    assert decisions[0].status == "skipped"
    assert "queue_class=critical" in decisions[0].reason


def test_backlog_summary_counts_decisions() -> None:
    decisions = dry_run_backlog_selection(
        BacklogDryRunRequest(
            tasks=[
                BacklogTaskCandidate(title="A"),
                BacklogTaskCandidate(title="B", sensitive=True),
            ]
        ),
        policy_engine=_policy_engine(),
        quota=InMemoryQuotaManager(),
    )

    assert backlog_summary(decisions) == {"total": 2, "selected": 1, "skipped": 1}


def test_backlog_decision_event_is_metadata_only(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    request = BacklogDryRunRequest(
        tasks=[BacklogTaskCandidate(title="Document migration boundary", prompt="Do not include prompt bodies in events")]
    )

    decisions = dry_run_backlog_selection(
        request,
        policy_engine=_policy_engine(),
        quota=InMemoryQuotaManager(),
        outbox=outbox,
        context=ContextSnapshot(revision="rev-backlog", source="unit-test"),
    )

    event = outbox.pending()[0]
    payload = event["payload"]

    assert decisions[0].status in {"selected", "skipped"}
    assert event["event_type"] in {"router.backlog_job.selected", "router.backlog_job.skipped"}
    assert payload["task_id"] == decisions[0].task_id
    assert payload["title"] == "Document migration boundary"
    assert payload["dry_run"] is True
    assert payload["context_revision"] == "rev-backlog"
    assert payload["context_source"] == "unit-test"
    assert "prompt" not in payload
    assert "Do not include prompt bodies in events" not in str(payload)


def test_backlog_selection_can_enqueue_real_events(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    request = BacklogDryRunRequest(
        tasks=[BacklogTaskCandidate(title="Burn down backlog", prompt="Actually enqueue the backlog decision")]
    )

    decisions = dry_run_backlog_selection(
        request,
        policy_engine=_policy_engine(),
        quota=InMemoryQuotaManager(),
        outbox=outbox,
        context=ContextSnapshot(revision="rev-backlog", source="unit-test"),
        dry_run=False,
    )

    event = outbox.pending()[0]
    payload = event["payload"]

    assert decisions[0].status == "selected"
    assert payload["dry_run"] is False
