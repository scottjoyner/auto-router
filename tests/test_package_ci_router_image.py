"""No-Docker, no-network fail-closed tests for portable CI image handoff."""
from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

import pytest

from scripts import package_ci_router_image as p

IMAGE="sha256:"+"d"*64
BRANCH=p.CI_BRANCH

def valid_env():
    return {"GITHUB_ACTIONS":"true",
            "AUTO_ROUTER_CI_IMAGE_PACKAGE":"unpromoted-disposable-only",
            "GITHUB_HEAD_REF":BRANCH,
            "GITHUB_SHA":"a"*40}

def valid_receipt():
    return {"status":"DISPOSABLE_CI_ADMIN_AUTH_EVIDENCE",
            "source_commit":"a"*40,
            "ci_container_image_id":IMAGE,
            "canary_result":"DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS",
            "checked_admin_route_count":10,
            "all_admin_surface_verdict":"DISPOSABLE_ALL_ADMIN_AUTH_NEGATIVES_PASS",
            "all_admin_operation_count":20,
            "all_admin_get_count":16,
            "all_admin_post_count":4,
            "all_admin_missing_and_wrong_tokens_denied":True,
            "authenticated_admin_post_attempts":0,
            "legacy_client_verdict":"DISPOSABLE_LEGACY_CLIENT_READ_PARITY_PASS",
            "public_client_discovery_reads":2,
            "legacy_admin_denial_checks":8,
            "legacy_authorized_admin_reads":4,
            "legacy_alt_auth_reads":2,
            "legacy_catalog_shape_verified":True,
            "synthetic_client_only":True,
            "real_provider_requests":0,
            "missing_and_wrong_token_rejected":True,
            "public_client_reads_preserved":True,
            "sanitized_provider_response":True,
            "production_image_identity_qualified":False,
            "production_deployment_authorized":False,
            "production_cutover_performed":False,
            "rollback_proven":False,
            "independent_archival_custody_proven":False}

def test_good_disposable_ci_identity_only():
    p.check_identity(valid_env(),valid_receipt(),IMAGE)

@pytest.mark.parametrize("field,value",[
    ("GITHUB_ACTIONS","false"),
    ("GITHUB_HEAD_REF","main"),
    ("GITHUB_SHA","not-a-commit"),
    ("AUTO_ROUTER_CI_IMAGE_PACKAGE","approved-for-prod"),
])
def test_wrong_ci_environment_denied(field,value):
    env=valid_env()
    env[field]=value
    with pytest.raises(p.PackageDenied):
        p.check_identity(env,valid_receipt(),IMAGE)

@pytest.mark.parametrize("field,value",[
    ("status","UNVERIFIED"),
    ("source_commit","b"*40),
    ("ci_container_image_id","sha256:"+"e"*64),
    ("canary_result","FAILED"),
    ("checked_admin_route_count",9),
    ("all_admin_operation_count",10),
    ("all_admin_get_count",15),
    ("all_admin_post_count",3),
    ("authenticated_admin_post_attempts",1),
    ("all_admin_missing_and_wrong_tokens_denied",False),
    ("all_admin_surface_verdict","NOT_RUN"),
    ("legacy_client_verdict","NOT_RUN"),
    ("public_client_discovery_reads",1),
    ("legacy_admin_denial_checks",7),
    ("legacy_authorized_admin_reads",3),
    ("legacy_alt_auth_reads",1),
    ("legacy_catalog_shape_verified",False),
    ("synthetic_client_only",False),
    ("real_provider_requests",1),
    ("real_provider_requests",False),
    ("public_client_discovery_reads",True),
    ("missing_and_wrong_token_rejected",False),
    ("public_client_reads_preserved",False),
    ("sanitized_provider_response",False),
    ("production_deployment_authorized",True),
    ("rollback_proven",True),
    ("independent_archival_custody_proven",True),
])
def test_tampered_or_overstated_canary_denied(field,value):
    receipt=valid_receipt()
    receipt[field]=value
    with pytest.raises(p.PackageDenied):
        p.check_identity(valid_env(),receipt,IMAGE)

def test_other_image_digest_denied():
    with pytest.raises(p.PackageDenied):
        p.check_identity(valid_env(),valid_receipt(),"sha256:"+"e"*64)

def test_package_uses_save_not_load_and_preserves_gzip(monkeypatch,tmp_path):
    payload=b"IMAGE_TEST_DATA_NO_SECRETS"*64
    calls=[]
    class Dummy:
        def __init__(self,argv,stdout,stderr):
            calls.append(argv)
            self.stdout=io.BytesIO(payload)
            self.stderr=io.BytesIO(b"")
        def wait(self,timeout):return 0
        def poll(self):return 0
    monkeypatch.setattr(p.subprocess,"Popen",Dummy)
    target=tmp_path/"sample.tar.gz"
    p.encode_exact_image(IMAGE,target)
    assert calls==[["docker","save",IMAGE]]
    assert target.stat().st_mode&0o777==0o600
    assert gzip.decompress(target.read_bytes())==payload
    assert len(p.digest(target))==64
    with pytest.raises(p.PackageDenied):
        p.encode_exact_image(IMAGE,target)

def test_failed_docker_save_does_not_leave_partial_archive(monkeypatch,tmp_path):
    class Broken:
        def __init__(self,*_args,**_kw):
            self.stdout=io.BytesIO(b"partial")
            self.stderr=io.BytesIO(b"image not present")
        def wait(self,timeout):return 1
        def poll(self):return 1
    monkeypatch.setattr(p.subprocess,"Popen",Broken)
    target=tmp_path/"bad.tar.gz"
    with pytest.raises(p.PackageDenied,match="docker save failed"):
        p.encode_exact_image(IMAGE,target)
    assert not target.exists()
