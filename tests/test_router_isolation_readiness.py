"""Offline, synthetic dual-plane isolation audit tests; no host firewall access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.admin_auth_readonly_canary import ADMIN_PATHS
from scripts.audit_router_isolation_readiness import (
    audit, attached_networks, docker_read, evaluate, network_metadata, write_private,
)

IMAGE="sha256:"+"a"*64
STARTED="2026-11-04T09:15:05Z"
NETWORKS=("assistx-fleet_default","git_default")
PORTS={"8088/tcp":[{"HostIp":"0.0.0.0","HostPort":"8088"},
                    {"HostIp":"::","HostPort":"8088"}]}


def fake_reader(*,restart=False,public=True,internal=False,
                missing_network=False,corrupt_network=False):
    calls=[]
    container_count=[0]
    def read(args):
        calls.append(tuple(args))
        assert args[0]=="docker"
        if args[:3]==["docker","inspect","--format"]:
            if "range" in args[-2]:
                return ",".join(NETWORKS[:-1] if missing_network else NETWORKS)+","
            container_count[0]+=1
            published=PORTS if public else {
                "8088/tcp":[{"HostIp":"127.0.0.1","HostPort":"8088"}]
            }
            stamp=STARTED+"-changed" if restart and container_count[0]>1 else STARTED
            return f"{IMAGE}|true|{stamp}|{json.dumps(published)}"
        if args[:4]==["docker","network","inspect","--format"]:
            name=args[-1]
            if corrupt_network and name==NETWORKS[0]:
                return f"{name}|bridge|maybe"
            return f"{name}|bridge|{'true' if internal else 'false'}"
        raise AssertionError(args)
    return read,calls


def statuses(*,leaked=True):
    values=[200,200,200,200,200,401] if leaked else [401]*6
    return {"all_denied":not leaked,
            "results":[{"path":path,"status":s}
                       for path,s in zip(ADMIN_PATHS,values)]}


def test_real_wildcard_and_both_docker_planes_is_hold():
    read,calls=fake_reader()
    d=audit(read=read,status_probe=lambda port:statuses())
    assert d["gate"]=="HOLD"
    assert d["host_published_8088"]["wildcard_ipv4"]
    assert d["host_published_8088"]["wildcard_ipv6"]
    assert d["host_published_8088"]["actual_external_reachability_verified"] is False
    assert not d["host_published_8088"]["effective_host_firewall_rules_inspected"]
    assert set(d["non_internal_docker_networks"])==set(NETWORKS)
    assert d["anonymous_admin_get_200_count"]==5
    assert d["rollback_to_old_exposed_image_allowed"] is False
    assert not d["cutover_authorized"]
    assert all(c[0]=="docker" and c[1] in ("inspect","network") for c in calls)
    assert all("run" not in c and "rm" not in c and "restart" not in c
               for c in calls)


def test_loopback_bind_does_not_imply_safe_docker_peer_scope():
    read,_=fake_reader(public=False)
    d=audit(read=read,status_probe=lambda port:statuses(leaked=False))
    assert not d["host_published_8088"]["nonloopback_or_wildcard_binding"]
    assert len(d["non_internal_docker_networks"])==2
    assert not d["docker_peer_acl_and_intra_bridge_access_verified"]
    assert not d["production_ready"]


def test_internal_bridges_do_not_imply_external_isolation():
    read,_=fake_reader(internal=True)
    d=audit(read=read,status_probe=lambda port:statuses(leaked=False))
    assert not d["non_internal_docker_networks"]
    assert not d["host_published_8088"]["actual_external_reachability_verified"]
    assert not d["traffic_isolation_approved"]


def test_restart_between_first_and_last_inspection_refused():
    read,_=fake_reader(restart=True)
    with pytest.raises(ValueError,match="changed"):
        audit(read=read,status_probe=lambda port:statuses())


def test_missing_network_metadata_is_refused():
    read,_=fake_reader(missing_network=True)
    d=audit(read=read,status_probe=lambda port:statuses())
    assert len(d["attached_docker_networks"])==1
    assert d["gate"]=="HOLD"


def test_corrupted_network_metadata_refused():
    read,_=fake_reader(corrupt_network=True)
    with pytest.raises(ValueError,match="ambiguous"):
        audit(read=read,status_probe=lambda port:statuses())


@pytest.mark.parametrize("mutation",["missing","reorder","flag","status"])
def test_forged_or_missing_http_evidence_denied(mutation):
    read,_=fake_reader()
    def bad(port):
        d=statuses()
        if mutation=="missing":
            d["results"].pop()
        elif mutation=="reorder":
            d["results"][0],d["results"][1]=d["results"][1],d["results"][0]
        elif mutation=="flag":
            d["all_denied"]=True
        elif mutation=="status":
            d["results"][1]["status"]=None
        return d
    with pytest.raises(ValueError):
        audit(read=read,status_probe=bad)


def test_attached_network_reordered_is_normalized():
    a=attached_networks(lambda args:"git_default,assistx-fleet_default,")
    b=attached_networks(lambda args:"assistx-fleet_default,git_default,")
    assert a==b==NETWORKS


@pytest.mark.parametrize("values",[
    "",
    "git_default,git_default,",
    "unsafe/network",
    ",".join(f"network{i}" for i in range(13)),
])
def test_invalid_network_list_refused(values):
    with pytest.raises(ValueError):
        attached_networks(lambda args:values)


def test_malformed_network_identity_refused():
    with pytest.raises(ValueError):
        network_metadata("bad/network",lambda args:"irrelevant")
    with pytest.raises(ValueError):
        network_metadata("git_default",lambda args:"git_default|bridge|UNKNOWN")


def test_docker_command_allowlist_rejects_mutations():
    for command in (
        ["docker","rm","git-router-1"],
        ["docker","network","disconnect","git_default","git-router-1"],
        ["docker","restart","git-router-1"],
    ):
        with pytest.raises(ValueError):
            docker_read(command)


def test_private_receipt_exclusive_and_fails_on_insecure_path(tmp_path):
    path=Path("/tmp")/f"router-dual-plane-isolation-unit-{os.getpid()}.json"
    try:
        write_private(path,{"gate":"HOLD","production_ready":False})
        assert path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError):
            write_private(path,{"gate":"PASS"})
    finally:
        path.unlink(missing_ok=True)
    with pytest.raises(ValueError):
        write_private(tmp_path/"router-dual-plane-isolation-unit.json",
                      {"gate":"HOLD"})


def test_live_lease_and_network_list_both_required():
    d={
        "immutable_image_id":IMAGE,"running":True,"started_at":STARTED,
        "host_port_8088_bindings":["0.0.0.0","::"],
    }
    with pytest.raises(ValueError,match="changed"):
        evaluate(before=d,after=d,networks_before=NETWORKS,
                 networks_after=("git_default",),
                 network_info=[
                     {"name":x,"driver":"bridge","internal_only":False}
                     for x in NETWORKS],
                 canary=statuses())


def test_cli_refuses_without_read_only_optin():
    root=Path(__file__).resolve().parents[1]
    p=subprocess.run([sys.executable,"-B","-m",
                      "scripts.audit_router_isolation_readiness"],
                     cwd=root,stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE,text=True,check=False,timeout=5)
    assert p.returncode!=0
    assert "explicit-read-only" in p.stderr
    assert not p.stdout
