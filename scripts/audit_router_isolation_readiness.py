#!/usr/bin/env python3
"""Read-only host-port + Docker-network exposure audit; never applies isolation.

A Docker wildcard binding is not proof of internet reachability. Docker
networks and external firewall state are separate authorization planes. This
tool never executes nft, iptables, firewall, Docker mutation or inference.
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

from scripts.admin_auth_readonly_canary import ADMIN_PATHS, probe
from scripts.audit_router_rollback_inventory import read_container

SCHEMA="auto-router.dual-plane-isolation-readiness.v1"
CONTAINER="git-router-1"
NETWORK_NAME=re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
IMAGE_ID=re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_NETS=12
REQUIRED_EXPOSURE_PATHS=frozenset(ADMIN_PATHS)


def docker_read(argv: list[str]) -> str:
    allowed=(
        len(argv)==5 and argv[:3]==["docker","inspect","--format"]
        and argv[-1]==CONTAINER
    ) or (
        len(argv)==6 and argv[:4]==["docker","network","inspect","--format"]
        and NETWORK_NAME.fullmatch(argv[-1]) is not None
    )
    if not allowed:
        raise ValueError("only bounded Docker read-only inspect operations permitted")
    r=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                     text=True,check=False,timeout=5)
    if r.returncode or len(r.stdout)>4096:
        raise RuntimeError("Docker read-only metadata unavailable")
    return r.stdout.strip()


def attached_networks(read=docker_read) -> tuple[str,...]:
    raw=read(["docker","inspect","--format",
              "{{range $name, $value := .NetworkSettings.Networks}}{{$name}},{{end}}",
              CONTAINER])
    values=raw.strip(",").split(",")
    if (not 1<=len(values)<=MAX_NETS or
            any(NETWORK_NAME.fullmatch(x) is None for x in values) or
            len(values)!=len(set(values))):
        raise ValueError("invalid Docker network attachment list")
    return tuple(sorted(values))


def network_metadata(name: str,read=docker_read) -> dict:
    if NETWORK_NAME.fullmatch(name) is None:
        raise ValueError("invalid Docker network name")
    raw=read(["docker","network","inspect","--format",
              "{{.Name}}|{{.Driver}}|{{.Internal}}",name])
    fields=raw.split("|")
    if (len(fields)!=3 or fields[0]!=name or fields[1] not in
            ("bridge","overlay","host","ipvlan","macvlan","none") or
            fields[2] not in ("true","false")):
        raise ValueError("ambiguous Docker network identity")
    return {"name":name,"driver":fields[1],"internal_only":fields[2]=="true"}


def evaluate(*,before:dict,after:dict,networks_before:tuple[str,...],
             networks_after:tuple[str,...],network_info:list[dict],
             canary:dict) -> dict:
    if before!=after or networks_before!=networks_after:
        raise ValueError("container/image/port/network changed during audit")
    if IMAGE_ID.fullmatch(before.get("immutable_image_id","")) is None or not before.get("running"):
        raise ValueError("router image identity missing or router not running")
    if sorted(x["name"] for x in network_info)!=list(networks_before):
        raise ValueError("missing or duplicate Docker network evidence")
    if any(type(x.get("internal_only")) is not bool
           or x.get("driver") not in ("bridge","overlay","host","ipvlan","macvlan","none")
           for x in network_info):
        raise ValueError("invalid network metadata")
    rows=canary.get("results",[])
    if (not isinstance(rows,list) or len(rows)!=len(ADMIN_PATHS)
            or any(not isinstance(x,dict) for x in rows)
            or [x["path"] for x in rows]!=list(ADMIN_PATHS)):
        raise ValueError("incomplete or reordered six-admin-path proof")
    statuses=[x.get("status") for x in rows]
    if any(type(x) is not int or not 100<=x<=599 for x in statuses):
        raise ValueError("missing/invalid admin HTTP status")
    all_denied=all(x==401 for x in statuses)
    if canary.get("all_denied") is not all_denied:
        raise ValueError("canary all_denied flag inconsistent with statuses")
    host_ips=before.get("host_port_8088_bindings")
    if not isinstance(host_ips,list) or not host_ips or any(
        type(x) is not str for x in host_ips
    ):
        raise ValueError("router host binding not established")
    wildcard_v4="0.0.0.0" in host_ips
    wildcard_v6="::" in host_ips
    other_host_ips=sorted(set(host_ips)-{"127.0.0.1","::1","0.0.0.0","::"})
    external_scope=wildcard_v4 or wildcard_v6 or bool(other_host_ips)
    non_internal=[x["name"] for x in network_info if not x["internal_only"]]
    return {
        "schema":SCHEMA,
        "read_only":True,"container_lifecycle_mutation":False,
        "network_rules_modified":False,"credentials_used":False,
        "response_bodies_read":False,"external_reachability_probed":False,
        "running_image_id":before["immutable_image_id"],
        "live_instance_unchanged":True,
        "host_published_8088":{
            "addresses":host_ips,
            "wildcard_ipv4":wildcard_v4,
            "wildcard_ipv6":wildcard_v6,
            "nonloopback_or_wildcard_binding":external_scope,
            "actual_external_reachability_verified":False,
            "effective_host_firewall_rules_inspected":False,
            "upstream_gateway_forwarding_verified":False,
        },
        "attached_docker_networks":network_info,
        "non_internal_docker_networks":non_internal,
        "docker_peer_acl_and_intra_bridge_access_verified":False,
        "anonymous_admin_get_statuses":[
            {"path":row["path"],"status":row["status"]} for row in rows
        ],
        "anonymous_admin_get_200_count":sum(x==200 for x in statuses),
        "all_six_admin_gets_deny":all_denied,
        "isolation_plan_options":{
            "traffic_isolation":{
                "untrusted_ipv4_and_ipv6_host_ingress_control":"UNVERIFIED",
                "docker_peer_ingress_scope_and_client_allowlist":"UNVERIFIED",
                "authorized_client_functional_test":"UNVERIFIED",
                "independent_external_denial_probe":"UNVERIFIED",
                "operator_review_and_recovery_test":"UNVERIFIED",
            },
            "hardened_image_replacement":{
                "approved_immutable_image_and_installed_source":"UNVERIFIED",
                "live_six_path_anonymous_401_and_operator_canary":"UNVERIFIED",
                "secure_fallback_or_external_containment":"UNVERIFIED",
                "operator_cutover_approval":"UNVERIFIED",
            },
        },
        "traffic_isolation_approved":False,
        "rollback_to_old_exposed_image_allowed":False,
        "production_ready":False,
        "cutover_authorized":False,
        "gate":"HOLD",
    }


def audit(*,read=docker_read,status_probe=probe)->dict:
    before=read_container(read)
    attached=attached_networks(read)
    info=[network_metadata(name,read) for name in attached]
    canary=status_probe(8088)
    after=read_container(read)
    attached_after=attached_networks(read)
    return evaluate(before=before,after=after,networks_before=attached,
                    networks_after=attached_after,network_info=info,canary=canary)


def write_private(path:Path,data:dict)->None:
    if (path.parent!=Path("/tmp") or
            not path.name.startswith("router-dual-plane-isolation-") or
            path.suffix!=".json" or path.exists() or path.is_symlink()):
        raise ValueError("receipt must be new /tmp/router-dual-plane-isolation-*.json")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|
               getattr(os,"O_NOFOLLOW",0),0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("receipt path is not regular file")
        f.write(json.dumps(data,sort_keys=True,allow_nan=False)+"\n")
        f.flush()
        os.fsync(f.fileno())


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-read-only",action="store_true")
    parser.add_argument("--out",type=Path)
    args=parser.parse_args()
    if not args.explicit_read_only:
        parser.error("explicit read-only inventory selection required")
    try:
        result=audit()
        if args.out is not None:
            write_private(args.out,result)
    except (ValueError,RuntimeError,OSError,KeyError,TypeError,
            subprocess.TimeoutExpired,json.JSONDecodeError) as e:
        print(json.dumps({"schema":SCHEMA,"gate":"HOLD","production_ready":False,
                          "error_type":type(e).__name__},sort_keys=True))
        return 2
    print(json.dumps({
        "schema":SCHEMA,"gate":result["gate"],
        "wildcard_ipv4":result["host_published_8088"]["wildcard_ipv4"],
        "wildcard_ipv6":result["host_published_8088"]["wildcard_ipv6"],
        "network_count":len(result["attached_docker_networks"]),
        "anonymous_admin_get_200_count":result["anonymous_admin_get_200_count"],
        "actual_external_reachability_verified":False,
        "production_ready":False},sort_keys=True))
    return 2


if __name__=="__main__":
    sys.exit(main())
