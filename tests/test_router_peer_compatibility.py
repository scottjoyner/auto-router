"""Synthetic Docker peer graphs only, no container calls or real network traffic."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.audit_router_peer_compatibility import (
    audit, evaluate, read_peers, write_private,
)

IMAGE="sha256:"+"a"*64
NETWORK_NAMES=("assistx-fleet_default","git_default")
STARTED="2026-11-04T09:15:05Z"
HOST_PORTS={"8088/tcp":[
    {"HostIp":"0.0.0.0","HostPort":"8088"},
    {"HostIp":"::","HostPort":"8088"},
]}
NETWORK_PEERS={
    "assistx-fleet_default":"git-router-1 ",
    "git_default":"neo4j assistx-api git-router-1 git-redis-1 auto-assign ",
}


def fake_docker(*,restart=False,change_membership=False,vanish_router=False,
                bad_network=False,change_network_count=False):
    calls=[]
    instance_reads=[0]
    membership_reads={}
    def read(args):
        calls.append(tuple(args))
        if args[:3]==["docker","inspect","--format"]:
            if "range" in args[-2]:
                return ("git_default," if change_network_count
                        else ",".join(NETWORK_NAMES)+",")
            instance_reads[0]+=1
            started=(STARTED+"-restart" if restart and instance_reads[0]>1
                     else STARTED)
            return IMAGE+"|true|"+started+"|"+json.dumps(HOST_PORTS)
        if args[:4]==["docker","network","inspect","--format"]:
            name=args[-1]
            assert name in NETWORK_NAMES
            if "range" in args[-2]:
                membership_reads[name]=membership_reads.get(name,0)+1
                peers=NETWORK_PEERS[name]
                if vanish_router:
                    peers=peers.replace("git-router-1 ","")
                if change_membership and membership_reads[name]>1:
                    peers=peers.replace("assistx-api ","") if name=="git_default" else peers
                return peers
            if bad_network and name==NETWORK_NAMES[0]:
                return name+"|bridge|maybe"
            return name+"|bridge|false"
        raise AssertionError(args)
    return read,calls


def test_actual_shaped_peer_graph_is_held_and_not_assumed_to_be_clients():
    read,calls=fake_docker()
    result=audit(read=read)
    assert result["gate"]=="HOLD"
    assert result["network_membership_stable_through_audit"] is True
    assert result["potential_peers_by_docker_network"]["assistx-fleet_default"]==[]
    assert result["potential_peers_by_docker_network"]["git_default"]==[
        "assistx-api","auto-assign","git-redis-1","neo4j"]
    assert result["potential_peer_count"]==4
    assert result["actual_api_dependencies_verified"] is False
    assert result["legitimate_client_allowlist_verified"] is False
    assert result["authorized_client_status_test_performed"] is False
    assert result["docker_peer_ingress_isolation_proven"] is False
    assert result["host_ingress_isolation_proven"] is False
    assert result["production_ready"] is False
    assert all(call[0]=="docker" and call[1] in ("inspect","network")
               for call in calls)
    assert all("exec" not in call and "restart" not in call and "run" not in call
               for call in calls)


@pytest.mark.parametrize("change",[
    {"restart":True},
    {"change_membership":True},
    {"vanish_router":True},
    {"bad_network":True},
])
def test_race_missing_router_or_invalid_network_denies(change):
    read,_=fake_docker(**change)
    with pytest.raises(ValueError):
        audit(read=read)


def test_one_network_with_peers_is_still_a_hold():
    read,_=fake_docker(change_network_count=True)
    d=audit(read=read)
    assert list(d["potential_peers_by_docker_network"])==["git_default"]
    assert d["potential_peer_count"]==4
    assert not d["actual_api_dependencies_verified"]


@pytest.mark.parametrize("data",[
    "",
    "git-router-1 git-router-1",
    "router-test",
    "git-router-1 evil/name",
    "git-router-1"+" "+" ".join(f"node{i}" for i in range(129)),
])
def test_invalid_empty_duplicate_nonmember_or_oversized_peer_list(data):
    with pytest.raises(ValueError):
        read_peers("git_default",read=lambda args:data)


def test_peer_names_are_sorted_without_claiming_service_usage():
    got=read_peers("git_default",
                   read=lambda args:"neo4j git-router-1 auto-assign assistx-api ")
    assert got==("assistx-api","auto-assign","git-router-1","neo4j")


@pytest.mark.parametrize("name",["","../foo","git default","-x","network/unsafe"])
def test_network_name_cannot_escape_docker_inspection_scope(name):
    with pytest.raises(ValueError):
        read_peers(name,read=lambda args:"git-router-1")


def test_modified_container_identity_denied_even_if_networks_same():
    before={"immutable_image_id":IMAGE,"running":True,
            "host_port_8088_bindings":["0.0.0.0","::"]}
    after={**before,"immutable_image_id":"sha256:"+"b"*64}
    with pytest.raises(ValueError):
        evaluate(before=before,after=after,network_names_before=NETWORK_NAMES,
                 network_names_after=NETWORK_NAMES,
                 members_before={x:("git-router-1",) for x in NETWORK_NAMES},
                 members_after={x:("git-router-1",) for x in NETWORK_NAMES},
                 networks=[{"name":x,"driver":"bridge","internal_only":False}
                           for x in NETWORK_NAMES])


def test_private_receipt_created_once_without_overwrite(tmp_path):
    path=Path("/tmp")/f"router-network-peers-unit-{os.getpid()}.json"
    try:
        write_private(path,{"gate":"HOLD","production_ready":False})
        assert path.stat().st_mode & 0o777==0o600
        assert json.loads(path.read_text())["gate"]=="HOLD"
        with pytest.raises(ValueError):
            write_private(path,{"gate":"PASS"})
    finally:
        path.unlink(missing_ok=True)
    with pytest.raises(ValueError):
        write_private(tmp_path/"router-network-peers-unit.json",{"gate":"HOLD"})


def test_cli_requires_explicit_read_only_consent():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run(
        [sys.executable,"-B","-m","scripts.audit_router_peer_compatibility"],
        cwd=root,capture_output=True,text=True,check=False,timeout=4,
    )
    assert result.returncode!=0
    assert "explicit-read-only" in result.stderr
    assert not result.stdout
