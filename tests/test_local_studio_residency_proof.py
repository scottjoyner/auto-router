"""Starred Local Studio fleet-discovery audit: residency must be evidenced, never inferred.

Uses mocked HTTP only. No fleet probes, model loads/unloads, actual inference,
network egress or production registry writes.
"""
from __future__ import annotations

import httpx
import pytest

from auto_router import fleet_task_dispatcher as discovery


def _probe(monkeypatch, *, native_v1=None, native_v0=None, compat=None):
    """Three exact HTTP response fixtures, otherwise isolated 404s."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models" and native_v1 is not None:
            return httpx.Response(200,json={"models":native_v1})
        if request.url.path == "/api/v0/models" and native_v0 is not None:
            return httpx.Response(200,json={"data":native_v0})
        if request.url.path == "/v1/models" and compat is not None:
            return httpx.Response(200,json={"data":compat})
        return httpx.Response(404)
    real_client=httpx.Client
    monkeypatch.setattr(discovery.httpx,"Client",
                        lambda *args,**kwargs: real_client(
                            transport=httpx.MockTransport(handler),
                            timeout=kwargs.get("timeout"),
                            follow_redirects=False))
    return discovery._probe_provider({
        "name":"fixture-lmstudio",
        "node_id":"fixture-node",
        "base_url":"http://fixture-node:1234/v1",
        "configured_models":[],
        "model_aliases":{},
    },timeout_seconds=5)


def test_native_loaded_instances_are_measured_not_advertised(monkeypatch):
    """xwing-style: 17 advertised IDs need not mean 17 loaded models."""
    native=[
        {"key":"resident-language","loaded_instances":[{"id":"inst1","config":{}}]},
        {"key":"resident-embed","loaded_instances":[{"id":"inst2","config":{}}]},
    ]
    native += [{"key":f"idle-{i}","loaded_instances":[]} for i in range(14)]
    compat=[{"id":d["key"]} for d in native]+[{"id":"advertised-only"}]
    node=_probe(monkeypatch,native_v1=native,compat=compat)
    assert node.online is True
    assert node.inventory_complete is True
    assert node.loaded_models==["resident-embed","resident-language"]
    assert len(node.all_models)==17
    # A compatibility-only entry has no independently measured residency.
    assert node.inventory_authoritative is False
    assert any("unverified" in w.lower() or "unknown" in w.lower()
               for w in node.warnings)


def test_native_complete_consistent_inventory_can_be_authoritative(monkeypatch):
    native=[
        {"key":"resident","loaded_instances":[{"id":"inst1","config":{}}]},
        {"key":"idle","loaded_instances":[]},
    ]
    node=_probe(monkeypatch,native_v1=native,compat=[{"id":"resident"},{"id":"idle"}])
    assert node.online is True
    assert node.inventory_authoritative is True
    assert node.loaded_state_source=="native"
    assert node.loaded_models==["resident"]


@pytest.mark.parametrize("bad",["false","true","yes",1,0,[],{},None])
def test_malformed_native_boolean_never_proves_loaded(monkeypatch,bad):
    node=_probe(monkeypatch,native_v1=[{"key":"not-proven","loaded":bad}],
                compat=[{"id":"not-proven"}])
    assert node.online is True
    assert node.loaded_models==[]
    assert node.inventory_authoritative is False


@pytest.mark.parametrize("state",["loading","fetching","warmed","",None,77,True])
def test_unrecognized_native_state_is_not_measured_unloaded(monkeypatch,state):
    node=_probe(monkeypatch,native_v0=[{"id":"unverified","state":state}],
                compat=[{"id":"unverified"}])
    assert node.online is True
    assert node.loaded_models==[]
    assert node.inventory_authoritative is False


@pytest.mark.parametrize("instances",[None,True,"[]",{"id":"wrong"},[1],["id"]])
def test_malformed_instance_list_is_never_residency_proof(monkeypatch,instances):
    node=_probe(monkeypatch,native_v1=[{"key":"unverified","loaded_instances":instances}],
                compat=[{"id":"unverified"}])
    assert node.online is True
    assert node.loaded_models==[]
    assert node.inventory_authoritative is False


def test_partial_native_catalog_does_not_certify_every_model(monkeypatch):
    node=_probe(monkeypatch,native_v1=[
        {"key":"resident","loaded_instances":[{"id":"i"}]},
        {"key":"unmeasured"},
    ],compat=[{"id":"resident"},{"id":"unmeasured"}])
    assert node.online is True
    assert node.loaded_models==["resident"]
    assert node.inventory_authoritative is False


def test_disagreement_between_native_versions_does_not_claim_live(monkeypatch):
    node=_probe(monkeypatch,
        native_v1=[{"key":"model-a","loaded_instances":[{"id":"i"}]}],
        native_v0=[{"id":"model-a","state":"unloaded"}],
        compat=[{"id":"model-a"}])
    assert node.online is True
    assert node.loaded_models==[]
    assert node.inventory_authoritative is False


def test_contradiction_inside_one_native_row_is_ambiguous(monkeypatch):
    node=_probe(monkeypatch,native_v1=[
        {"key":"model-a","state":"unloaded","loaded_instances":[{"id":"i"}]}
    ],compat=[{"id":"model-a"}])
    assert node.online is True
    assert node.loaded_models==[]
    assert node.inventory_authoritative is False


def test_404_native_compatibility_visibility_stays_non_authoritative(monkeypatch):
    node=_probe(monkeypatch,compat=[{"id":"available-unknown"}])
    assert node.online is True
    assert node.all_models==["available-unknown"]
    assert node.loaded_models==[]
    assert node.inventory_authoritative is False
    assert node.loaded_state_source=="compatibility-inferred"


def test_native_catalog_without_any_residency_fields_is_not_authoritative(monkeypatch):
    node=_probe(monkeypatch,native_v1=[{"key":"visible-no-residency"}],
                compat=[{"id":"visible-no-residency"}])
    assert node.online is True
    assert node.loaded_models==[]
    assert node.inventory_authoritative is False


def test_unknown_native_model_does_not_supplant_measured_loaded(monkeypatch):
    node=_probe(monkeypatch,native_v1=[
        {"key":"measured","loaded_instances":[{"id":"i"}]},
        {"key":"unknown","status":"starting"},
    ],compat=[{"id":"measured"},{"id":"unknown"}])
    assert node.loaded_models==["measured"]
    assert node.inventory_authoritative is False


def test_all_model_ids_are_never_automatically_declared_loaded(monkeypatch):
    node=_probe(monkeypatch,native_v1=[
        {"key":"resident","loaded_instances":[{"id":"i"}]},
        {"key":"idle","loaded_instances":[]}
    ],compat=[{"id":"resident"},{"id":"idle"}])
    assert len(node.all_models)==2
    assert len(node.loaded_models)==1


def test_observed_catalog_and_loaded_count_remain_separate(monkeypatch):
    node=_probe(monkeypatch,native_v1=[
        {"key":"loaded","state":"running"},
        {"key":"downloaded","state":"not-loaded"}
    ],compat=[{"id":"loaded"},{"id":"downloaded"}])
    assert node.inventory_authoritative is True
    assert node.loaded_models==["loaded"]
    assert node.all_models==["downloaded","loaded"]
