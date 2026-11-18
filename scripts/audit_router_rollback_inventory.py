#!/usr/bin/env python3
"""Read-only inventory for safe Auto-Router rollback planning.

Records that locally cached images exist, NOT that they are secure,
approved, independently attested, or viable rollback candidates.
Never starts a container, inspects environment/credentials, or alters routing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from scripts.admin_auth_readonly_canary import ADMIN_PATHS, probe

CONTAINER = "git-router-1"
PORT = 8088
SCHEMA = "auto-router.readonly-rollback-image-inventory.v1"
IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_CANDIDATES = 8
DEFAULT_CANDIDATES = (
    "sha256:8e4676c96bd8e03723b3ce1aa9f0a0791f6d4a1566d0d6d8aeeb1694a6e908f1",
    "sha256:4023ff9ff6ba511f5e68d8e82deb149a42b234b6d7b4fd45c4661f7b347153d9",
    "sha256:f6425386ad566ca5a1e3608fa2160167f5292a30db09b587f3153f480ddeba77",
)


def validate_images(values: list[str] | tuple[str,...]) -> tuple[str,...]:
    if not isinstance(values,(list,tuple)) or not 1 <= len(values) <= MAX_CANDIDATES:
        raise ValueError("require 1 to 8 explicit immutable Docker image IDs")
    if any(type(x) is not str or IMAGE_RE.fullmatch(x) is None for x in values):
        raise ValueError("only full immutable lowercase Docker sha256 IDs are accepted")
    if len(set(values)) != len(values):
        raise ValueError("duplicate candidate image digest")
    return tuple(values)


def read_docker(argv: list[str]) -> str:
    """No command dispatch beyond fixed argv-shaped inspect calls."""
    if (len(argv) < 4 or argv[0:2] != ["docker","inspect"]
            and argv[0:3] != ["docker","image","inspect"]):
        raise ValueError("only Docker inspect permitted")
    p=subprocess.run(argv,stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL,text=True,
                     check=False,timeout=7)
    if p.returncode:
        raise RuntimeError("Docker inspection failed")
    if len(p.stdout) > 8192:
        raise RuntimeError("Docker inspection exceeded bounded output")
    return p.stdout.strip()


def read_container(read=read_docker) -> dict:
    raw=read([
        "docker","inspect","--format",
        "{{.Image}}|{{.State.Running}}|{{.State.StartedAt}}|{{json .NetworkSettings.Ports}}",
        CONTAINER,
    ])
    fields=raw.split("|",3)
    if (len(fields)!=4 or IMAGE_RE.fullmatch(fields[0]) is None
            or fields[1] not in ("true","false")
            or not fields[2]):
        raise ValueError("Docker container identity ambiguous")
    bindings=json.loads(fields[3])
    if not isinstance(bindings,dict):
        raise ValueError("Docker port mappings invalid")
    ports=bindings.get("8088/tcp")
    if ports is None:
        host_ips=[]
    elif isinstance(ports,list) and all(
        isinstance(x,dict) and isinstance(x.get("HostIp"),str)
        and isinstance(x.get("HostPort"),str)
        for x in ports
    ):
        host_ips=[x["HostIp"] for x in ports if x["HostPort"]=="8088"]
        if len(host_ips)!=len(ports):
            raise ValueError("unexpected published host-port mapping")
    else:
        raise ValueError("invalid Docker host-port binding")
    return {
        "immutable_image_id":fields[0],
        "running":fields[1]=="true",
        "started_at":fields[2],
        "host_port_8088_bindings":host_ips,
        "public_host_port_8088":any(x in ("0.0.0.0","::") for x in host_ips),
    }


def read_image(image_id: str, read=read_docker) -> dict:
    if IMAGE_RE.fullmatch(image_id) is None:
        raise ValueError("unsafe or mutable image reference")
    raw=read(["docker","image","inspect","--format","{{.Id}}|{{.Created}}",image_id])
    fields=raw.split("|")
    if (len(fields)!=2 or fields[0]!=image_id or not fields[1]):
        raise ValueError("cached image metadata not identity-bound")
    return {"image_id":fields[0], "created_at":fields[1]}


def evaluate(*, live: dict, after: dict,
             images: list[dict], admin: dict) -> dict:
    if live!=after:
        raise ValueError("container image, ports, or start identity changed during audit")
    statuses=admin.get("results",[])
    if (not isinstance(statuses,list) or len(statuses)!=len(ADMIN_PATHS)
        or [x.get("path") for x in statuses if isinstance(x,dict)]
            != list(ADMIN_PATHS)):
        raise ValueError("incomplete or unordered status-only admin canary")
    findings=[]
    exposures=[x["path"] for x in statuses if type(x.get("status")) is int
               and x["status"]==200]
    all_denied=all(type(x.get("status")) is int and x["status"]==401
                   for x in statuses)
    if all_denied != bool(admin.get("all_denied")):
        raise ValueError("inconsistent admin-denial receipt")
    for row in images:
        digest=row.get("image_id")
        if not isinstance(digest,str) or IMAGE_RE.fullmatch(digest) is None:
            raise ValueError("candidate image identity not verified")
        cached=row.get("cached") is True
        is_live=digest==live["immutable_image_id"]
        findings.append({
            "immutable_image_id":digest,
            "cached_locally":cached,
            "created_at":row.get("created_at") if cached else None,
            "is_current_live_image":is_live,
            "source_code_identity_verified":False,
            "anonymous_admin_denial_verified":False,
            "operator_client_verified":False,
            "independently_approved_rollback_drill":False,
            "production_rollback_approved":False,
            "assessment":"KNOWN_LIVE_EXPOSURE_DENY" if is_live and not all_denied
                         else "CACHED_UNVERIFIED_HOLD" if cached
                         else "MISSING_IMAGE_HOLD",
        })
    return {
        "schema":SCHEMA,
        "read_only":True,
        "container_lifecycle_mutations":False,
        "external_provider_calls":False,
        "operator_credentials_used":False,
        "response_bodies_read":False,
        "live_container":live,
        "live_anonymous_admin_statuses":[{
            "path":x["path"],"status":x.get("status")
        } for x in statuses],
        "live_unauthenticated_200_paths":exposures,
        "live_all_six_admin_gets_deny":all_denied,
        "candidate_images":findings,
        "cached_image_is_approved_rollback":False,
        "rollback_to_existing_live_image_safe":False,
        "traffic_isolation_independently_verified":False,
        "rollback_operator_approval_verified":False,
        "security_safe_rollback_available":False,
        "cutover_authorized":False,
        "production_ready":False,
        "gate":"HOLD",
    }


def audit(candidates: tuple[str,...]=DEFAULT_CANDIDATES, *,
          read=read_docker, status_probe=probe) -> dict:
    selected=validate_images(candidates)
    before=read_container(read)
    results=[]
    for image_id in selected:
        try:
            metadata=read_image(image_id,read)
            results.append({**metadata,"cached":True})
        except RuntimeError:
            results.append({"image_id":image_id,"cached":False})
    admin=status_probe(PORT)
    after=read_container(read)
    return evaluate(live=before,after=after,images=results,admin=admin)


def write_private(path: Path, payload: dict) -> None:
    if (path.parent!=Path("/tmp") or
        not path.name.startswith("router-rollback-inventory-") or
        path.suffix!=".json" or path.exists() or path.is_symlink()):
        raise ValueError("receipt must be new /tmp/router-rollback-inventory-*.json")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|
               getattr(os,"O_NOFOLLOW",0),0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600:
            raise ValueError("receipt permissions invalid")
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            f.write(json.dumps(payload,sort_keys=True,allow_nan=False)+"\n")
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:os.close(fd)
        except OSError:pass
        raise


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-read-only",action="store_true")
    parser.add_argument("--candidate-image-id",action="append")
    parser.add_argument("--out",type=Path)
    args=parser.parse_args()
    if not args.explicit_read_only:
        parser.error("explicit read-only inventory selection required")
    try:
        candidates=validate_images(args.candidate_image_id or DEFAULT_CANDIDATES)
        result=audit(candidates)
        if args.out is not None:
            write_private(args.out,result)
    except (ValueError,RuntimeError,OSError,KeyError,TypeError,
            subprocess.TimeoutExpired,json.JSONDecodeError) as e:
        print(json.dumps({
            "schema":SCHEMA,"gate":"HOLD",
            "error_type":type(e).__name__,"cutover_authorized":False,
            "production_ready":False,
        },sort_keys=True))
        return 2
    print(json.dumps({
        "schema":SCHEMA,"gate":"HOLD",
        "live_image_id":result["live_container"]["immutable_image_id"],
        "live_anonymous_admin_200_count":len(result["live_unauthenticated_200_paths"]),
        "candidate_count":len(result["candidate_images"]),
        "cached_candidate_count":sum(x["cached_locally"] for x in result["candidate_images"]),
        "security_safe_rollback_available":False,
        "cutover_authorized":False,
    },sort_keys=True))
    return 2  # Never promote a cached image to rollback or production.


if __name__=="__main__":
    sys.exit(main())
