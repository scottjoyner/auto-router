"""Synthetic/no-Docker unit tests for strict read-only container release gate."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.admin_auth_readonly_canary import ADMIN_PATHS
from scripts.verify_deployed_admin_release_identity import (
    FILES, _identity_lease, _sha, evaluate, inspect,
)

IMAGE="sha256:"+"a"*64
FILES_HASHES={file:format(i+1,"064x") for i,file in enumerate(FILES)}
LEASE=IMAGE+"|2026-11-04T16:02:00.000Z|true"


def _proof(*, public=False):
    return {
        "image_id":IMAGE,
        "identity_lease":LEASE,
        "running":True,
        "expected_entrypoint":True,
        "installed_source_hashes":FILES_HASHES.copy(),
        "bindings":{"8088/tcp":[{
            "HostIp":"0.0.0.0" if public else "127.0.0.1",
            "HostPort":"8088",
        }]},
    }


def _canary(port):
    assert port==8088
    return {"all_denied":True,"results":[
        {"path":path,"status":401} for path in ADMIN_PATHS
    ]}


def test_exact_image_sources_bind_and_all_401_only_pass_pre_cutover_not_production():
    result=evaluate(_proof(), "a"*64,FILES_HASHES,status_probe=_canary)
    assert result["gate"]=="PASS_PRE_CUTOVER_INSPECTION_ONLY"
    assert result["production_ready"] is False
    assert result["authenticated_operator_behavior_verified"] is False
    assert result["response_bodies_read"] is False
    assert all(result["checks"].values())


@pytest.mark.parametrize("change",[
    lambda d:d.update(image_id="sha256:"+"b"*64),
    lambda d:d.update(running=False),
    lambda d:d.update(expected_entrypoint=False),
    lambda d:d["installed_source_hashes"].update({"security.py":"0"*64}),
    lambda d:d.update(bindings={}),
    lambda d:d.update(bindings={"8088/tcp":[{"HostIp":"0.0.0.0","HostPort":"8088"}]}),
    lambda d:d.update(bindings={"8088/tcp":[{"HostIp":"127.0.0.1","HostPort":"8089"}]}),
    lambda d:d.update(bindings={"8088/tcp":[
        {"HostIp":"127.0.0.1","HostPort":"8088"},
        {"HostIp":"0.0.0.0","HostPort":"8088"},
    ]}),
])
def test_stale_or_unsafe_deployment_never_passes(change):
    d=_proof()
    change(d)
    result=evaluate(d,"a"*64,FILES_HASHES,status_probe=_canary)
    assert result["gate"]=="HOLD"
    assert not all(result["checks"].values())


def test_additional_published_host_port_denies_even_if_8088_is_correct():
    d=_proof()
    d["bindings"]["9090/tcp"]=[{"HostIp":"0.0.0.0","HostPort":"9090"}]
    result=evaluate(d,"a"*64,FILES_HASHES,status_probe=_canary)
    assert result["gate"]=="HOLD"
    assert result["checks"]["no_unreviewed_published_host_ports"] is False
    # Docker may report additional EXPOSEd but unpublished ports as null.
    d["bindings"]["9090/tcp"]=None
    assert evaluate(d,"a"*64,FILES_HASHES,
                    status_probe=_canary)["gate"]=="PASS_PRE_CUTOVER_INSPECTION_ONLY"


def test_decoy_asgi_argument_cannot_pass_exact_entrypoint_check():
    def fake(args):
        if "exec" in args:
            return json.dumps(FILES_HASHES)
        if "{{.Image}}|{{.State.StartedAt}}|{{.State.Running}}" in args:
            return LEASE
        if "{{.State.Running}}" in args:
            return "true"
        if "{{.Path}}" in args:
            return "uvicorn"
        if "{{json .Args}}" in args:
            return json.dumps(["unreviewed.module:app","--factory",
                               "auto_router.secure_live:app"])
        if "{{json .NetworkSettings.Ports}}" in args:
            return json.dumps(_proof()["bindings"])
        raise AssertionError(args)
    actual=inspect(read=fake)
    assert actual["expected_entrypoint"] is False
    result=evaluate(actual,"a"*64,FILES_HASHES,status_probe=_canary)
    assert result["gate"]=="HOLD"


def test_public_bind_requires_explicit_individual_optin_and_still_needs_valid_image():
    result=evaluate(_proof(public=True),"a"*64,FILES_HASHES,
                    allow_public_bind=True,status_probe=_canary)
    assert result["gate"]=="PASS_PRE_CUTOVER_INSPECTION_ONLY"
    assert result["production_ready"] is False
    result=evaluate(_proof(public=True),"b"*64,FILES_HASHES,
                    allow_public_bind=True,status_probe=_canary)
    assert result["gate"]=="HOLD"


def test_anonymous_200_refuses_even_when_image_is_exact():
    def leak(port):
        response=_canary(port)
        response["all_denied"]=False
        response["results"][0]["status"]=200
        return response
    result=evaluate(_proof(),"a"*64,FILES_HASHES,status_probe=leak)
    assert result["gate"]=="HOLD"
    assert result["anonymous_admin_statuses"][0]=={
        "path":"/admin/providers","status":200}


def test_forged_shortened_success_receipt_must_not_pass():
    def shortened(port):
        return {"all_denied":True,"results":[
            {"path":"/admin/providers","status":401}
        ]}
    assert evaluate(_proof(),"a"*64,FILES_HASHES,
                    status_probe=shortened)["gate"]=="HOLD"


def test_forged_reordered_path_or_non_401_denies():
    def reordered(port):
        result=_canary(port)
        result["results"][0],result["results"][1]=(
            result["results"][1],result["results"][0])
        return result
    assert evaluate(_proof(),"a"*64,FILES_HASHES,
                    status_probe=reordered)["gate"]=="HOLD"


def test_expected_sha_and_file_identity_require_all_four_pins():
    for bad in ("x"*64,"a"*63,True,"A"*64):
        with pytest.raises((ValueError,TypeError)):
            _sha(bad,"hash")
    with pytest.raises(ValueError):
        evaluate(_proof(),"a"*64,{"main.py":"b"*64},status_probe=_canary)


def test_no_arbitrary_host_port_permitted():
    for port in (0,8089,False,"8088"):
        with pytest.raises(ValueError):
            evaluate(_proof(),"a"*64,FILES_HASHES,status_probe=_canary,port=port)


def test_docker_inspect_only_selected_metadata_and_files():
    calls=[]
    def fake(args):
        calls.append(args)
        if "exec" in args:
            assert args[:4]==["docker","exec","git-router-1","python3"]
            assert "import auto_router" not in args[-1]
            assert "os.environ" not in args[-1]
            return json.dumps(FILES_HASHES)
        if "{{.Image}}|{{.State.StartedAt}}|{{.State.Running}}" in args:
            return LEASE
        if "{{.State.Running}}" in args:
            return "true"
        if "{{.Path}}" in args:
            return "uvicorn"
        if "{{json .Args}}" in args:
            return json.dumps(["auto_router.secure_live:app","--port","8088"])
        if "{{json .NetworkSettings.Ports}}" in args:
            return json.dumps(_proof()["bindings"])
        raise AssertionError(args)
    actual=inspect(read=fake)
    assert actual["image_id"]==IMAGE
    assert actual["identity_lease"]==LEASE
    assert actual["installed_source_hashes"]==FILES_HASHES
    assert len(calls)==6
    assert not any("docker rm" in " ".join(x) or "docker restart" in " ".join(x)
                   for x in calls)


def test_container_restart_makes_identity_lease_change():
    def before(args):return LEASE
    def after(args):return IMAGE+"|2026-11-04T16:03:00.000Z|true"
    assert _identity_lease(before)!=_identity_lease(after)


def test_cli_requires_all_image_and_source_identity_pins_before_network():
    root=Path(__file__).resolve().parents[1]
    # A direct filename execution sets sys.path[0] to scripts/, so a
    # package-qualified import is unavailable without PYTHONPATH.
    # Run the documented package/module CLI from the repo root even
    # when the environment is as minimal as a clean hosted runner.
    env={key:value for key,value in os.environ.items()
         if key not in ("PYTHONPATH","PYTHONHOME")}
    process=subprocess.run(
        [sys.executable,"-B","-m","scripts.verify_deployed_admin_release_identity"],
        cwd=root,env=env,
        capture_output=True,text=True,timeout=5,check=False,
    )
    assert process.returncode==2
    assert "--expected-image-sha256" in process.stderr
    assert "ModuleNotFoundError" not in process.stderr
    assert "Traceback" not in process.stderr
