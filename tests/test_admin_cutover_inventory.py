"""No-production-mutation tests for the read-only Router cutover inventory."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import inspect_admin_cutover_readonly as m

def test_current_source_all_inherited_routes_guarded():
    source=(m.ROOT/"src/auto_router/main.py").read_text()
    routes=m.guarded_admin_routes(source)
    assert len(routes)>=13
    assert m.EXPECTED_ADMIN <= set(routes)
    assert all(routes.values())

def test_fails_if_added_legacy_admin_route_omits_auth():
    source=(m.ROOT/"src/auto_router/main.py").read_text()
    source+='\n@app.get("/admin/unsafe-legacy")\nasync def unsafe_legacy():\n    return {}\n'
    routes=m.guarded_admin_routes(source)
    assert routes["/admin/unsafe-legacy"] is False

def test_inventory_never_claims_cutover_even_when_anonymous_is_denied(monkeypatch):
    source=(m.ROOT/"src/auto_router/main.py").read_text()
    sha="a"*40
    image="sha256:"+"f"*64
    container={
        "Image":image,
        "State":{"Status":"running"},
        "Config":{"Env":["AUTO_ROUTER_ADMIN_TOKEN=synthetic-do-not-print-me"]},
        "Mounts":[{"Destination":"/data"}],
    }
    def fake_cmd(*args):
        if args[0]=="git":
            if "rev-parse" in args:
                return sha
            return source.rstrip("\n")
        if args[:2]==("docker","inspect"):
            return json.dumps([container])
        if args[:3]==("docker","image","inspect"):
            return json.dumps([{"Created":"2026-10-07T17:00:00Z"}])
        raise AssertionError("unexpected command")
    monkeypatch.setattr(m,"run_cmd",fake_cmd)
    monkeypatch.setattr(m,"status_only",lambda path:401)
    result=m.inventory()
    assert result["candidate_admin_source_guarded"] is True
    assert result["running_admin_token_configured_nonempty"] is True
    assert result["production_cutover_allowed"] is False
    assert result["candidate_image_canary_passed"] is False
    assert result["authorized_operator_canary_passed"] is False
    assert "synthetic-do-not-print-me" not in json.dumps(result)

def test_live_unprotected_admin_classified_exposure(monkeypatch):
    source=(m.ROOT/"src/auto_router/main.py").read_text()
    def fake_cmd(*args):
        if args[0]=="git":
            return "b"*40 if "rev-parse" in args else source.rstrip("\n")
        if args[:2]==("docker","inspect"):
            return json.dumps([{"Image":"sha256:"+"c"*64,
                                "State":{"Status":"running"},
                                "Config":{"Env":[]},
                                "Mounts":[]}])
        if args[:3]==("docker","image","inspect"):
            return json.dumps([{"Created":"2026-10-07T00:00:00Z"}])
        raise AssertionError("unexpected command")
    monkeypatch.setattr(m,"run_cmd",fake_cmd)
    monkeypatch.setattr(m,"status_only",lambda path:200 if path=="/admin/providers" else 401)
    result=m.inventory()
    assert result["anonymous_admin_exposed"]
    assert result["production_cutover_allowed"] is False
    assert not result["running_admin_token_configured_nonempty"]

def test_status_only_localhost_and_does_not_read_response(monkeypatch):
    urls=[]
    class Reply:
        status=401
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,*args): raise AssertionError("body must not be read")
    def fake(req,timeout):
        urls.append(req.full_url)
        return Reply()
    monkeypatch.setattr(m.urllib.request,"urlopen",fake)
    assert m.status_only("/admin/providers")==401
    assert urls==["http://127.0.0.1:8088/admin/providers"]
