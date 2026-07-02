from __future__ import annotations

from types import SimpleNamespace

from auto_router import task_sourcer


class _FakeRunResult:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def single(self) -> dict[str, object]:
        return self._row


class _FakeSession:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def run(self, query: str, params: dict[str, object]) -> _FakeRunResult:
        self.calls.append((query, params))
        return _FakeRunResult(self.row)


class _FakeDriver:
    def __init__(self, row: dict[str, object]) -> None:
        self.session_obj = _FakeSession(row)
        self.closed = False

    def session(self) -> _FakeSession:
        return self.session_obj

    def close(self) -> None:
        self.closed = True


def test_get_batch_tasks_claims_neo4j_before_filling_other_sources(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_claim(limit: int, claimer: str = "fleet_task_dispatcher") -> list[dict[str, object]]:
        calls.append((f"claim:{claimer}", limit))
        return [{"source": "neo4j", "task_id": "n-1", "title": "neo-1", "prompt": "n1", "task_kind": "general", "requires_tools": False, "evidence_required": False, "capability_lane": "prompt_only"}]

    def fake_vault(limit: int) -> list[dict[str, object]]:
        calls.append(("vault", limit))
        return [{"source": "vault", "task_id": "v-1", "title": "vault-1", "prompt": "v1", "task_kind": "refinement", "requires_tools": False, "evidence_required": True, "capability_lane": "prompt_only"}]

    def fake_generated(limit: int) -> list[dict[str, object]]:
        calls.append(("generated", limit))
        return [{"source": "generated", "task_id": "g-1", "title": "gen-1", "prompt": "g1", "task_kind": "analysis", "requires_tools": True, "evidence_required": True, "capability_lane": "tool_required"}]

    monkeypatch.setattr(task_sourcer, "claim_tasks_from_neo4j", fake_claim)
    monkeypatch.setattr(task_sourcer, "get_vault_tasks", fake_vault)
    monkeypatch.setattr(task_sourcer, "generate_task_ideas", fake_generated)

    tasks = task_sourcer.get_batch_tasks(3)

    assert [task["source"] for task in tasks] == ["neo4j", "vault", "generated"]
    assert tasks[0]["task_kind"] == "general"
    assert tasks[1]["task_kind"] == "refinement"
    assert tasks[2]["requires_tools"] is True
    assert calls == [("claim:fleet_task_dispatcher", 3), ("vault", 2), ("generated", 1)]


def test_get_vault_tasks_includes_file_contents(monkeypatch, tmp_path) -> None:
    vault = tmp_path / "vault-workspace"
    (vault / "notes").mkdir(parents=True)
    md_file = vault / "notes" / "sample.md"
    md_file.write_text(
        "# Title\n\n" + "This is the body of the file. " * 5 + "\n",
        encoding="utf-8",
    )
    fake_module = tmp_path / "repo" / "src" / "auto_router" / "task_sourcer.py"
    fake_module.parent.mkdir(parents=True, exist_ok=True)
    fake_module.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("LM_FLEET_VAULT_WORKSPACE", str(vault))
    monkeypatch.setattr(task_sourcer, "__file__", str(fake_module))

    tasks = task_sourcer.get_vault_tasks(limit=1)

    assert tasks
    task = tasks[0]
    assert "This is the body of the file." in task["prompt"]
    assert str(md_file) in task["prompt"]
    assert task["context_note"].startswith("File path: ")
    assert "This is the body of the file." in task["context_note"]


def test_get_vault_tasks_skips_generated_task_artifacts(monkeypatch, tmp_path) -> None:
    vault = tmp_path / "vault-workspace"
    task_dir = vault / "tasks"
    task_dir.mkdir(parents=True)
    artifact = task_dir / "generated-task.md"
    artifact.write_text(
        "# Task: Review and refine this markdown file. Use the full file contents below, not just\n\n"
        "## Node: xwing\n"
        "## Model: vibethinker-3b\n\n"
        "SOURCE CONTEXT START\n"
        "This is clearly a prompt wrapper rather than source material.\n"
        "SOURCE CONTEXT END\n",
        encoding="utf-8",
    )
    fake_module = tmp_path / "repo" / "src" / "auto_router" / "task_sourcer.py"
    fake_module.parent.mkdir(parents=True, exist_ok=True)
    fake_module.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("LM_FLEET_VAULT_WORKSPACE", str(vault))
    monkeypatch.setattr(task_sourcer, "__file__", str(fake_module))

    tasks = task_sourcer.get_vault_tasks(limit=10)

    assert tasks == []


def test_task_contract_includes_handoff_guidance() -> None:
    contract: dict[str, object] = task_sourcer._task_contract({"task_kind": "review", "finalized": True})
    plan_steps = contract["plan_steps"]
    validation_metrics = contract["validation_metrics"]
    review_checkpoints = contract["review_checkpoints"]

    assert contract["workflow_stage"] == "handoff"
    assert isinstance(plan_steps, list)
    assert isinstance(validation_metrics, list)
    assert isinstance(review_checkpoints, list)
    assert plan_steps[0] == "Inspect the current state one slice at a time."
    assert validation_metrics == ["acceptance_criteria_met", "regressions_checked", "handoff_ready"]
    assert review_checkpoints[-1] == "final handoff approved"


def test_complete_task_in_neo4j_writes_metadata_only(monkeypatch) -> None:
    row = {"id": "task-1", "status": "DONE", "props": {"id": "task-1"}}
    driver = _FakeDriver(row)
    monkeypatch.setattr(task_sourcer, "get_neo4j_driver", lambda: driver)

    result = task_sourcer.complete_task_in_neo4j(
        "task-1",
        status="DONE",
        completed_by="xwing",
        completed_node="xwing",
        completed_model="ornith-1.0-35b",
        stage="review",
        response_path="/vault/tasks/task-1.md",
        draft_response_path="/vault/tasks/task-1-draft.md",
        final_response_path="/vault/tasks/task-1.md",
        input_tokens=120,
        output_tokens=240,
        latency_ms=18.5,
        quality_score=0.92,
        response_chars=1536,
        claimed_by="xwing",
        completion_source="fleet_task_dispatcher_service",
        task_kind="analysis",
        requires_tools=True,
        evidence_required=True,
        capability_lane="tool_required",
        workflow_stage="handoff",
        plan_steps=["Inspect", "Validate"],
        validation_metrics=["accepted"],
        review_checkpoints=["final"],
        evidence_bundle={"summary": "test evidence"},
        outcome_state="accepted",
        outcome_reason=None,
        queue_wait_ms=22.5,
        dispatch_latency_ms=18.5,
    )

    assert driver.closed is True
    assert result == {"id": "task-1", "status": "DONE", "props": {"id": "task-1"}}

    query, params = driver.session_obj.calls[0]
    assert "completed_at" in query
    assert "last_result_at" in query
    assert params["task_id"] == "task-1"
    assert params["status"] == "DONE"
    assert params["response_path"] == "/vault/tasks/task-1.md"
    assert params["draft_response_path"] == "/vault/tasks/task-1-draft.md"
    assert params["final_response_path"] == "/vault/tasks/task-1.md"
    assert params["completion_source"] == "fleet_task_dispatcher_service"
    assert params["task_kind"] == "analysis"
    assert params["requires_tools"] is True
    assert params["evidence_required"] is True
    assert params["capability_lane"] == "tool_required"
    assert params["workflow_stage"] == "handoff"
    assert params["plan_steps"] == ["Inspect", "Validate"]
    assert params["validation_metrics"] == ["accepted"]
    assert params["review_checkpoints"] == ["final"]
    assert params["outcome_state"] == "accepted"
    assert params["queue_wait_ms"] == 22.5
    assert "response_text" not in params
