#!/usr/bin/env python3
"""Package the exact disposable CI-canary image, never any production state.

The published archive is an unpromoted image supply artifact only. It does not
contain runtime mounts or CI environment variables. No registry pushes, host
deployments or Docker image removal are performed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

EVIDENCE = Path("/tmp/auto-router-admin-ci-evidence.json")
OUT_DIR = Path("/tmp/router-ci-image-handoff")
MAX_IMAGE_ARCHIVE = 700 * 1024 * 1024
CI_BRANCH = "security/inherited-admin-exact-head-regression-20261023"

class PackageDenied(ValueError):
    pass

def check_identity(env: dict[str, str], receipt: dict, image: str) -> None:
    if (env.get("GITHUB_ACTIONS") != "true"
        or env.get("AUTO_ROUTER_CI_IMAGE_PACKAGE") != "unpromoted-disposable-only"
        or env.get("GITHUB_HEAD_REF") != CI_BRANCH):
        raise PackageDenied("CI packaging identity not permitted")
    sha = env.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise PackageDenied("missing source SHA")
    if receipt.get("status") != "DISPOSABLE_CI_ADMIN_AUTH_EVIDENCE":
        raise PackageDenied("missing successful auth evidence")
    if receipt.get("source_commit") != sha:
        raise PackageDenied("source/image evidence mismatch")
    if (receipt.get("canary_result") != "DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS"
        or receipt.get("checked_admin_route_count") != 10
        or receipt.get("all_admin_surface_verdict") != "DISPOSABLE_ALL_ADMIN_AUTH_NEGATIVES_PASS"
        or type(receipt.get("all_admin_operation_count")) is not int
        or receipt["all_admin_operation_count"] != 20
        or type(receipt.get("all_admin_get_count")) is not int
        or receipt["all_admin_get_count"] != 16
        or type(receipt.get("all_admin_post_count")) is not int
        or receipt["all_admin_post_count"] != 4
        or receipt.get("all_admin_missing_and_wrong_tokens_denied") is not True
        or type(receipt.get("authenticated_admin_post_attempts")) is not int
        or receipt["authenticated_admin_post_attempts"] != 0
        or receipt.get("missing_and_wrong_token_rejected") is not True
        or receipt.get("public_client_reads_preserved") is not True
        or receipt.get("sanitized_provider_response") is not True):
        raise PackageDenied("auth/client canary not certified")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise PackageDenied("invalid tested image identity")
    if receipt.get("ci_container_image_id") != image:
        raise PackageDenied("not the image actually tested by CI")
    if any(receipt.get(k) is not False for k in (
        "production_image_identity_qualified",
        "production_deployment_authorized",
        "production_cutover_performed",
        "rollback_proven",
        "independent_archival_custody_proven",
    )):
        raise PackageDenied("untrusted promotion/custody claim")

def image_identity() -> str:
    result=subprocess.run(
        ["docker","inspect","auto-router-smoke","--format","{{.Image}}"],
        capture_output=True,text=True,check=True,timeout=10)
    return result.stdout.strip()

def encode_exact_image(image: str, output: Path):
    if output.exists() or output.is_symlink():
        raise PackageDenied("archive target already exists")
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    proc=None
    try:
        with os.fdopen(fd,"wb") as target:
            proc=subprocess.Popen(["docker","save",image],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            with gzip.GzipFile(filename="",fileobj=target,mode="wb",
                               compresslevel=1,mtime=0) as compressed:
                total=0
                while True:
                    chunk=proc.stdout.read(1024*1024)
                    if not chunk:
                        break
                    total+=len(chunk)
                    if total>3*1024*1024*1024:
                        raise PackageDenied("uncompressed image too large")
                    compressed.write(chunk)
            proc.stdout.close()
            error=proc.stderr.read(4096)
            proc.stderr.close()
            if proc.wait(timeout=50)!=0:
                raise PackageDenied("docker save failed")
            target.flush()
            os.fsync(target.fileno())
        if output.stat().st_size>MAX_IMAGE_ARCHIVE:
            raise PackageDenied("compressed image exceeds bounded artifact budget")
        # Integrity test of the newly produced gzip stream: no Docker load.
        with gzip.open(output,"rb") as src:
            while src.read(1024*1024):
                pass
    except Exception:
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait()
        output.unlink(missing_ok=True)
        raise

def digest(p: Path):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    env=dict(os.environ)
    if not EVIDENCE.is_file() or EVIDENCE.is_symlink():
        raise PackageDenied("missing trusted local CI evidence")
    receipt=json.loads(EVIDENCE.read_text())
    image=image_identity()
    check_identity(env,receipt,image)
    if OUT_DIR.is_symlink():
        raise PackageDenied("untrusted output directory")
    OUT_DIR.mkdir(mode=0o700,exist_ok=True)
    os.chmod(OUT_DIR,0o700)
    archive=OUT_DIR/"tested-router-image.tar.gz"
    encode_exact_image(image,archive)
    record={
        "schema":1,
        "status":"UNPROMOTED_DISPOSABLE_CI_IMAGE_PACKAGE",
        "image_id":image,
        "source_commit":receipt["source_commit"],
        "source_tree":receipt["git_source_tree"],
        "canary_result":receipt["canary_result"],
        "tested_admin_routes":receipt["all_admin_operation_count"],
        "tested_admin_get_operations":receipt["all_admin_get_count"],
        "tested_admin_post_operations":receipt["all_admin_post_count"],
        "authenticated_admin_post_attempts":0,
        "all_admin_surface_verdict":receipt["all_admin_surface_verdict"],
        "public_client_reads_preserved":True,
        "sanitized_provider_response":True,
        "archive_name":archive.name,
        "archive_sha256":digest(archive),
        "archive_bytes":archive.stat().st_size,
        "produced_utc":datetime.now(timezone.utc).isoformat(),
        "production_deployed":False,
        "production_rollback_verified":False,
        "external_trace_custody_verified":False,
    }
    manifest=OUT_DIR/"image-manifest.json"
    fd=os.open(manifest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w") as file:
        json.dump(record,file,sort_keys=True,indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    print(json.dumps({
        "status":record["status"],
        "image_id":image,
        "source_commit":record["source_commit"],
        "image_archive_sha256":record["archive_sha256"],
        "image_bytes":record["archive_bytes"],
        "not_production":True,
    },sort_keys=True))

if __name__=="__main__":
    main()
