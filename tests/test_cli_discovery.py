from types import SimpleNamespace

from auto_router.cli_discovery import CliDiscoveryResult
from auto_router.cli_routes import cli_summary, enqueue_cli_discovery_events
from auto_router.context import ContextSnapshot
from auto_router.event_outbox import EventOutbox


def test_cli_summary_counts_results() -> None:
    results = [
        {"name": "codex", "installed": True, "runnable": False},
        {"name": "gemini-cli", "installed": True, "runnable": True},
        {"name": "opencode", "installed": False, "runnable": False},
    ]

    assert cli_summary(results) == {"total": 3, "installed": 2, "runnable": 1, "missing": 1}


def test_enqueue_cli_discovery_events(tmp_path) -> None:
    outbox = EventOutbox(f"sqlite:///{tmp_path / 'router.sqlite3'}")
    state = SimpleNamespace(
        event_outbox=outbox,
        context=ContextSnapshot(revision="rev-agent", source="unit-test"),
    )
    result = CliDiscoveryResult(
        name="gemini-cli",
        command="gemini",
        cli_type="gemini_cli",
        installed=True,
        runnable=True,
        path="/usr/local/bin/gemini",
        node_id="x1-370",
        checked_at=123,
        version="gemini 1.0.0",
        credit_hint="credits_remaining_expected",
    ).to_dict()

    event_ids = enqueue_cli_discovery_events(state, [result])
    events = outbox.pending()

    assert len(event_ids) == 1
    assert events[0]["event_type"] == "router.agent_cli.discovered"
    assert events[0]["payload"]["name"] == "gemini-cli"
    assert events[0]["payload"]["runnable"] is True
    assert events[0]["payload"]["context_revision"] == "rev-agent"
