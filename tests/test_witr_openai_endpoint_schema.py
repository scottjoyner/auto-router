"""Identity-aware OpenAI model catalog tests: no live network or model loads.

Pinned witr inspection found that Joyner's :1234 was llama-server and returned
Ollama-style {models:[{name,model,...}]} from GET /v1/models, not OpenAI-style
{data:[{id,...}]}. This response MUST NOT count as an OpenAI model catalog.
"""

from __future__ import annotations

import httpx
import pytest

from auto_router import fleet_task_dispatcher as discovery


def _probe(monkeypatch, *, compatibility, native_v1=None, native_v0=None):
    """Mocked GET-only discovery with bounded, exact endpoint responses."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models" and native_v1 is not None:
            return httpx.Response(200, json={"models": native_v1})
        if request.url.path == "/api/v0/models" and native_v0 is not None:
            return httpx.Response(200, json={"data": native_v0})
        if request.url.path == "/v1/models":
            if compatibility is None:
                # HTTPX's json=None omits the body; send JSON null explicitly.
                return httpx.Response(
                    200, content=b"null", headers={"Content-Type": "application/json"}
                )
            return httpx.Response(200, json=compatibility)
        return httpx.Response(404)

    original = httpx.Client
    monkeypatch.setattr(
        discovery.httpx, "Client",
        lambda *a, **kw: original(
            transport=httpx.MockTransport(handler),
            timeout=kw.get("timeout"),
            follow_redirects=False,
        ),
    )
    return discovery._probe_provider(
        {
            "name": "read-only-schema-fixture",
            "node_id": "fixture-node",
            "base_url": "http://fixture-node:1234/v1",
            "configured_models": [],
            "model_aliases": {},
        },
        timeout_seconds=5,
    )


def _joyner_response():
    # Shape inspected on Joyner on Nov 3, not a live/customer-data payload.
    return {
        "models": [{
            "name": "local-placeholder",
            "model": "local-placeholder",
            "modified_at": "2026-11-03T00:00:00Z",
            "size": 1500,
            "digest": "sha256:synthetic",
            "type": "model",
            "description": "synthetic",
            "tags": [],
            "capabilities": [],
            "parameters": {},
        }]
    }


def test_joyner_ollama_shape_is_not_routeable_openai_catalog(monkeypatch):
    node = _probe(monkeypatch, compatibility=_joyner_response())
    assert node.all_models == []
    assert node.loaded_models == []
    assert node.inventory_authoritative is False
    assert node.inventory_complete is False
    assert node.online is False
    assert node.endpoint_status["v1/models"] == "incompatible-model-schema"
    assert "incompatible-model-schema" in node.error
    assert "local-placeholder" not in node.error


def test_compatible_openai_catalog_retains_diagnostic_visibility(monkeypatch):
    node = _probe(monkeypatch, compatibility={
        "object": "list",
        "data": [{"id": "legitimate-model", "object": "model"}],
    })
    assert node.online is True
    assert node.all_models == ["legitimate-model"]
    assert node.loaded_models == []
    assert node.inventory_authoritative is False
    assert node.loaded_state_source == "compatibility-inferred"
    assert node.endpoint_status["v1/models"] == "ok:1"


def test_openai_catalog_without_object_marker_accepted(monkeypatch):
    node = _probe(monkeypatch, compatibility={"data": [{"id": "model-id"}]})
    assert node.online is True
    assert node.all_models == ["model-id"]


def test_empty_openai_catalog_is_valid_empty_response(monkeypatch):
    node = _probe(monkeypatch, compatibility={"data": []})
    assert node.online is True
    assert node.all_models == []
    assert node.loaded_models == []


@pytest.mark.parametrize("payload", [
    {"models": [{"model": "foo"}]},
    {"models": []},
    {"models": [{"id": "smuggled-id"}], "data": [{"id": "valid-id"}]},
    {"data": [{"name": "no-id", "model": "m"}]},
    {"data": [{"id": "valid"}, {"name": "missing-id"}]},
    {"data": [None]},
    {"data": ["model"]},
    {"data": "not-a-list"},
    {"data": {}},
    {"error": "authorization required"},
    [],
    None,
    {"data": [{"id": "m", "object": "not-a-model"}]},
    {"object": "not-list", "data": [{"id": "m"}]},
    {"data": [{"id": "m"}, {"id": "m"}]},
])
def test_non_openai_schema_is_rejected_fail_closed(monkeypatch,payload):
    node = _probe(monkeypatch, compatibility=payload)
    assert node.online is False
    assert node.all_models == []
    assert node.loaded_models == []
    assert node.endpoint_status["v1/models"] == "incompatible-model-schema"


@pytest.mark.parametrize("invalid", [
    None, True, False, 42, [], {}, "", "   ", "\x00injected", "a\nb",
    " " + "m", "m" + " ", "a" * 513,
])
def test_untrustworthy_model_id_rejected(monkeypatch,invalid):
    node = _probe(monkeypatch, compatibility={"data": [{"id": invalid}]})
    assert node.online is False
    assert node.all_models == []
    assert node.endpoint_status["v1/models"] == "incompatible-model-schema"


def test_native_lmstudio_evidence_survives_incompatible_compat_endpoint(monkeypatch):
    native = [
        {"key": "native-loaded", "loaded_instances": [{"id": "instance-1"}]},
        {"key": "native-idle", "loaded_instances": []},
    ]
    node = _probe(
        monkeypatch,
        native_v1=native,
        compatibility=_joyner_response(),
    )
    assert node.online is True
    assert node.loaded_models == ["native-loaded"]
    assert node.all_models == ["native-idle", "native-loaded"]
    assert node.inventory_authoritative is True
    assert node.loaded_state_source == "native"
    assert node.endpoint_status["v1/models"] == "incompatible-model-schema"
    assert any("incompatible-model-schema" in w for w in node.warnings)


def test_matching_native_and_compatible_catalog_can_be_authoritative(monkeypatch):
    native = [
        {"key": "native-loaded", "loaded_instances": [{"id": "instance-1"}]},
        {"key": "native-idle", "loaded_instances": []},
    ]
    node = _probe(
        monkeypatch,
        native_v1=native,
        compatibility={
            "data": [{"id": "native-loaded"}, {"id": "native-idle"}],
        },
    )
    assert node.online is True
    assert node.inventory_authoritative is True
    assert node.loaded_models == ["native-loaded"]
    assert node.all_models == ["native-idle", "native-loaded"]


def test_invalid_openai_catalog_cannot_expand_native_catalog(monkeypatch):
    node = _probe(
        monkeypatch,
        native_v1=[{"key": "native-one", "loaded_instances": []}],
        compatibility={
            "models": [{"model": "forged-extra-model"}],
        },
    )
    assert node.online is True
    assert node.all_models == ["native-one"]
    assert "forged-extra-model" not in node.all_models
    assert node.inventory_authoritative is True


def test_native_api_model_key_remains_supported(monkeypatch):
    node = _probe(
        monkeypatch,
        native_v1=[{"model": "native-model", "loaded_instances": [{"id":"i"}]}],
        compatibility={"data": []},
    )
    assert node.all_models == ["native-model"]
    assert node.loaded_models == ["native-model"]
    assert node.inventory_authoritative is True
