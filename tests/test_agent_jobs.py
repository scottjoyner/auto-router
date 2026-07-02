import asyncio

from auto_router.agent_jobs import AgentJobManager, build_agent_job_request
from auto_router.agent_workers import AgentWorkerAdapter
from auto_router.models import AgentWorkerConfig


def test_build_agent_job_request_prefers_xwing_for_portfolio_management() -> None:
    request = build_agent_job_request(
        {
            "task": "Review the portfolio-management scheduling path",
            "repo_path": "/home/scott/git/portfolio-management",
            "metadata": {"repo_name": "portfolio-management"},
        }
    )

    assert request.preferred_workers[0] == "xwing"


def test_build_agent_job_request_prefers_codex_for_code_tasks() -> None:
    request = build_agent_job_request(
        {
            "task": "Fix the failing repo routing bug",
            "task_kind": "code",
            "metadata": {"repo_path": "/home/scott/git/auto-router", "allow_write": True},
        }
    )

    assert request.capability_lane == "tool_required"
    assert request.requires_tools is True
    assert request.preferred_workers[0] == "hermes"
    assert "codex" in request.preferred_workers
    assert request.allow_write is True
    assert request.enabled_toolsets == ["terminal", "file", "code_execution", "skills"]


def test_build_agent_job_request_prefers_gemini_for_research_tasks() -> None:
    request = build_agent_job_request(
        {
            "task": "Research the routing lane setup",
            "task_kind": "research",
            "metadata": {"evidence_required": True},
        }
    )

    assert request.preferred_workers[0] == "hermes"
    assert request.evidence_required is True
    assert request.plan_steps[0] == "Gather the relevant evidence or context."
    assert request.validation_metrics == ["evidence_captured", "claims_supported", "final_answer_ready"]
    assert request.enabled_toolsets[:3] == ["web", "browser", "file"]


def test_build_agent_job_request_handoff_prefers_finalize_workers() -> None:
    request = build_agent_job_request(
        {
            "task": "Finalize the reviewed routing workflow",
            "task_kind": "review",
            "workflow_stage": "handoff",
            "metadata": {"finalized": True},
        }
    )

    assert request.workflow_stage == "handoff"
    assert request.preferred_workers[:4] == ["hermes", "codex", "opencode", "gemini-cli"]
    assert request.review_checkpoints[0] == "reviewed by local iteration"
    assert request.validation_metrics[0] == "acceptance_criteria_met"
def test_agent_job_manager_reports_queued_and_404_safely(tmp_path) -> None:
    manager = AgentJobManager(
        [AgentWorkerConfig(name="noop", type="custom", command="definitely-not-installed", enabled=False)],
        base_dir=tmp_path,
    )
    request = build_agent_job_request({"task": "inspect this repo"})
    record = manager.submit(request)

    assert record.status == "queued"
    assert manager.get(record.request.job_id) is not None



def test_agent_job_manager_lists_records(tmp_path) -> None:
    manager = AgentJobManager(
        [AgentWorkerConfig(name="noop", type="custom", command="definitely-not-installed", enabled=False)],
        base_dir=tmp_path,
    )
    request = build_agent_job_request({"task": "inspect this repo"})
    manager.submit(request)

    assert len(manager.list_records()) == 1


def test_agent_worker_prompt_requests_coordination() -> None:
    adapter = AgentWorkerAdapter(
        AgentWorkerConfig(name="noop", type="custom", command="definitely-not-installed", enabled=False)
    )
    request = build_agent_job_request(
        {
            "task": "coordinate a local planning pass",
            "preferred_workers": ["xwing", "x1-370"],
            "context_note": "File path: /tmp/sample.md\n\n# Title\nbody text",
        }
    )
    assert request.metadata.get("context_note") == "File path: /tmp/sample.md\n\n# Title\nbody text"

    prompt = adapter._build_task_payload(request)

    assert "coordinated local-model team" in prompt
    assert "your assigned slice" in prompt.lower()
    assert "dependencies or handoff needed from peers" in prompt.lower()
    assert "enabled_toolsets" in prompt
    assert "SOURCE CONTEXT START" in prompt
    assert "body text" in prompt


def test_agent_worker_hermes_launcher_builds_oneshot_command(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            captured["input"] = input
            return b"stdout", b""

        def kill(self):
            captured["killed"] = True

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    adapter = AgentWorkerAdapter(
        AgentWorkerConfig(
            name="hermes",
            type="hermes",
            command="hermes",
            enabled=True,
            launcher="hermes_oneshot",
            model="vibethinker-3b-ablated-i1",
            provider="LM Studio",
            toolsets=["terminal", "file", "code_execution", "skills"],
            skills=["auto-router-ops"],
        )
    )
    request = build_agent_job_request({"task": "Fix the repo routing bug", "task_kind": "code"})

    result = asyncio.run(adapter.run(request, tmp_path))

    assert result.status == "succeeded"
    args = captured.get("args")
    kwargs = captured.get("kwargs")
    assert isinstance(args, tuple)
    assert isinstance(kwargs, dict)
    assert args[0] == "hermes"
    assert "-z" in args
    assert "-t" in args
    assert kwargs["cwd"] == str(tmp_path)
    assert "stdin" not in kwargs
    assert captured.get("input") is None
