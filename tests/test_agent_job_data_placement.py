"""Legacy agent-job artifacts must share the configured Router state volume.

All paths are synthetic; no real Docker volumes, NAS files or jobs are used.
"""
from __future__ import annotations

from pathlib import Path

from auto_router.agent_jobs import AgentJobManager
from auto_router.settings import Settings


def test_production_sqlite_data_volume_places_jobs_under_data_mount():
    settings = Settings(database_url="sqlite:////data/router.sqlite3")
    assert settings.agent_jobs_path == "/data/agent-jobs"
    assert not settings.agent_jobs_path.startswith("/app/")


def test_private_readonly_oci_staging_database_uses_private_tmpfs():
    settings = Settings(database_url="sqlite:////tmp/router-lifespan-acceptance.sqlite3")
    assert settings.agent_jobs_path == "/tmp/agent-jobs"


def test_local_relative_sqlite_keeps_historical_data_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(database_url="sqlite:///./data/router.sqlite3")
    assert Path(settings.agent_jobs_path) == tmp_path / "data" / "agent-jobs"


def test_empty_legacy_job_manager_initializes_only_its_configured_temp_directory(tmp_path):
    db_dir = tmp_path / "isolated-router-state"
    settings = Settings(database_url="sqlite:///" + str(db_dir / "router.sqlite3"))
    manager = AgentJobManager([], base_dir=settings.agent_jobs_path)
    assert manager.base_dir == db_dir / "agent-jobs"
    assert manager.base_dir.is_dir()
    assert list(manager.base_dir.iterdir()) == []


def test_router_load_state_uses_configured_job_directory():
    content = (Path(__file__).resolve().parents[1] / "src/auto_router/main.py").read_text()
    assert "base_dir=settings.agent_jobs_path" in content
