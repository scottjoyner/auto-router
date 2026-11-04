#!/usr/bin/env python3
"""Read-only Docker network peer inventory. Peer != verified application client.

Safe for a loaded fleet host: no docker exec, Docker lifecycle changes,
network requests, credentials, logs, environment values, firewall inspection
or inference. Treat all discovered peer names as possible dependencies until
an independently approved nonmutating client-compatibility test verifies them.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from scripts.audit_router_rollback_inventory import read_container
from scripts.audit_router_isolation_readiness import (
    CONTAINER, NETWORK_NAME, attached_networks, docker_read, network_metadata,
)

SCHEMA="auto-router.network-peer-compatibility-inventory.v1"
CONTAINER_NAME=re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
MAX_PEERS=128
PEER_TEMPLATE="{{range .Containers}}{{.Name}} {{end}}"


def read_peers(name: str, *, read=docker_read) -> tuple[str,...]:
    if not isinstance(name,str) or NETWORK_NAME.fullmatch(name) is None:
        raise ValueError("invalid Docker network name")
    value=read(["docker","network","inspect","--format",PEER_TEMPLATE,name])
    items=value.split()
    if (not 1<=len(items)<=MAX_PEERS
        or any(CONTAINER_NAME.fullmatch(item) is None for item in items)
        or len(items)!=len(set(items))):
        raise ValueError("invalid, duplicate or empty Docker peer list")
    if CONTAINER not in items:
        raise ValueError("router absent from claimed attached network")
    return tuple(sorted(items))


def evaluate(*,before:dict,after:dict,network_names_before:tuple[str,...],
             network_names_after:tuple[str,...],members_before:dict[str,tuple[str,...]],
             members_after:dict[str,tuple[str,...]],networks:list[dict])->dict:
    if before!=after or network_names_before!=network_names_after or members_before!=members_after:
        raise ValueError("container identity or network membership changed during audit")
    if not before.get("running") or not isinstance(before.get("immutable_image_id"),str):
        raise ValueError("running router identity not established")
    if (set(members_before)!=set(network_names_before)
        or {x.get("name") for x in networks}!=set(network_names_before)
        or len(networks)!=len(network_names_before)):
        raise ValueError("missing Docker network or peer evidence")
    for network in networks:
        if type(network.get("internal_only")) is not bool:
            raise ValueError("Docker network internal status unverified")
    all_peers=sorted({member for members in members_before.values() for member in members
                      if member!=CONTAINER})
    membership={name:[peer for peer in members_before[name] if peer!=CONTAINER]
                for name in network_names_before}
    # Presence in a shared Docker network does not prove any API traffic,
    # service role, firewall bypass, or compatibility after a port change.
    return {
        "schema":SCHEMA,
        "read_only":True,
        "operator_credentials_used":False,
        "network_requests_sent":False,
        "container_exec_called":False,
        "network_or_firewall_mutations":False,
        "external_reachability_verified":False,
        "router_image_id":before["immutable_image_id"],
        "router_published_host_port_8088":before["host_port_8088_bindings"],
        "network_membership_stable_through_audit":True,
        "network_properties":networks,
        "potential_peers_by_docker_network":membership,
        "potential_peer_names_unique":all_peers,
        "potential_peer_count":len(all_peers),
        "peer_name_is_proof_of_router_api_usage":False,
        "actual_api_dependencies_verified":False,
        "legitimate_client_allowlist_verified":False,
        "authorized_client_status_test_performed":False,
        "docker_peer_ingress_isolation_proven":False,
        "host_ingress_isolation_proven":False,
        "rollback_approved":False,
        "production_ready":False,
        "gate":"HOLD",
    }


def audit(*,read=docker_read)->dict:
    before=read_container(read)
    names=attached_networks(read)
    peers={name:read_peers(name,read=read) for name in names}
    meta=[network_metadata(name,read) for name in names]
    peers_again={name:read_peers(name,read=read) for name in names}
    after=read_container(read)
    names_again=attached_networks(read)
    return evaluate(before=before,after=after,
                    network_names_before=names,network_names_after=names_again,
                    members_before=peers,members_after=peers_again,networks=meta)


def write_private(path:Path,payload:dict)->None:
    if (path.parent!=Path("/tmp") or
        not path.name.startswith("router-network-peers-") or
        path.suffix!=".json" or path.exists() or path.is_symlink()):
        raise ValueError("receipt must be a new /tmp/router-network-peers-*.json")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|
               getattr(os,"O_NOFOLLOW",0),0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as output:
        if not stat.S_ISREG(os.fstat(output.fileno()).st_mode):
            raise ValueError("receipt must be a regular file")
        output.write(json.dumps(payload,sort_keys=True,allow_nan=False)+"\n")
        output.flush()
        os.fsync(output.fileno())


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-read-only",action="store_true")
    parser.add_argument("--out",type=Path)
    args=parser.parse_args()
    if not args.explicit_read_only:
        parser.error("explicit read-only invocation required")
    try:
        evidence=audit()
        if args.out is not None:
            write_private(args.out,evidence)
    except (ValueError,OSError,RuntimeError,KeyError,TypeError,
            subprocess.TimeoutExpired,json.JSONDecodeError) as error:
        print(json.dumps({"schema":SCHEMA,"gate":"HOLD",
                          "error_type":type(error).__name__,
                          "production_ready":False},sort_keys=True))
        return 2
    print(json.dumps({
        "schema":SCHEMA,
        "gate":"HOLD",
        "potential_peers_by_docker_network":evidence["potential_peers_by_docker_network"],
        "potential_peer_count":evidence["potential_peer_count"],
        "actual_api_dependencies_verified":False,
        "production_ready":False,
    },sort_keys=True))
    return 2


if __name__=="__main__":
    sys.exit(main())
