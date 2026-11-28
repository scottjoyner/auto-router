import asyncio
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from auto_router import main as m
from auto_router.admission import RuntimeAdmissionQueueFull
from auto_router.providers import ProviderError
from auto_router.circuit_breaker import CircuitBreakerManager
from auto_router.models import RouterRequest

@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("local_queue_full", [False, True])
def test_queue_saturation_is_not_provider_failure(monkeypatch, stream, local_queue_full):
    calls=[]
    candidate=NS(provider=NS(name="first"),model=NS(alias="model",provider_model="model"))
    stage=NS(candidates=[candidate],purpose=NS(value="test"),optional=False)
    policy=NS(plan=lambda r:NS(profile_name="test",stages=[stage]),mark_planned_end=lambda o:None,mark_inflight_start=lambda o:calls.append("start"),mark_inflight_end=lambda o:calls.append("end"))
    quota=NS(estimate=lambda *a:1,reserve=lambda *a:True,release=lambda *a:calls.append("release"))
    state=NS(policy_engine=policy,context=None,quota=quota,circuits=CircuitBreakerManager())
    monkeypatch.setattr(m,"state",state)
    monkeypatch.setattr(m,"_fleet_health_map",lambda:{})
    monkeypatch.setattr(m,"_apply_power_tiers_to_policy",lambda:None)
    monkeypatch.setattr(m,"_candidate_allowed",lambda *a:True)
    monkeypatch.setattr(m,"enqueue_route_decision_event",lambda *a,**k:None)
    monkeypatch.setattr(m,"build_provider",lambda *a,**k:None)
    monkeypatch.setattr(m,"_gateway_route_context",lambda *a:None)
    monkeypatch.setattr(m,"_record_usage",lambda *a,**k:calls.append("usage"))
    async def rejected(*args,**kwargs):
        cls=RuntimeAdmissionQueueFull if local_queue_full else ProviderError
        raise cls("capacity failure",status_code=429)
    monkeypatch.setattr(m,"_dispatch",rejected)
    monkeypatch.setattr(m,"_dispatch_stream",rejected)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m._execute(RouterRequest(request_id="test",route="chat_completions",stream=stream)))
    if local_queue_full:
        assert exc.value.status_code==429
        assert exc.value.headers=={"Retry-After":"2"}
        assert calls==["start","end","release"]
        assert state.circuits.snapshot()==[]
        assert state.runtime_admission_queue_rejections==1
    else:
        assert exc.value.status_code==503
        assert calls==["start","end","release","usage"]
        assert state.circuits.snapshot()[0]["failures"]==1
