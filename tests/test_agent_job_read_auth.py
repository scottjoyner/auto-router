"""Sensitive agent job record and artifacts require admin auth before lookup.

All job identities, credentials and stores are synthetic. Never enters
ASGI lifespan, starts workers, accesses real job records, or sends POST.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from auto_router.main import app, state
from auto_router import security
from auto_router.security import require_admin

TOKEN = "synthetic-only-operator-credential"
JOB = "synthetic-no-real-record"


class FakeJobs:
    def __init__(self):
        self.lookups = 0
        self.artifact_reads = 0

    def get(self, _job_id):
        self.lookups += 1
        return None

    def artifacts_for(self, _job_id):
        self.artifact_reads += 1
        raise AssertionError("no artifact retrieval should occur in this test")


@pytest.fixture()
def restricted(monkeypatch):
    fake = FakeJobs()
    monkeypatch.setattr(state, "agent_jobs", fake, raising=False)
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(admin_token=TOKEN))
    # No lifespan: synthetic handler/router check only.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, fake


READS = (
    "/jobs/agent/" + JOB,
    "/jobs/agent/" + JOB + "/artifacts",
)


@pytest.mark.parametrize("route", READS)
def test_job_read_routes_are_directly_protected(route):
    matching = [
        r for r in app.routes if isinstance(r, APIRoute)
        and "GET" in r.methods and r.path == route.replace(JOB, "{job_id}")
    ]
    assert len(matching) == 1
    assert any(dep.call is require_admin for dep in matching[0].dependant.dependencies)


@pytest.mark.parametrize("route", READS)
@pytest.mark.parametrize("mode", ("anonymous", "wrong", "bearer_wrong", "basic_wrong"))
def test_no_job_lookup_when_missing_or_invalid_credential(restricted, route, mode):
    client, jobs = restricted
    headers = {
        "anonymous": {},
        "wrong": {"X-Admin-Token": "incorrect"},
        "bearer_wrong": {"Authorization": "Bearer incorrect"},
        "basic_wrong": {"Authorization": "Basic YWRtaW46aW5jb3JyZWN0"},
    }[mode]
    assert client.get(route, headers=headers).status_code == 401
    assert jobs.lookups == 0
    assert jobs.artifact_reads == 0


@pytest.mark.parametrize("route", READS)
def test_authorized_missing_job_is_404_without_data(restricted, route):
    client, jobs = restricted
    assert client.get(route, headers={"X-Admin-Token": TOKEN}).status_code == 404
    assert jobs.lookups == 1
    assert jobs.artifact_reads == 0


@pytest.mark.parametrize("route", READS)
def test_unconfigured_auth_always_denies_even_correct_old_header(restricted, monkeypatch, route):
    client, jobs = restricted
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(admin_token=""))
    assert client.get(route, headers={"X-Admin-Token": TOKEN}).status_code == 401
    assert jobs.lookups == 0
    assert jobs.artifact_reads == 0


def test_unrelated_browsable_status_route_is_not_modified():
    matching = [
        r for r in app.routes if isinstance(r, APIRoute)
        and "GET" in r.methods and r.path == "/health"
    ]
    assert len(matching) == 1
    assert not any(d.call is require_admin for d in matching[0].dependant.dependencies)
