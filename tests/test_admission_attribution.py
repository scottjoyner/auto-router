import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from auto_router import main as m
from auto_router.circuit_breaker import CircuitBreakerManager
from auto_router.models import RouterRequest


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["zero", "queue", "upstream", "mixed"])
def test_local_admission_rejection_not_provider_failure(monkeypatch, stream, kind):
    from auto_router.admission import RuntimeAdmissionController, RuntimeAdmissionQueueFull
    from auto_router.providers import ProviderError
    from auto_router.models import ProviderConfig, ProviderCandidate, ModelConfig
    a=ProviderConfig(name="first",type="lmstudio",base_url="http://example.invalid",runtime_instance_id="r1",parallel_slots=0,models=[ModelConfig(alias="model",provider_model="model")]); b=a.model_copy(update={"name":"second","runtime_instance_id":"r2"})
    candidates=[ProviderCandidate(provider=p,model=p.models[0]) for p in (a,b)]
    controller=RuntimeAdmissionController([a,b]); calls=[]
    stage=NS(candidates=candidates,purpose=NS(value="test"),optional=False)
    policy=NS(plan=lambda r:NS(profile_name="test",stages=[stage]),mark_planned_end=lambda o:None,mark_inflight_start=lambda o:calls.append(("start",o)),mark_inflight_end=lambda o:calls.append(("end",o)))
    quota=NS(estimate=lambda *a:1,reserve=lambda *a:True,release=lambda *a:calls.append(("release",a[0].name)))
    state=NS(policy_engine=policy,context=None,quota=quota,circuits=CircuitBreakerManager())
    monkeypatch.setattr(m,"state",state)
    for name in ("_fleet_health_map","_apply_power_tiers_to_policy","enqueue_route_decision_event","build_provider","_gateway_route_context"):
        monkeypatch.setattr(m,name,lambda *a,**k:None)
    monkeypatch.setattr(m,"_candidate_allowed",lambda *a:True)
    monkeypatch.setattr(m,"_record_usage",lambda *a,**k: calls.append(("fabricated_usage",)))
    async def rejected(_provider,candidate,*args,**kwargs):
        if kind == "queue" or (kind == "mixed" and candidate.provider.name == "first"):
            raise RuntimeAdmissionQueueFull("queue full", status_code=429)
        if kind in {"upstream", "mixed"}:
            raise ProviderError("upstream rate limit", status_code=429)
        await controller.acquire(candidate)
    monkeypatch.setattr(m,"_dispatch",rejected); monkeypatch.setattr(m,"_dispatch_stream",rejected)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m._execute(RouterRequest(request_id="test",route="chat_completions",stream=stream)))
    if kind in {"zero", "queue"}:
        assert state.circuits.snapshot()==[]
        assert calls==[("start","first/model"),("end","first/model"),("release","first"),("start","second/model"),("end","second/model"),("release","second")]
        assert state.runtime_admission_rejections==2
    else:
        assert len(state.circuits.snapshot()) == (2 if kind == "upstream" else 1)
        assert calls.count(("fabricated_usage",)) == (2 if kind == "upstream" else 1)
    if kind == "queue":
        assert exc.value.status_code==429
        assert exc.value.headers=={"Retry-After":"2"}
        assert state.runtime_admission_queue_rejections==2
    else:
        assert exc.value.status_code==503
