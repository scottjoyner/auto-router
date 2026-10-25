"""No-network tests for exact-head CI image / admin canary receipt creation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import write_ci_admin_release_evidence as m

def fake_env():
    return {
        "GITHUB_ACTIONS":"true",
        "AUTO_ROUTER_CANARY_ENV":"disposable-github-ci",
        "AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN":"smoke-admin-token",
        "GITHUB_SHA":"a"*40,
    }

def healthy_subprocess(monkeypatch):
    seen=[]
    def checked(*args):
        seen.append(args)
        if args[:2]==("git","rev-parse"):
            return "a"*40 if args[2]=="HEAD" else "b"*40
        if args[:2]==("git","status"):
            return ""
        if args[:2]==("docker","inspect"):
            if "auto-router-smoke" not in args:
                raise AssertionError("unexpected container")
            if args[-1]=="{{.Image}}":
                return "sha256:"+"f"*64
            return "llm-router"
        raise AssertionError("unexpected command: "+repr(args))
    monkeypatch.setattr(m,"checked_output",checked)
    monkeypatch.setattr(m,"digest",lambda path:"c"*64)
    data={
        "result":"DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS",
        "checked_routes":10,"denied_missing_and_wrong_token":True,
        "public_client_reads_pass":True,
        "authorized_read_pass":True,"production_tested":False,
        "synthetic_credentials_only":True,
        "effective_snapshot":{"sanitized":True,"provider_count":0}
    }
    surface={
        "verdict":"DISPOSABLE_ALL_ADMIN_AUTH_NEGATIVES_PASS",
        "mounted_admin_operations":20,"get_operations":16,"post_operations":4,
        "checked_missing_and_wrong_credentials":True,
        "authenticated_mutation_attempts":0,
        "production_tested":False,"production_modified":False,
    }
    client={
        "verdict":"DISPOSABLE_LEGACY_CLIENT_READ_PARITY_PASS",
        "public_discovery_reads":2,"admin_missing_wrong_denials":8,
        "authorized_legacy_admin_reads":4,"alternate_authorization_reads":2,
        "legacy_catalog_shape_verified":True,"sanitized_effective_providers":True,
        "model_requests":0,"authenticated_admin_mutations":0,
        "production_tested":False,"production_modified":False,
        "cross_repo_real_clients_executed":False,
    }
    def call(cmd,**kwargs):
        seen.append(tuple(cmd))
        name=str(cmd[-1])
        payload=(client if "verify_router_legacy_client_ci.py" in name else
                 surface if "verify_admin_surface_exhaustive.py" in name else data)
        return SimpleNamespace(returncode=0,stdout=json.dumps(payload),stderr="")
    monkeypatch.setattr(m.subprocess,"run",call)
    return data,seen

def test_ci_exact_head_evidence_only(monkeypatch):
    _data,seen=healthy_subprocess(monkeypatch)
    result=m.build_evidence(fake_env())
    assert result["source_commit"]=="a"*40
    assert result["ci_container_image_id"]=="sha256:"+"f"*64
    assert result["checked_admin_route_count"]==10
    assert result["all_admin_operation_count"]==20
    assert result["all_admin_get_count"]==16
    assert result["all_admin_post_count"]==4
    assert result["authenticated_admin_post_attempts"]==0
    assert result["all_admin_missing_and_wrong_tokens_denied"] is True
    assert result["legacy_client_verdict"]=="DISPOSABLE_LEGACY_CLIENT_READ_PARITY_PASS"
    assert result["public_client_discovery_reads"]==2
    assert result["legacy_admin_denial_checks"]==8
    assert result["legacy_authorized_admin_reads"]==4
    assert result["legacy_alt_auth_reads"]==2
    assert result["synthetic_client_only"] is True
    assert result["real_provider_requests"]==0
    assert result["public_client_reads_preserved"] is True
    assert result["production_deployment_authorized"] is False
    assert result["rollback_proven"] is False
    assert result["independent_archival_custody_proven"] is False
    assert "smoke-admin-token" not in json.dumps(result)
    assert any("verify_admin_canary_ci.py" in " ".join(t) for t in seen)
    assert any("verify_admin_surface_exhaustive.py" in " ".join(t) for t in seen)
    assert any("verify_router_legacy_client_ci.py" in " ".join(t) for t in seen)

def test_non_ci_or_bad_sha_denied(monkeypatch):
    healthy_subprocess(monkeypatch)
    env=fake_env()
    env["GITHUB_ACTIONS"]="false"
    with pytest.raises(m.EvidenceDenied):
        m.build_evidence(env)
    env=fake_env()
    env["GITHUB_SHA"]="dirty"
    with pytest.raises(m.EvidenceDenied):
        m.build_evidence(env)

def test_mismatched_checkout_denied(monkeypatch):
    healthy_subprocess(monkeypatch)
    def wrong(*args):
        if args[:2]==("git","rev-parse"):return "f"*40
        raise AssertionError("must stop on mismatched checkout")
    monkeypatch.setattr(m,"checked_output",wrong)
    with pytest.raises(m.EvidenceDenied,match="identity"):
        m.build_evidence(fake_env())

def test_failed_canary_never_generates_pass(monkeypatch):
    healthy_subprocess(monkeypatch)
    monkeypatch.setattr(m.subprocess,"run",lambda *args,**kwargs:
                        SimpleNamespace(returncode=2,stdout="",stderr="refused"))
    with pytest.raises(m.EvidenceDenied,match="canary failed"):
        m.build_evidence(fake_env())

def test_incomplete_canary_denied(monkeypatch):
    healthy_subprocess(monkeypatch)
    monkeypatch.setattr(m.subprocess,"run",lambda *args,**kwargs:
                        SimpleNamespace(returncode=0,stdout=json.dumps(
                            {"result":"DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS",
                             "checked_routes":0}),stderr=""))
    with pytest.raises(m.EvidenceDenied,match="incomplete"):
        m.build_evidence(fake_env())

def test_failed_surface_canary_never_generates_receipt(monkeypatch):
    healthy_subprocess(monkeypatch)
    fake = m.subprocess.run
    def fail_surface(cmd, **kwargs):
        if "verify_admin_surface_exhaustive.py" in str(cmd[-1]):
            return SimpleNamespace(returncode=2,stdout="",stderr="hold")
        return fake(cmd, **kwargs)
    monkeypatch.setattr(m.subprocess,"run",fail_surface)
    with pytest.raises(m.EvidenceDenied,match="all-admin negative-auth canary failed"):
        m.build_evidence(fake_env())


@pytest.mark.parametrize("change", [
    {"mounted_admin_operations":10},
    {"get_operations":15},
    {"post_operations":3},
    {"authenticated_mutation_attempts":1},
    {"checked_missing_and_wrong_credentials":False},
    {"production_modified":True},
])
def test_forged_or_partial_surface_canary_denied(monkeypatch,change):
    healthy_subprocess(monkeypatch)
    original = m.subprocess.run
    def spoof(cmd, **kwargs):
        response=original(cmd, **kwargs)
        if "verify_admin_surface_exhaustive.py" in str(cmd[-1]):
            doc=json.loads(response.stdout)
            doc.update(change)
            return SimpleNamespace(returncode=0,stdout=json.dumps(doc),stderr="")
        return response
    monkeypatch.setattr(m.subprocess,"run",spoof)
    with pytest.raises(m.EvidenceDenied,match="incomplete all-admin"):
        m.build_evidence(fake_env())

def test_failed_legacy_client_canary_never_publishes(monkeypatch):
    healthy_subprocess(monkeypatch)
    original=m.subprocess.run
    def fail(cmd,**kwargs):
        if "verify_router_legacy_client_ci.py" in str(cmd[-1]):
            return SimpleNamespace(returncode=1,stdout="",stderr="denied")
        return original(cmd,**kwargs)
    monkeypatch.setattr(m.subprocess,"run",fail)
    with pytest.raises(m.EvidenceDenied,match="legacy-client parity canary failed"):
        m.build_evidence(fake_env())


@pytest.mark.parametrize("tamper",[
    {"public_discovery_reads":1},{"admin_missing_wrong_denials":7},
    {"authorized_legacy_admin_reads":3},{"alternate_authorization_reads":1},
    {"legacy_catalog_shape_verified":False},{"sanitized_effective_providers":False},
    {"model_requests":1},{"authenticated_admin_mutations":1},
    {"production_modified":True},{"cross_repo_real_clients_executed":True},
])
def test_forged_legacy_client_evidence_denied(monkeypatch,tamper):
    healthy_subprocess(monkeypatch)
    original=m.subprocess.run
    def spoof(cmd,**kwargs):
        response=original(cmd,**kwargs)
        if "verify_router_legacy_client_ci.py" in str(cmd[-1]):
            doc=json.loads(response.stdout)
            doc.update(tamper)
            return SimpleNamespace(returncode=0,stdout=json.dumps(doc),stderr="")
        return response
    monkeypatch.setattr(m.subprocess,"run",spoof)
    with pytest.raises(m.EvidenceDenied,match="legacy client parity incomplete"):
        m.build_evidence(fake_env())
