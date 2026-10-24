"""Synthetic ZIP/OCI tampering tests; no Docker, network, extraction or writes outside tmp."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import zipfile

import pytest

from scripts import verify_router_handoff_offline as v

SOURCE = "a" * 40

def sha(data):
    return hashlib.sha256(data).hexdigest()

def make(tmp_path, *, env=None, flags=None, config_override=None):
    config = {"architecture": "amd64", "os": "linux",
              "config": {"Env": list(env or ["PYTHONUNBUFFERED=1"])}}
    if config_override:
        config.update(config_override)
    conf_bytes = json.dumps(config, sort_keys=True).encode()
    image = "sha256:" + sha(conf_bytes)
    config_file = "blobs/sha256/" + image.split(":")[1]
    index = json.dumps([{"Config": config_file, "RepoTags": None, "Layers": []}]).encode()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for name, body in [(config_file, conf_bytes), ("manifest.json", index)]:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            t.addfile(info, io.BytesIO(body))
    compressed = gzip.compress(raw.getvalue())
    receipt = {
        "schema": 1, "status": "UNPROMOTED_DISPOSABLE_CI_IMAGE_PACKAGE",
        "image_id": image, "source_commit": SOURCE,
        "archive_name": "tested-router-image.tar.gz",
        "archive_sha256": sha(compressed),
        "archive_bytes": len(compressed),
        "tested_admin_routes": 20,
        "tested_admin_get_operations": 16,
        "tested_admin_post_operations": 4,
        "authenticated_admin_post_attempts": 0,
        "all_admin_surface_verdict": "DISPOSABLE_ALL_ADMIN_AUTH_NEGATIVES_PASS",
        "public_client_reads_preserved": True,
        "sanitized_provider_response": True,
        "canary_result": "DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS",
        "production_deployed": False, "production_rollback_verified": False,
        "external_trace_custody_verified": False,
    }
    receipt.update(flags or {})
    path = tmp_path/"handoff.zip"
    with zipfile.ZipFile(path,"w") as z:
        z.writestr("image-manifest.json",json.dumps(receipt))
        z.writestr("tested-router-image.tar.gz",compressed)
    return path,image,receipt

def test_exact_synthetic_archive_verified(tmp_path):
    path,image,receipt=make(tmp_path)
    result=v.verify(path,image,SOURCE,receipt["archive_sha256"])
    assert result["status"]=="OFFLINE_UNPROMOTED_IMAGE_VERIFIED"
    assert result["production_cutover_allowed"] is False
    assert result["docker_load_executed"] is False
    assert result["oci_blobs_verified"]==1

@pytest.mark.parametrize("claim",[
    {"production_deployed":True},
    {"production_rollback_verified":True},
    {"external_trace_custody_verified":True},
    {"tested_admin_routes":9},
    {"tested_admin_get_operations":15},
    {"tested_admin_post_operations":3},
    {"authenticated_admin_post_attempts":1},
    {"authenticated_admin_post_attempts":False},
    {"tested_admin_post_operations":True},
    {"all_admin_surface_verdict":"UNVERIFIED"},
    {"sanitized_provider_response":False},
    {"public_client_reads_preserved":False},
    {"canary_result":"UNVERIFIED"},
])
def test_inflated_or_failing_provenance_denied(tmp_path,claim):
    path,image,receipt=make(tmp_path,flags=claim)
    with pytest.raises(v.VerificationDenied):
        v.verify(path,image,SOURCE,receipt["archive_sha256"])

def test_wrong_image_identity_denied(tmp_path):
    path,image,receipt=make(tmp_path)
    with pytest.raises(v.VerificationDenied):
        v.verify(path,"sha256:"+"1"*64,SOURCE,receipt["archive_sha256"])

def test_wrong_source_commit_denied(tmp_path):
    path,image,receipt=make(tmp_path)
    with pytest.raises(v.VerificationDenied):
        v.verify(path,image,"b"*40,receipt["archive_sha256"])

def test_wrong_archive_digest_denied(tmp_path):
    path,image,receipt=make(tmp_path)
    with pytest.raises(v.VerificationDenied):
        v.verify(path,image,SOURCE,"2"*64)

def test_embedded_admin_token_denied(tmp_path):
    path,image,receipt=make(tmp_path,env=["AUTO_ROUTER_ADMIN_TOKEN=synthetic-unsafe"])
    with pytest.raises(v.VerificationDenied,match="embedded"):
        v.verify(path,image,SOURCE,receipt["archive_sha256"])

def test_wrong_platform_denied(tmp_path):
    path,image,receipt=make(tmp_path,config_override={"architecture":"arm64"})
    with pytest.raises(v.VerificationDenied,match="architecture"):
        v.verify(path,image,SOURCE,receipt["archive_sha256"])

def test_no_alternate_paths_or_extra_zip_files(tmp_path):
    path,image,receipt=make(tmp_path)
    with zipfile.ZipFile(path,"a") as z:
        z.writestr("../evil","not extracted")
    with pytest.raises(v.VerificationDenied,match="unexpected ZIP members"):
        v.verify(path,image,SOURCE,receipt["archive_sha256"])

def test_never_calls_docker_or_extracts(tmp_path,monkeypatch):
    path,image,receipt=make(tmp_path)
    import subprocess
    monkeypatch.setattr(subprocess,"run",lambda *a,**k:(_ for _ in ()).throw(
        AssertionError("external command cannot run")))
    assert v.verify(path,image,SOURCE,receipt["archive_sha256"])["docker_load_executed"] is False
