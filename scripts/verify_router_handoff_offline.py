#!/usr/bin/env python3
"""Read-only verification of the unpromoted CI-tested Router image handoff ZIP.

NEVER imports, starts or deploys an image; never extracts archive contents.
All public approval and rollback flags must remain false. Caller must supply
independently trusted expected image, source, and compressed-archive digests.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import tarfile
import zipfile

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_RE = re.compile(r"^[0-9a-f]{40}$")
ZIP_FILES = {"image-manifest.json", "tested-router-image.tar.gz"}
MAX_ZIP_BYTES = 250 * 1024 * 1024
MAX_TAR_MEMBER = 800 * 1024 * 1024
MAX_TAR_TOTAL = 3 * 1024 * 1024 * 1024
MAX_TAR_MEMBERS = 150
NO_PROMOTION = ("production_deployed", "production_rollback_verified",
                "external_trace_custody_verified")

class VerificationDenied(ValueError):
    pass

def digest_stream(stream, max_size: int) -> tuple[str, int]:
    result = hashlib.sha256()
    n = 0
    while True:
        data = stream.read(1024 * 1024)
        if not data:
            break
        n += len(data)
        if n > max_size:
            raise VerificationDenied("size limit exceeded")
        result.update(data)
    return result.hexdigest(), n

def verify(path: Path, expected_image: str, expected_source: str,
           expected_archive_sha: str) -> dict:
    if (not FULL_SHA_RE.fullmatch(expected_image) or
        not GIT_RE.fullmatch(expected_source) or
        not SHA_RE.fullmatch(expected_archive_sha)):
        raise VerificationDenied("independent exact pins are required")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ZIP_BYTES:
        raise VerificationDenied("untrusted artifact ZIP")
    with zipfile.ZipFile(path) as z:
        files = z.infolist()
        if len(files) != 2 or set(x.filename for x in files) != ZIP_FILES:
            raise VerificationDenied("unexpected ZIP members")
        for x in files:
            mode = x.external_attr >> 16
            if stat.S_ISLNK(mode) or x.file_size > MAX_ZIP_BYTES:
                raise VerificationDenied("unsafe ZIP entry type/size")
        with z.open("image-manifest.json") as fp:
            manifest_bytes = fp.read(16_385)
        if len(manifest_bytes) > 16_384:
            raise VerificationDenied("manifest too large")
        receipt = json.loads(manifest_bytes)
        if (receipt.get("schema") != 1 or
            receipt.get("status") != "UNPROMOTED_DISPOSABLE_CI_IMAGE_PACKAGE" or
            receipt.get("image_id") != expected_image or
            receipt.get("source_commit") != expected_source or
            receipt.get("archive_name") != "tested-router-image.tar.gz" or
            receipt.get("archive_sha256") != expected_archive_sha or
            receipt.get("tested_admin_routes") != 10 or
            receipt.get("public_client_reads_preserved") is not True or
            receipt.get("sanitized_provider_response") is not True or
            receipt.get("canary_result") != "DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS" or
            any(receipt.get(name) is not False for name in NO_PROMOTION)):
            raise VerificationDenied("invalid provenance, canary, or production claim")
        if receipt.get("archive_bytes") != z.getinfo("tested-router-image.tar.gz").file_size:
            raise VerificationDenied("compressed image size mismatch")
        with z.open("tested-router-image.tar.gz") as fp:
            archive_sha, n = digest_stream(fp, MAX_ZIP_BYTES)
        if archive_sha != expected_archive_sha or n != receipt["archive_bytes"]:
            raise VerificationDenied("compressed image hash mismatch")
        total = 0
        blob_count = 0
        config = None
        index = None
        seen = set()
        config_file = "blobs/sha256/" + expected_image.split(":")[1]
        with z.open("tested-router-image.tar.gz") as packed:
            with gzip.GzipFile(fileobj=packed, mode="rb") as decompressed:
                with tarfile.open(fileobj=decompressed, mode="r|") as t:
                    for member in t:
                        if len(seen) >= MAX_TAR_MEMBERS or member.name in seen:
                            raise VerificationDenied("tar duplicate/excess entries")
                        seen.add(member.name)
                        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                            raise VerificationDenied("unsafe tar member type")
                        name = member.name.rstrip("/")
                        if (name.startswith("/") or ".." in name.split("/") or
                            "\\" in name):
                            raise VerificationDenied("unsafe tar member path")
                        if member.isdir():
                            continue
                        if member.size > MAX_TAR_MEMBER or total + member.size > MAX_TAR_TOTAL:
                            raise VerificationDenied("oversized tar image")
                        total += member.size
                        fp = t.extractfile(member)
                        if fp is None:
                            raise VerificationDenied("missing tar file bytes")
                        if name.startswith("blobs/sha256/"):
                            expected_blob = name.split("/")[-1]
                            if not SHA_RE.fullmatch(expected_blob):
                                raise VerificationDenied("invalid OCI blob name")
                            if name == config_file:
                                if member.size > 100_000:
                                    raise VerificationDenied("oversized Docker config")
                                data = fp.read()
                                actual_sha = hashlib.sha256(data).hexdigest()
                                config = json.loads(data)
                            else:
                                actual_sha, _ = digest_stream(fp, MAX_TAR_MEMBER)
                            if actual_sha != expected_blob:
                                raise VerificationDenied("OCI blob digest mismatch")
                            blob_count += 1
                        elif name == "manifest.json":
                            if member.size > 50_000:
                                raise VerificationDenied("oversized Docker manifest")
                            index = json.loads(fp.read())
                        elif name in ("oci-layout", "index.json", "repositories"):
                            digest_stream(fp, MAX_TAR_MEMBER)
                        else:
                            raise VerificationDenied("unexpected image file")
        if not isinstance(index, list) or len(index) != 1 or config is None:
            raise VerificationDenied("no single pinned image")
        if index[0].get("Config") != config_file:
            raise VerificationDenied("Docker manifest image identity differs")
        if config.get("architecture") != "amd64" or config.get("os") != "linux":
            raise VerificationDenied("unexpected architecture")
        envs = ((config.get("config") or {}).get("Env") or [])
        if any(str(e).split("=", 1)[0] in ("AUTO_ROUTER_ADMIN_TOKEN",
                "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN") for e in envs):
            raise VerificationDenied("privileged token embedded in image config")
        return {"status": "OFFLINE_UNPROMOTED_IMAGE_VERIFIED",
                "image_id": expected_image, "source_commit": expected_source,
                "compressed_archive_sha256": archive_sha,
                "oci_blobs_verified": blob_count,
                "platform": "linux/amd64", "production_deployed": False,
                "docker_load_executed": False, "production_cutover_allowed": False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-archive-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.zip, args.expected_image, args.expected_source,
                            args.expected_archive_sha256), sort_keys=True))

if __name__ == "__main__":
    main()
