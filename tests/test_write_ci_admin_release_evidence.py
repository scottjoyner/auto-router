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
    def call(cmd,**kwargs):
        seen.append(tuple(cmd))
        return SimpleNamespace(returncode=0,stdout=json.dumps(data),stderr="")
    monkeypatch.setattr(m.subprocess,"run",call)
    return data,seen

def test_ci_exact_head_evidence_only(monkeypatch):
    _data,seen=healthy_subprocess(monkeypatch)
    result=m.build_evidence(fake_env())
    assert result["source_commit"]=="a"*40
    assert result["ci_container_image_id"]=="sha256:"+"f"*64
    assert result["checked_admin_route_count"]==10
    assert result["public_client_reads_preserved"] is True
    assert result["production_deployment_authorized"] is False
    assert result["rollback_proven"] is False
    assert result["independent_archival_custody_proven"] is False
    assert "smoke-admin-token" not in json.dumps(result)
    assert any("verify_admin_canary_ci.py" in " ".join(t) for t in seen)

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
