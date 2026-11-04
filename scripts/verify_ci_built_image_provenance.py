#!/usr/bin/env python3
"""Read-only proof: newly built CI Docker image contains the checked-out router code.

Exact synthetic GitHub CI container only. The digest observed from the CI build
is NOT an external approval or a signed supply-chain attestation, and never
authorizes production deployment. No auth tokens, credential reads or mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from scripts.admin_auth_readonly_canary import ADMIN_PATHS, probe
from scripts.verify_deployed_admin_release_identity import (
    FILES, _identity_lease, _run, evaluate, inspect,
)

SCHEMA="auto-router.ci-built-image-provenance.v1"
CONTAINER="auto-router-smoke"
MOUNT_DESTINATIONS=frozenset(("/app/config","/data"))
ROOT=Path(__file__).resolve().parents[1]


def source_hashes(root: Path) -> dict:
    code=root/"src"/"auto_router"
    result={}
    for name in FILES:
        target=code/name
        info=target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError("reviewed checkout code is not a single regular file")
        result[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    return result


def mounts_safe(read=_run) -> bool:
    data=json.loads(read([
        "docker","inspect","--format","{{json .Mounts}}",CONTAINER]))
    if not isinstance(data,list):
        return False
    destinations=[]
    for item in data:
        if not isinstance(item,dict):
            return False
        destination=item.get("Destination")
        if (destination not in MOUNT_DESTINATIONS or
                item.get("Type") not in ("bind","volume")):
            return False
        destinations.append(destination)
    return set(destinations)==set(MOUNT_DESTINATIONS) and len(destinations)==2


def evaluate_ci(*, root: Path=ROOT, docker_read=_run,
                status_probe=probe) -> dict:
    actual=inspect(read=docker_read,container=CONTAINER)
    before=actual["identity_lease"]
    source=source_hashes(root)
    build=evaluate(
        actual,actual["image_id"][7:],source,
        status_probe=status_probe,allow_public_bind=False,
    )
    checks=build["checks"]
    checks["no_source_code_bind_mount"]=mounts_safe(docker_read)
    checks["container_unchanged_after_probes"]=(
        _identity_lease(docker_read,CONTAINER)==before
    )
    revision=subprocess.run(
        ["git","-C",str(root),"rev-parse","HEAD"],
        capture_output=True,text=True,check=False,timeout=4
    )
    sha=revision.stdout.strip()
    checks["reviewed_checkout_commit_pinned"]=(
        revision.returncode==0 and len(sha)==40 and
        all(ch in "0123456789abcdef" for ch in sha))
    paths=[f"src/auto_router/{name}" for name in FILES]
    tracked=subprocess.run(
        ["git","-C",str(root),"ls-files","--error-unmatch","--",*paths],
        capture_output=True,text=True,check=False,timeout=4,
    )
    clean=subprocess.run(
        ["git","-C",str(root),"status","--porcelain","--",*paths],
        capture_output=True,text=True,check=False,timeout=4,
    )
    checks["all_four_reviewed_source_files_tracked"]=(
        tracked.returncode==0 and
        set(tracked.stdout.splitlines())==set(paths)
    )
    checks["reviewed_source_files_unmodified"]=(
        clean.returncode==0 and clean.stdout==""
    )
    return {
        "schema":SCHEMA,
        "research_only":True,
        "production_ready":False,
        "independent_signature_or_attestation":False,
        "read_only":True,
        "secrets_accessed":False,
        "container":CONTAINER,
        "checkout_git_sha":sha if checks["reviewed_checkout_commit_pinned"] else None,
        "built_image_sha256":actual["image_id"],
        "reviewed_source_sha256":source,
        "status_only_admin_paths":list(ADMIN_PATHS),
        "checks":checks,
        "gate":"PASS_CI_BUILD_ONLY" if all(checks.values()) else "HOLD",
    }


def write_receipt(path: Path, receipt: dict) -> None:
    if (path.is_symlink() or path.exists() or
        not path.name.startswith("router-ci-") or path.suffix!=".json" or
        path.parent.name!="ci-artifacts"):
        raise ValueError("only new ci-artifacts/router-ci-*.json receipt permitted")
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|
               getattr(os,"O_NOFOLLOW",0),0o600)
    with os.fdopen(fd,"wb") as f:
        f.write((json.dumps(receipt,sort_keys=True)+"\n").encode())
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-ci-smoke-only",action="store_true")
    parser.add_argument("--out",required=True,type=Path)
    args=parser.parse_args()
    if not args.explicit_ci_smoke_only:
        parser.error("must explicitly opt in to research CI smoke only")
    try:
        receipt=evaluate_ci()
        write_receipt(args.out,receipt)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(json.dumps({
            "schema":SCHEMA,"gate":"HOLD","production_ready":False,
            "error_type":type(exc).__name__,
        },sort_keys=True))
        return 2
    # Print only a bounded nonsecret summary. The receipt is separately
    # uploaded as a CI artifact for an operator to verify and compare.
    print(json.dumps({
        "schema":SCHEMA,
        "gate":receipt["gate"],
        "checkout_git_sha":receipt["checkout_git_sha"],
        "built_image_sha256":receipt["built_image_sha256"],
        "checks":receipt["checks"],
        "production_ready":False,
    },sort_keys=True))
    return 0 if receipt["gate"]=="PASS_CI_BUILD_ONLY" else 2


if __name__=="__main__":
    sys.exit(main())
