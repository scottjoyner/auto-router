"""Hermetic coverage of the actual deployed secure_live inference entrypoint.

No import of secure_live: the reconciled app loads production-only /data
settings, so this test audits source-level installation and every local
OpenAI-style inference route without contacting any model or app service.
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

from auto_router.stream_effect_fence import StreamEffectDenied
from test_stream_lifecycle import _candidate, _module, _request

SRC=Path(__file__).resolve().parents[1]/"src"/"auto_router"


def tree(filename):
    return ast.parse((SRC/filename).read_text(),filename=filename)


def _decorated_post_routes(filename):
    found={}
    for node in tree(filename).body:
        if not isinstance(node,(ast.AsyncFunctionDef,ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator,ast.Call):
                continue
            func=decorator.func
            if not isinstance(func,ast.Attribute) or func.attr!="post":
                continue
            if not decorator.args or not isinstance(decorator.args[0],ast.Constant):
                continue
            path=decorator.args[0].value
            if isinstance(path,str):
                found[path]=node.name
    return found


def _protected_paths():
    for node in tree("executor_auth.py").body:
        if (isinstance(node,ast.Assign) and any(
                isinstance(target,ast.Name) and target.id=="_PROTECTED_PATHS"
                for target in node.targets)):
            return ast.literal_eval(node.value)
    raise AssertionError("executor middleware no longer has a reviewed protected-path set")


def _call_name(node):
    f=node.func
    if isinstance(f,ast.Name):
        return f.id
    if isinstance(f,ast.Attribute):
        return f.attr
    return None


def test_every_openai_inference_post_route_is_signed_claim_protected():
    registered=_decorated_post_routes("main.py")
    inference={path for path in registered if path.startswith("/v1/")}
    assert inference=={
        "/v1/chat/completions","/v1/responses",
        "/v1/completions","/v1/embeddings",
    },("Review added/replaced inference routes before updating this guard",inference)
    assert inference<=_protected_paths(),("An OpenAI inference POST route bypasses token auth",inference)


def test_secure_entrypoint_installs_all_three_guards_in_expected_order():
    app_tree=tree("secure_live.py")
    installers=[
        _call_name(node.value)
        for node in app_tree.body
        if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call)
        and _call_name(node.value) in {
            "install_request_idempotency",
            "install_stream_lifecycle",
            "install_executor_inference_auth",
        }
    ]
    assert installers==[
        "install_request_idempotency",
        "install_stream_lifecycle",
        "install_executor_inference_auth",
    ]


def test_main_live_routes_dispatch_through_underlying_patched_module():
    live=tree("main_live.py")
    assignments=[
        node for node in live.body
        if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Attribute)
                and isinstance(target.value,ast.Name)
                and target.value.id=="main_module"
                and target.attr in {"_dispatch","_dispatch_stream"}
                for target in node.targets)
    ]
    assert len(assignments)==2
    assert any(isinstance(node,ast.FunctionDef)
               and node.name=="install_stream_lifecycle"
               for node in tree("stream_lifecycle.py").body)


def test_main_live_removes_retired_inherited_agent_route():
    live=(SRC/"main_live.py").read_text()
    assert '"/jobs/agent"' in live
    assert "_remove_retired_inherited_routes()" in live
    # Tool-capable AssistX routes are routed to its own authority rather than
    # directly running arbitrary in-process model/tool requests.
    strict=(SRC/"strict_assistx_routes.py").read_text()
    assert "install_strict_assistx_route_guard" in strict
    assert 'if assistx_routes_module._request_needs_tools(request):' in strict


@pytest.mark.asyncio
@pytest.mark.parametrize("route",[
    "chat_completions","completions","responses","embeddings",
])
async def test_each_nonstream_inference_route_is_denied_by_strict_effect_flag(
    route,monkeypatch
):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE","true")
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")
    reached=[]
    async def body():
        reached.append(route)
        yield b"forbidden"
    module,*_=_module(body)
    request=_request(stream=False)
    request.route=route
    with pytest.raises(StreamEffectDenied,match="non-streaming inference"):
        await module._dispatch(None,_candidate(),request)
    assert reached==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("route",["chat_completions","completions","responses"])
async def test_every_streaming_route_denies_missing_owner_authority_before_provider(
    route,monkeypatch
):
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE","true")
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")
    reached=[]
    async def body():
        reached.append(route)
        yield b"forbidden"
    module,*_=_module(body)
    request=_request(stream=True)
    request.route=route
    with pytest.raises(StreamEffectDenied,match="authority unavailable"):
        await module._dispatch_stream(None,_candidate(),request)
    assert reached==[]


def test_guard_does_not_consider_client_metadata_or_header_as_trust_authority():
    source=(SRC/"stream_effect_fence.py").read_text()
    assert 'getattr(state, "stream_effect_authority", None)' in source
    assert 'request.raw_body["stream_effect_authority"]' not in source
    assert 'request.metadata["stream_effect_authority"]' not in source
