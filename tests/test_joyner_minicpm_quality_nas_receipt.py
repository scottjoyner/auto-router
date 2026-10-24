"""Offline negative controls for the host-locked, NAS-first synthetic runner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def get_module():
    p = Path(__file__).resolve().parents[1] / "scripts/joyner_minicpm_quality_nas_receipt.py"
    name = "joyner_minicpm_quality_nas_receipt"
    spec = importlib.util.spec_from_file_location(name, p)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(p.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(p.parent))
    return module


m = get_module()


@pytest.mark.parametrize(("case","content","expected"),[
    ("arithmetic","42",True),("arithmetic","42.",True),
    ("arithmetic","Answer: 42",False),("arithmetic","43",False),
    ("json",'{"ready":true}',True),("json",'{"ready":false}',False),
    ("json", "<json>bad",False),
    ("literal","READY",True),("literal","Ready",False),
])
def test_quality_is_deterministic_and_strict(case,content,expected):
    assert m.quality(case,content) is expected


def test_refuse_unverified_nas_before_request(monkeypatch,capsys):
    monkeypatch.setattr(m,"is_verified_nas",lambda:False)
    monkeypatch.setattr(m,"request",lambda *a,**kw:pytest.fail("request must not run"))
    assert m.main([])==2
    assert "NO_GO" in capsys.readouterr().err


def test_custody_write_is_atomic_and_readback_verified(tmp_path,monkeypatch):
    monkeypatch.setattr(m,"NAS",tmp_path)
    receipt=tmp_path/"joyner-minicpm-dualpath-fixture.json"
    sha=m.persist(receipt,{"schema":"trace-v1","cutover_authorized":False})
    raw=receipt.read_bytes()
    assert sha==hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["cutover_authorized"] is False
    assert not list(tmp_path.glob("*.tmp"))
    replacement=m.persist(receipt,{"schema":"trace-v2","cutover_authorized":False})
    assert replacement==hashlib.sha256(receipt.read_bytes()).hexdigest()
    assert json.loads(receipt.read_text())["schema"]=="trace-v2"


def test_refuse_arbitrary_receipt_path(tmp_path,monkeypatch):
    monkeypatch.setattr(m,"NAS",tmp_path)
    with pytest.raises(ValueError):
        m.persist(tmp_path/"unrelated.json",{"hello":"world"})
    with pytest.raises(ValueError):
        m.persist(tmp_path.parent/"joyner-minicpm-dualpath-other.json",{"hello":"world"})


def test_bounded_receipt_size(tmp_path,monkeypatch):
    monkeypatch.setattr(m,"NAS",tmp_path)
    with pytest.raises(ValueError):
        m.persist(tmp_path/"joyner-minicpm-dualpath-big.json",{"x":"A"*110_000})


def test_prompt_and_secret_never_deserialized_from_diagnostic_fields():
    assert len(m.CASES)==3
    assert all(n<=96 for _,_,n in m.CASES)
    assert m.ROLE_URLS=={
        "legacy":"http://127.0.0.1:1234/v1/chat/completions",
        "guarded":"http://100.83.215.83:18320/v1/chat/completions",
    }


def test_long_capped_followup_is_exact_fixed_scope():
    assert len(m.FOLLOWUP_CASES)==2
    assert {x[0] for x in m.FOLLOWUP_CASES}=={"arithmetic","literal"}
    assert all(tokens==256 for _,_,tokens in m.FOLLOWUP_CASES)


def simulated_identity(tmp_path,monkeypatch):
    fake_model = tmp_path / "weights.gguf"
    fake_key = tmp_path / "api_key"
    fake_model.write_bytes(b"fixture-non-model")
    fake_key.write_text("test-only-ephemeral-value")
    monkeypatch.setattr(m, "NAS", tmp_path)
    monkeypatch.setattr(m, "is_verified_nas", lambda: True)
    monkeypatch.setattr(m.original, "extract_guarded_identity",
                        lambda: (fake_model, fake_key))
    monkeypatch.setattr(m.original, "allow_paths", lambda *args: True)
    monkeypatch.setattr(m.original, "sha256",
                        lambda path: m.original.EXPECTED_MODEL_SHA256)
    return fake_model, fake_key


def test_each_request_has_nas_pending_receipt_first(tmp_path,monkeypatch,capsys):
    simulated_identity(tmp_path,monkeypatch)
    called=[]
    def fake_request(role,body,token,case):
        traces=list(tmp_path.glob("joyner-minicpm-dualpath-*.json"))
        assert len(traces)==1
        receipt=json.loads(traces[0].read_text())
        assert len(receipt["requests"])==len(called)+1
        assert receipt["requests"][-1]["state"]=="REQUEST_PENDING"
        assert not receipt["cutover_authorized"]
        assert role in ("guarded","legacy")
        assert (token == "test-only-ephemeral-value") == (role=="guarded")
        called.append((case,role))
        return {"http_code":200,"quality_passed":case!="arithmetic",
                "generated_text_sha256":hashlib.sha256(case.encode()).hexdigest(),
                "generated_text_saved":False,"latency_ms":1,
                "prompt_tokens":2,"completion_tokens":2,
                "response_model_id":"synthetic-fixture"}
    monkeypatch.setattr(m,"request",fake_request)
    assert m.main([])==0
    assert len(called)==6
    trace=next(tmp_path.glob("joyner-minicpm-dualpath-*.json"))
    data=json.loads(trace.read_text())
    assert data["status"]=="COMPLETE_DIAGNOSTIC_ONLY"
    assert len(data["requests"])==6
    assert all(r["state"]=="REQUEST_RECORDED" for r in data["requests"])
    assert not data["quality_acceptance"]
    assert not data["cutover_authorized"]
    assert "test-only-ephemeral-value" not in trace.read_text()
    printed=json.loads(capsys.readouterr().out)
    assert printed["sha256"]==hashlib.sha256(trace.read_bytes()).hexdigest()


def test_nas_failure_prevents_any_inference_request(tmp_path,monkeypatch):
    simulated_identity(tmp_path,monkeypatch)
    monkeypatch.setattr(m,"persist",lambda *args: (_ for _ in ()).throw(OSError("NAS unavailable")))
    monkeypatch.setattr(m,"request",lambda *args,**kwargs:pytest.fail("must not call inference"))
    with pytest.raises(OSError,match="NAS unavailable"):
        m.main([])
