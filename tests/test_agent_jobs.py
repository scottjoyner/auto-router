from auto_router.agent_jobs import AgentJobManager, build_agent_job_request
from auto_router.models import AgentWorkerConfig


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
