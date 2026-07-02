import httpx
import pytest

from auto_router.assistx_tasks import AssistXTaskClient, normalize_assistx_task
from auto_router.models import Priority


def test_normalize_assistx_task_preserves_privacy_flags() -> None:
    task = normalize_assistx_task(
        {
            "id": "task-1",
            "title": "Private task",
            "description": "Do private work",
            "priority": "background",
            "privacy": "private",
            "metadata": {"model": "auto/backlog-burn"},
        }
    )

    assert task.task_id == "task-1"
    assert task.priority == Priority.background
    assert task.local_only is True
    assert task.sensitive is True
    assert task.allow_cloud is False


def test_normalize_assistx_task_maps_normal_priority_to_batch() -> None:
    task = normalize_assistx_task(
        {
            "task_id": "task-2",
            "name": "Normal task",
            "body": "Do docs",
            "priority": "normal",
        }
    )

    assert task.priority == Priority.batch
    assert task.queue_class == "batch"
    assert task.sensitive is False
    assert task.local_only is False


@pytest.mark.parametrize(
    "priority,expected,queue_class",
    [
        ("high", Priority.critical, "critical"),
        ("critical", Priority.critical, "critical"),
        ("repo_critical", Priority.repo_critical, "critical"),
        ("interactive", Priority.interactive, "interactive"),
        ("local_only", Priority.local_only, "background"),
    ],
)
def test_normalize_assistx_task_preserves_high_priority_values(priority, expected, queue_class) -> None:
    task = normalize_assistx_task(
        {
            "task_id": f"task-{priority}",
            "title": "Priority task",
            "description": "Do important work",
            "priority": priority,
        }
    )

    assert task.priority == expected
    assert task.queue_class == queue_class


@pytest.mark.asyncio
async def test_assistx_task_client_fetches_tasks(monkeypatch) -> None:
    async def fake_get(self, url, params):
        assert params["dry_run"] == "true"
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {"id": "a", "title": "A", "description": "Alpha"},
                    {"id": "b", "title": "B", "description": "Beta"},
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    client = AssistXTaskClient("http://assistx.test/api/tasks")

    tasks = await client.fetch_backlog_candidates(limit=1)

    assert len(tasks) == 1
    assert tasks[0].task_id == "a"
    assert tasks[0].metadata["assistx_source"] is True


@pytest.mark.asyncio
async def test_assistx_task_client_requires_config() -> None:
    client = AssistXTaskClient(None)

    with pytest.raises(RuntimeError):
        await client.fetch_backlog_candidates()
