#!/usr/bin/env python3
"""Read-only, fail-closed Docker image/source/port/admin gate for a human-approved cutover.

No container lifecycle changes, secrets, response body reads, shell execution,
external network, inference requests, or authenticated requests. This is an
additional deployed proof gate, not permission to deploy.
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import subprocess
import sys

from scripts.admin_auth_readonly_canary import ADMIN_PATHS, probe

HEX64 = re.compile(r"^[0-9a-f]{64}$")
CONTAINER = "git-router-1"
ALLOWED_CONTAINERS = frozenset(("git-router-1", "auto-router-smoke"))
ENTRYPOINT = "auto_router.secure_live:app"
FILES = ("main.py", "security.py", "main_live.py", "secure_live.py")
# Executed only inside the existing named container. Does not import app code
# or read any environment variables, credentials, key files or user data.
FILE_HASH_CODE = """
import hashlib,json,sysconfig
from pathlib import Path
from importlib.machinery import PathFinder
root=(Path(sysconfig.get_path('purelib'))/'auto_router').resolve()
# Find the package without importing or executing its application code.
spec=PathFinder.find_spec('auto_router')
locations=[] if spec is None else list(spec.submodule_search_locations or [])
if len(locations)!=1 or Path(locations[0]).resolve()!=root:
    raise SystemExit(3)
names=('main.py','security.py','main_live.py','secure_live.py')
print(json.dumps({name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                  for name in names},sort_keys=True))
"""


def _run(args: list[str]) -> str:
    child = subprocess.run(args, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True,
                           check=False, timeout=7)
    if child.returncode:
        raise RuntimeError("Docker read-only inspect command failed")
    if len(child.stdout)>8192:
        raise RuntimeError("Docker inspection output exceeded fixed limit")
    return child.stdout.strip()


def _sha(value: str, name: str) -> str:
    if not isinstance(value,str) or HEX64.fullmatch(value) is None:
        raise ValueError(f"invalid expected {name}: require full lowercase SHA-256")
    return value


def _identity_lease(read=_run, container: str=CONTAINER) -> str:
    if container not in ALLOWED_CONTAINERS:
        raise ValueError("only designated Docker research/prod containers permitted")
    raw=read(["docker","inspect","--format",
              "{{.Image}}|{{.State.StartedAt}}|{{.State.Running}}",container])
    fields=raw.split("|")
    if (len(fields)!=3 or not fields[0].startswith("sha256:")
            or not HEX64.fullmatch(fields[0][7:])
            or not fields[1] or fields[2] not in ("true","false")):
        raise RuntimeError("invalid running-container identity lease")
    return raw


CODE_ROOTS=("/app", "/code", "/workspace", "/opt/auto-router",
            "/usr/local/lib", "/usr/lib")


def code_mounts_safe(mounts: object) -> bool:
    """Reject source/runtime-path overlays without exposing host mount sources."""
    if not isinstance(mounts,list):
        return False
    for mount in mounts:
        if not isinstance(mount,dict):
            return False
        target=mount.get("Destination")
        if not isinstance(target,str) or not target.startswith("/") or "/../" in target:
            return False
        # The CI image has a deliberately mounted, data-only configuration
        # directory. This exact mount is not an import root; its parent, any
        # app source mount, and every other /app subtree remain denied.
        if target=="/app/config":
            continue
        if any(target==root or target.startswith(root+"/") for root in CODE_ROOTS):
            return False
    return True


def inspect(read=_run, container: str=CONTAINER) -> dict:
    """Only selected nonsecret Docker metadata and installed Python source hashes."""
    if container not in ALLOWED_CONTAINERS:
        raise ValueError("only designated Docker research/prod containers permitted")
    lease=_identity_lease(read,container)
    image=lease.split("|")[0]
    running = read(["docker", "inspect", "--format", "{{.State.Running}}", container])
    command = read(["docker", "inspect", "--format", "{{.Path}}", container])
    args = json.loads(read(["docker", "inspect", "--format", "{{json .Args}}", container]))
    bindings = json.loads(read([
        "docker","inspect","--format","{{json .NetworkSettings.Ports}}",container]))
    # Destination-only mount metadata is evaluated in memory. Never print
    # host-side mount Source paths or inspect mounted credentials.
    mounts = json.loads(read([
        "docker","inspect","--format","{{json .Mounts}}",container]))
    installed = json.loads(read([
        "docker","exec",container,"python3","-B","-c",FILE_HASH_CODE]))
    if (not isinstance(args,list) or not isinstance(bindings,dict)
            or set(installed)!=set(FILES)
            or any(not isinstance(s,str) or not HEX64.fullmatch(s)
                   for s in installed.values())):
        raise RuntimeError("container metadata or installed source proof malformed")
    if not image.startswith("sha256:") or not HEX64.fullmatch(image[7:]):
        raise RuntimeError("noncanonical Docker image identifier")
    return {
        "image_id":image,
        "identity_lease":lease,
        "running":running=="true",
        # A foreign ASGI app must not pass by mentioning the expected app
        # as an unrelated flag value. Uvicorn's first argument is its app.
        "expected_entrypoint":command in ("uvicorn","/usr/local/bin/uvicorn")
            and bool(args) and args[0] == ENTRYPOINT
            and args.count(ENTRYPOINT) == 1
            and not any(
                isinstance(a,str) and (
                    a in {"--app-dir", "--factory", "--reload", "--env-file"}
                    or a.startswith(("--app-dir=", "--env-file="))
                ) for a in args[1:]
            ),
        "code_mounts_clear":code_mounts_safe(mounts),
        "bindings":bindings,
        "installed_source_hashes":installed,
    }


def evaluate(actual: dict, expected_image: str, expected_files: dict,
             *, allow_public_bind: bool=False, status_probe=probe,
             port: int=8088) -> dict:
    expected_image = "sha256:"+_sha(expected_image,"Docker image")
    if set(expected_files)!=set(FILES):
        raise ValueError("exactly four installed source hashes are required")
    for name,digest in expected_files.items():
        _sha(digest,name)
    if type(port) is not int or port!=8088:
        raise ValueError("only designated Auto-Router loopback port 8088 is permitted")
    binding=actual.get("bindings",{}).get("8088/tcp")
    if not isinstance(binding,list) or not binding:
        ips=[]
    else:
        ips=[x.get("HostIp") if isinstance(x,dict) and x.get("HostPort")=="8088"
             else None for x in binding]
    valid_bind=bool(ips) and all(
        ip in (("127.0.0.1","::1") if not allow_public_bind
               else ("127.0.0.1","::1","0.0.0.0","::"))
        for ip in ips
    )
    # Every host-published port must be explicitly reviewed; otherwise an
    # unexpected secondary published port can circumvent the tested 8088 gate.
    bindings=actual.get("bindings",{})
    no_extra_publications=isinstance(bindings,dict) and all(
        key=="8088/tcp" or value is None
        for key,value in bindings.items()
    )
    # Read only HTTP status. This is NOT an authenticated positive-client test.
    canary=status_probe(port)
    rows=canary.get("results",[])
    complete_denials=(
        canary.get("all_denied") is True
        and isinstance(rows,list)
        and len(rows)==len(ADMIN_PATHS)
        and [r.get("path") if isinstance(r,dict) else None for r in rows]
            ==list(ADMIN_PATHS)
        and all(type(r.get("status")) is int and r["status"]==401
                for r in rows)
    )
    checks={
        "container_running":actual.get("running") is True,
        "immutable_image_id_matches_pinned_build":actual.get("image_id")==expected_image,
        "expected_secure_live_entrypoint":actual.get("expected_entrypoint") is True,
        "no_unreviewed_runtime_source_mounts":actual.get("code_mounts_clear") is True,
        "installed_source_hashes_match_pinned_build":actual.get("installed_source_hashes")==expected_files,
        "host_port_bind_is_explicitly_approved":valid_bind,
        "no_unreviewed_published_host_ports":no_extra_publications,
        "anonymous_admin_gets_all_401":complete_denials,
    }
    return {
        "schema":"auto-router.deployed-pinned-image-admin-gate.v1",
        "read_only":True,
        "production_mutation":False,
        "response_bodies_read":False,
        "authenticated_operator_behavior_verified":False,
        "expected_image_id":expected_image,
        "actual_image_id":actual.get("image_id"),
        "source_hashes_match":checks["installed_source_hashes_match_pinned_build"],
        "ports_checked":["8088/tcp"],
        "anonymous_admin_statuses":[{
            "path":r["path"],"status":r.get("status")
        } for r in rows if isinstance(r,dict)],
        "checks":checks,
        "gate":"PASS_PRE_CUTOVER_INSPECTION_ONLY" if all(checks.values()) else "HOLD",
        "production_ready":False,
    }


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-image-sha256",required=True)
    for name in FILES:
        parser.add_argument("--expected-"+name.removesuffix(".py").replace("_","-")+"-sha256",
                            required=True)
    parser.add_argument("--allow-public-bind",action="store_true")
    args=parser.parse_args()
    names={name:getattr(args,"expected_"+name.removesuffix(".py")+"_sha256")
           for name in FILES}
    try:
        # Expected values must be from separately reviewed builder provenance.
        # This program NEVER derives desired hashes from the live container.
        before=inspect()
        result=evaluate(before,args.expected_image_sha256,names,
                        allow_public_bind=args.allow_public_bind)
        # A successful 401 response must belong to the same running
        # container/image as the hashes inspected before the request.
        after=_identity_lease()
        result["checks"]["same_running_container_through_probe"]=(
            after==before["identity_lease"])
        if not result["checks"]["same_running_container_through_probe"]:
            result["gate"]="HOLD"
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,
            json.JSONDecodeError,subprocess.TimeoutExpired,http.client.HTTPException) as e:
        result={
            "schema":"auto-router.deployed-pinned-image-admin-gate.v1",
            "read_only":True,"production_mutation":False,
            "gate":"HOLD","production_ready":False,
            "error_type":type(e).__name__,
        }
    print(json.dumps(result,sort_keys=True))
    return 0 if result["gate"]=="PASS_PRE_CUTOVER_INSPECTION_ONLY" else 2


if __name__=="__main__":
    sys.exit(main())
