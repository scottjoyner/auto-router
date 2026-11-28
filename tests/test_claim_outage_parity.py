import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from auto_router import claim_fence as f
from auto_router import main as m
from auto_router.circuit_breaker import CircuitBreakerManager
from auto_router.models import RouterRequest


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["revoked", "outage", "cooldown"])
def test_authority_rejection_is_not_provider_failure(monkeypatch, stream, kind):
    calls = []
    candidates = [NS(provider=NS(name=n), model=NS(alias="model", provider_model="model"))
                  for n in ("first", "second")]
    stage = NS(candidates=candidates, purpose=NS(value="test"), optional=False)
    policy = NS(plan=lambda r: NS(profile_name="test", stages=[stage]),
                mark_planned_end=lambda o: None,
                mark_inflight_start=lambda o: calls.append(("start", o)),
                mark_inflight_end=lambda o: calls.append(("end", o)))
    quota = NS(estimate=lambda *a: 1, reserve=lambda *a: True,
               release=lambda *a: calls.append(("release", a[0].name)))
    state = NS(policy_engine=policy, context=None, quota=quota, circuits=CircuitBreakerManager())
    monkeypatch.setattr(m, "state", state)
    for name in ("_fleet_health_map", "_apply_power_tiers_to_policy", "enqueue_route_decision_event",
                 "build_provider", "_gateway_route_context"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m, "_candidate_allowed", lambda *a: True)
    monkeypatch.setattr(m, "_record_usage", lambda *a, **k: calls.append(("usage",)))
    if kind == "cooldown" and hasattr(f, "ExecutorClaimStatusCoolingDown"):
        error = f.ExecutorClaimStatusCoolingDown(3)
    elif kind == "outage":
        error = getattr(f, "ExecutorClaimStatusUnavailable", f.ExecutorClaimFenceError)("private diagnostic")
    else:
        error = f.ExecutorClaimFenceError("private diagnostic")

    async def rejected(*args, **kwargs):
        raise error

    monkeypatch.setattr(m, "_dispatch", rejected)
    monkeypatch.setattr(m, "_dispatch_stream", rejected)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m._execute(RouterRequest(request_id="test", route="chat_completions", stream=stream)))
    assert state.circuits.snapshot() == []
    assert calls == [("start", "first/model"), ("end", "first/model"), ("release", "first")]
    assert exc.value.status_code == 503
    assert exc.value.detail == {"error": "executor_claim_unavailable" if kind == "revoked" else "executor_claim_status_unavailable"}
    assert state.claim_fence_rejections == 1
    assert hasattr(state, "claim_status_last_failure") == (kind == "outage")
    assert exc.value.headers == (None if kind == "revoked" else {"Retry-After": "3" if kind == "cooldown" else "5"})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_outage_cooldown_never_caches_success(monkeypatch, status):
    clock = [100.0]
    monkeypatch.setattr(f, "time", NS(monotonic=lambda: clock[0], time=lambda: 1000.0))
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL", "http://assistx.test")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_TOKEN", "synthetic-token")
    calls = []
    payload = {"active": True, "task_id": "task", "claim_id": "claim", "agent_id": "agent",
               "projection_generation": 9, "lease_expires_at_ts": 2000000}
    responses = [status, 200, 200]

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            calls.append(True)
            return NS(status_code=responses.pop(0), json=lambda: payload)

    monkeypatch.setattr(f.httpx, "AsyncClient", Client)
    state = NS(runtime_projection_manager=NS(current=NS(generation=9, is_fresh=lambda: True)))
    request = NS(metadata={"assistx_executor": {"task_id": "task", "claim_id": "claim",
                 "agent_id": "agent", "projection_generation": 9}})
    with pytest.raises(f.ExecutorClaimFenceError):
        await f.assert_executor_claim_current(request, state)
    with pytest.raises(f.ExecutorClaimFenceError):
        await f.assert_executor_claim_current(request, state)
    assert len(calls) == 1
    assert state.claim_status_checks_suppressed == 1
    clock[0] += 6
    await f.assert_executor_claim_current(request, state)
    await f.assert_executor_claim_current(request, state)
    assert len(calls) == 3
