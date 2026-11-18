"""Fail-closed rollback inventory contract: no Docker lifecycle commands."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.admin_auth_readonly_canary import ADMIN_PATHS
from scripts.audit_router_rollback_inventory import (
    DEFAULT_CANDIDATES, IMAGE_RE, audit, read_container,
    read_image, validate_images, write_private,
)

LIVE="sha256:"+"a"*64
NEXT="sha256:"+"b"*64
LEASE="2026-11-04T16:01:00Z"
PORT_MAP={"8088/tcp":[{"HostIp":"0.0.0.0","HostPort":"8088"},
                        {"HostIp":"::","HostPort":"8088"}]}


def fixture_docker(*, images=None, restart=False, different_image=False,
                   bind=None):
    images=images or {NEXT, LIVE}
    calls=[]
    count=[0]

    def reader(args):
        calls.append(args)
        if args[:2]==["docker","inspect"]:
            assert args[-1]=="git-router-1"
            count[0]+=1
            digest=NEXT if different_image and count[0]>1 else LIVE
            start=(LEASE+"-restart") if restart and count[0]>1 else LEASE
            ports=PORT_MAP if bind is None else bind
            return f"{digest}|true|{start}|{json.dumps(ports)}"
        if args[:3]==["docker","image","inspect"]:
            image=args[-1]
            if image not in images:
                raise RuntimeError("not cached")
            return f"{image}|2026-10-25T18:29:13Z"
        raise AssertionError(f"unexpected Docker API: {args}")
    return reader,calls


def canary(*, leaked=True):
    statuses=[200,200,200,200,200,401] if leaked else [401]*6
    return {"all_denied":not leaked,"results":[
        {"path":p,"status":code} for p,code in zip(ADMIN_PATHS,statuses)
    ]}


def test_real_live_image_is_disallowed_as_fallback():
    reader,calls=fixture_docker()
    r=audit((LIVE,NEXT),read=reader,status_probe=lambda port:canary())
    assert r["gate"]=="HOLD"
    assert len(r["live_unauthenticated_200_paths"])==5
    assert r["live_container"]["public_host_port_8088"]
    assert r["candidate_images"][0]["assessment"]=="KNOWN_LIVE_EXPOSURE_DENY"
    assert r["candidate_images"][1]["assessment"]=="CACHED_UNVERIFIED_HOLD"
    assert not r["security_safe_rollback_available"]
    assert not r["cutover_authorized"]
    assert not r["production_ready"]
    assert all(args[0]=="docker" and args[1] in ("inspect","image") for args in calls)
    assert all("exec" not in args and "run" not in args and "restart" not in args
               for args in calls)


def test_cached_image_does_not_confer_source_auth_or_operator_approval():
    reader,_=fixture_docker()
    r=audit((NEXT,),read=reader,status_probe=lambda port:canary(leaked=False))
    assert r["live_all_six_admin_gets_deny"] is True
    assert r["candidate_images"][0]["cached_locally"] is True
    for key in ("source_code_identity_verified","anonymous_admin_denial_verified",
                "operator_client_verified","independently_approved_rollback_drill",
                "production_rollback_approved"):
        assert r["candidate_images"][0][key] is False
    assert r["gate"]=="HOLD"


def test_missing_image_is_a_hold_not_a_rollout_recommendation():
    reader,_=fixture_docker(images={LIVE})
    r=audit((NEXT,),read=reader,status_probe=lambda port:canary())
    assert r["candidate_images"][0]["assessment"]=="MISSING_IMAGE_HOLD"
    assert not r["candidate_images"][0]["cached_locally"]
    assert not r["security_safe_rollback_available"]


def test_concurrent_container_restart_refuses_authority():
    reader,_=fixture_docker(restart=True)
    with pytest.raises(ValueError,match="changed"):
        audit((NEXT,),read=reader,status_probe=lambda port:canary())


def test_concurrent_container_image_swap_refuses_authority():
    reader,_=fixture_docker(different_image=True)
    with pytest.raises(ValueError,match="changed"):
        audit((NEXT,),read=reader,status_probe=lambda port:canary())


def test_canary_path_truncation_or_reordering_is_refused():
    reader,_=fixture_docker()
    def partial(port):
        d=canary()
        d["results"].pop()
        return d
    with pytest.raises(ValueError,match="incomplete"):
        audit((NEXT,),read=reader,status_probe=partial)
    def reorder(port):
        d=canary()
        d["results"][0],d["results"][1]=d["results"][1],d["results"][0]
        return d
    with pytest.raises(ValueError,match="incomplete"):
        audit((NEXT,),read=reader,status_probe=reorder)


def test_forged_all_denied_field_refused():
    reader,_=fixture_docker()
    def forged(port):
        d=canary()
        d["all_denied"]=True
        return d
    with pytest.raises(ValueError,match="inconsistent"):
        audit((NEXT,),read=reader,status_probe=forged)


@pytest.mark.parametrize("digest",[
    "sha256:"+"A"*64,"sha256:"+"a"*63,"latest",
    "router:latest","sha256:"+"g"*64,
    "sha256:"+"a"*64+"; docker rm git-router-1",
])
def test_image_tag_or_malformed_digest_refused(digest):
    assert not IMAGE_RE.fullmatch(digest)
    with pytest.raises(ValueError):
        validate_images([digest])


def test_duplicate_and_oversized_image_lists_refused():
    with pytest.raises(ValueError):
        validate_images([NEXT,NEXT])
    with pytest.raises(ValueError):
        validate_images([f"sha256:{i:064x}" for i in range(9)])
    with pytest.raises(ValueError):
        validate_images([])


def test_unexpected_image_metadata_refused():
    read=lambda args:NEXT+"|2026-10-25T00:00:00Z"
    with pytest.raises(ValueError,match="not identity-bound"):
        read_image(LIVE,read)


def test_unknown_port_bindings_denied():
    read=lambda args:LIVE+"|true|"+LEASE+"|"+json.dumps({
        "8088/tcp":[{"HostIp":"0.0.0.0","HostPort":"8089"}]
    })
    with pytest.raises(ValueError,match="mapping"):
        read_container(read)


def test_receipt_is_exclusive_and_private(tmp_path):
    # Only /tmp allowed; never infer safe writes to arbitrary test directories.
    p=Path("/tmp")/f"router-rollback-inventory-unit-{os.getpid()}.json"
    try:
        write_private(p,{"gate":"HOLD","production_ready":False})
        assert p.stat().st_mode & 0o777 == 0o600
        assert json.loads(p.read_text())["gate"]=="HOLD"
        with pytest.raises(ValueError):
            write_private(p,{"gate":"PASS"})
    finally:
        p.unlink(missing_ok=True)
    with pytest.raises(ValueError):
        write_private(tmp_path/"router-rollback-inventory-local.json",
                      {"gate":"HOLD"})


def test_cli_requires_explicit_read_only_operator_selection():
    root=Path(__file__).resolve().parents[1]
    cmd=[sys.executable,"-B","-m","scripts.audit_router_rollback_inventory"]
    r=subprocess.run(cmd,cwd=root,stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE,text=True,timeout=4,check=False)
    assert r.returncode!=0
    assert "explicit-read-only" in r.stderr
    assert not r.stdout


def test_default_candidates_are_three_unique_immutable_ids():
    assert len(validate_images(DEFAULT_CANDIDATES))==3


def test_unauthorized_image_cannot_be_accepted_by_false_boolean():
    reader,_=fixture_docker()
    r=audit((NEXT,),read=reader,status_probe=lambda port:canary(leaked=False))
    # Cached existence or healthy live anonymous canary does not admit fallback.
    assert r["candidate_images"][0]["cached_locally"]
    assert r["live_all_six_admin_gets_deny"]
    assert not r["rollback_operator_approval_verified"]
    assert not r["traffic_isolation_independently_verified"]
    assert r["gate"]=="HOLD"
