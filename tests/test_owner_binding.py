"""Deny-only owner contract — never infer a human principal from request JSON."""
from __future__ import annotations

from types import SimpleNamespace
import time

import pytest

from auto_router.owner_binding import (
    OwnerBindingError, assert_claim_owner, bound_principal, owner_binding_required,
)
from auto_router import claim_fence
from auto_router.claim_fence import ExecutorClaimFenceError, assert_executor_claim_current


def _principal(owner="person-a", *,verified=True):
    return {"owner_issuer":"trusted-idp","owner_subject":owner,"authenticated":verified}


def _signed(owner="person-a"):
    return {"owner_issuer":"trusted-idp","owner_subject":owner}


def _scope(principal=None):
    return {"state":{"verified_owner": principal or _principal()}}


@pytest.fixture(autouse=True)
def require_owner(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")


def test_feature_is_explicitly_on():
    assert owner_binding_required() is True


def test_signed_identity_matches_trusted_scope():
    assert bound_principal(_scope(),_signed()) == _signed()


def test_signed_owner_cannot_be_swapped():
    with pytest.raises(OwnerBindingError,match="differs"):
        bound_principal(_scope(),_signed("person-b"))


def test_missing_trusted_principal_denied():
    with pytest.raises(OwnerBindingError,match="unavailable"):
        bound_principal({"state":{}},_signed())


def test_unverified_trusted_principal_denied():
    with pytest.raises(OwnerBindingError,match="unavailable"):
        bound_principal(_scope(_principal(verified=False)),_signed())


def test_request_supplied_identity_ignored():
    scope={"headers":[(b"x-owner-id",b"person-a")],"state":{}}
    with pytest.raises(OwnerBindingError,match="unavailable"):
        bound_principal(scope,_signed())


def test_signed_token_missing_owner_denied():
    with pytest.raises(OwnerBindingError,match="absent"):
        bound_principal(_scope(),{})


@pytest.mark.parametrize("value",["", " person-a", "person b", "x"*161, None, 123, True])
def test_malformed_owner_denied(value):
    with pytest.raises(OwnerBindingError):
        bound_principal(_scope(),{"owner_issuer":"trusted-idp","owner_subject":value})


def test_claim_status_owner_matched():
    assert_claim_owner(_signed(),_signed())


def test_claim_status_owner_swapped_denied():
    with pytest.raises(OwnerBindingError,match="no longer"):
        assert_claim_owner(_signed(),_signed("person-b"))


def test_claim_status_owner_missing_denied():
    with pytest.raises(OwnerBindingError,match="absent"):
        assert_claim_owner(_signed(),{})


def test_flag_disabled_is_backwards_compatible(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING")
    assert not owner_binding_required()
    assert bound_principal({}, {}) == {}
    assert_claim_owner({}, {})


class _Projection:
    generation=9
    @staticmethod
    def is_fresh(): return True


class _Response:
    status_code=200
    def __init__(self,owner):
        self.owner=owner
    def json(self):
        return {
            "active":True,
            "task_id":"task-1","claim_id":"claim-1","agent_id":"agent-1",
            "projection_generation":9,
            "lease_expires_at_ts":int(time.time()*1000)+60000,
            **self.owner,
        }


@pytest.mark.asyncio
async def test_predispatch_claim_fence_rejects_owner_change(monkeypatch):
    class _Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def get(self,*args,**kwargs):return _Response(_signed("person-b"))
    monkeypatch.setattr(claim_fence.httpx,"AsyncClient",_Client)
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL","http://assistx.test")
    monkeypatch.setenv("AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN","fake-unit-token")
    req=SimpleNamespace(metadata={"assistx_executor":{
        "task_id":"task-1","claim_id":"claim-1","agent_id":"agent-1",
        "projection_generation":9, **_signed("person-a"),
    }})
    state=SimpleNamespace(runtime_projection_manager=SimpleNamespace(current=_Projection()))
    with pytest.raises(ExecutorClaimFenceError,match="owner no longer"):
        await assert_executor_claim_current(req,state)


@pytest.mark.asyncio
async def test_strict_owner_fence_disallows_unowned_service_bypass():
    req=SimpleNamespace(metadata={"assistx_service":{
        "identity":"assistx-internal","authenticated":True,
    }})
    state=SimpleNamespace(runtime_projection_manager=SimpleNamespace(current=_Projection()))
    with pytest.raises(ExecutorClaimFenceError,match="untrusted service identity"):
        await assert_executor_claim_current(req,state)



def test_owner_fence_misspelled_config_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING", "tru")
    assert owner_binding_required() is True
    with pytest.raises(OwnerBindingError):
        bound_principal({}, {})


@pytest.mark.asyncio
async def test_strict_owner_requires_executor_auth_enabled(monkeypatch):
    from auto_router.executor_auth import ExecutorInferenceAuthMiddleware
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED","false")
    sent=[]
    async def down(*args):
        raise AssertionError("auth-disabled bypass reached downstream")
    async def receive():
        return {"type":"http.request","body":b"", "more_body":False}
    async def send(message):
        sent.append(message)
    middleware=ExecutorInferenceAuthMiddleware(down,SimpleNamespace())
    await middleware({"type":"http","method":"POST","path":"/v1/chat/completions"},receive,send)
    assert sent[0]["status"]==503
