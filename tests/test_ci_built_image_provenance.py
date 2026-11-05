"""No Docker, no HTTP network and no credentials in CI attestation unit tests."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from scripts.admin_auth_readonly_canary import ADMIN_PATHS
from scripts.verify_ci_built_image_provenance import (
    CONTAINER, evaluate_ci, mounts_safe, source_hashes, write_receipt,
)
from scripts.verify_deployed_admin_release_identity import FILES

IMAGE="sha256:"+"a"*64
LEASE=IMAGE+"|2026-11-04T16:13:00.001Z|true"


@pytest.fixture()
def checkout():
    directory=Path(tempfile.mkdtemp(prefix="auto-router-ci-unit-"))
    root=directory/"checkout"
    folder=root/"src/auto_router"
    folder.mkdir(parents=True)
    for file in FILES:
        (folder/file).write_bytes(b"reviewed exact test source "+file.encode())
    try:
        yield root
    finally:
        shutil.rmtree(directory)


def fake_docker(source,*, image=IMAGE, after=None, extra_mount=None):
    calls=[]
    count=[0]
    def read(args):
        calls.append(args)
        assert args[0]=="docker"
        if "exec" in args:
            assert args[:4]==["docker","exec",CONTAINER,"python3"]
            assert "import auto_router" not in args[-1]
            assert "os.environ" not in args[-1]
            return json.dumps(source)
        if "{{.Image}}|{{.State.StartedAt}}|{{.State.Running}}" in args:
            count[0]+=1
            return LEASE if count[0]==1 else (after or LEASE)
        if "{{.State.Running}}" in args:
            return "true"
        if "{{.Path}}" in args:
            return "uvicorn"
        if "{{.HostConfig.Privileged}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.PidMode}}" in args:
            return "false|auto-router-smoke|"
        if "{{json .Args}}" in args:
            return json.dumps(["auto_router.secure_live:app","--port","8088"])
        if "{{json .NetworkSettings.Ports}}" in args:
            return json.dumps({"8088/tcp":[
                {"HostIp":"127.0.0.1","HostPort":"8088"}]})
        if "{{json .Mounts}}" in args:
            mounts=[
                {"Destination":"/app/config","Type":"bind"},
                {"Destination":"/data","Type":"bind"},
            ]
            if extra_mount is not None:
                mounts.append(extra_mount)
            return json.dumps(mounts)
        raise AssertionError(f"unrecognized read-only command: {args}")
    return read,calls


def deny_all(port):
    assert port==8088
    return {"all_denied":True,"results":[
        {"path":path,"status":401} for path in ADMIN_PATHS
    ]}


def fake_git(args,**kwargs):
    assert args[:3]==["git","-C",args[2]]
    if args[-2:]==["rev-parse","HEAD"]:
        return subprocess.CompletedProcess(args,0,"b"*40+"\n","")
    if args[3:6]==["ls-files","--cached","--"]:
        assert args[6:]==["src/auto_router"]
        return subprocess.CompletedProcess(args,0,
            "".join(f"src/auto_router/{name}\n" for name in FILES),"")
    if args[3:6]==["status","--porcelain","--untracked-files=all"]:
        assert args[6:]==["--","src/auto_router"]
        return subprocess.CompletedProcess(args,0,"","")
    raise AssertionError(f"unapproved Git subcommand in proof: {args}")


def test_exact_checked_out_code_matches_built_image_then_produces_only_ci_gate(checkout):
    read,calls=fake_docker(source_hashes(checkout))
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=fake_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=deny_all)
    assert receipt["gate"]=="PASS_CI_BUILD_ONLY"
    assert receipt["production_ready"] is False
    assert receipt["independent_signature_or_attestation"] is False
    assert receipt["read_only"] is True
    assert receipt["checkout_git_sha"]=="b"*40
    assert receipt["built_image_sha256"]==IMAGE
    assert all(receipt["checks"].values())
    assert all(args[1] in ("inspect","exec") for args in calls)


def test_supporting_module_mismatch_denies_even_if_main_files_match(checkout):
    prior=source_hashes(checkout)
    (checkout/"src/auto_router/supporting.py").write_bytes(
        b"new unreviewed helper behavior"
    )
    changed=source_hashes(checkout)
    from scripts.verify_deployed_admin_release_identity import TREE_KEY
    assert prior[TREE_KEY]!=changed[TREE_KEY]
    assert all(prior[name]==changed[name] for name in FILES)
    read,_=fake_docker(prior)
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=fake_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=deny_all)
    assert receipt["gate"]=="HOLD"
    assert receipt["checks"]["installed_source_hashes_match_pinned_build"] is False


def test_python_tree_rejects_untracked_symlink_and_multilink(checkout):
    from scripts.verify_deployed_admin_release_identity import python_tree_hash
    root=checkout/"src/auto_router"
    (root/"external.py").symlink_to("main.py")
    with pytest.raises(ValueError,match="unverifiable"):
        python_tree_hash(root)
    (root/"external.py").unlink()
    (root/"shadow.py").hardlink_to(root/"main.py")
    with pytest.raises(ValueError,match="unverifiable"):
        python_tree_hash(root)


def test_untracked_support_module_cannot_pass_even_if_image_matches(checkout):
    (checkout/"src/auto_router/extra.py").write_bytes(b"unreviewed plugin")
    read,_=fake_docker(source_hashes(checkout))
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=fake_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=deny_all)
    assert receipt["gate"]=="HOLD"
    assert receipt["checks"]["installed_source_hashes_match_pinned_build"] is True
    assert receipt["checks"]["all_python_source_files_git_tracked"] is False


def test_package_wide_git_status_flags_changed_support_module(checkout):
    (checkout/"src/auto_router/extra.py").write_bytes(b"reviewed extension")
    def staged_git(args,**kwargs):
        if args[3:6]==["ls-files","--cached","--"]:
            return subprocess.CompletedProcess(args,0,
                "".join(f"src/auto_router/{name}\n" for name in FILES)
                +"src/auto_router/extra.py\n","")
        if args[3:6]==["status","--porcelain","--untracked-files=all"]:
            return subprocess.CompletedProcess(args,0,
                " M src/auto_router/extra.py\n","")
        return fake_git(args,**kwargs)
    read,_=fake_docker(source_hashes(checkout))
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=staged_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=deny_all)
    assert receipt["checks"]["all_python_source_files_git_tracked"] is True
    assert receipt["checks"]["reviewed_source_files_unmodified"] is False
    assert receipt["gate"]=="HOLD"


def test_mismatched_installed_source_refuses_ci_gate(checkout):
    hashes=source_hashes(checkout)
    hashes["security.py"]="0"*64
    read,_=fake_docker(hashes)
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=fake_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=deny_all)
    assert receipt["gate"]=="HOLD"
    assert receipt["checks"]["installed_source_hashes_match_pinned_build"] is False


def test_unexpected_code_bind_mount_denied(checkout):
    read,_=fake_docker(source_hashes(checkout),
        extra_mount={"Destination":"/usr/local/lib/python3.12/site-packages",
                     "Type":"bind"})
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=fake_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=deny_all)
    assert receipt["gate"]=="HOLD"
    assert receipt["checks"]["no_source_code_bind_mount"] is False


def test_restart_between_attestation_and_get_denied(checkout):
    read,_=fake_docker(source_hashes(checkout),
        after=IMAGE+"|2026-11-04T16:15:00.001Z|true")
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=fake_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=deny_all)
    assert receipt["gate"]=="HOLD"
    assert receipt["checks"]["container_unchanged_after_probes"] is False


def test_modified_reviewed_checkout_source_denies_even_if_built_image_matches(checkout):
    read,_=fake_docker(source_hashes(checkout))
    def dirty_git(args,**kwargs):
        result=fake_git(args,**kwargs)
        if args[3:6]==["status","--porcelain","--untracked-files=all"]:
            return subprocess.CompletedProcess(args,0," M src/auto_router/security.py\n","")
        return result
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=dirty_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=deny_all)
    assert receipt["gate"]=="HOLD"
    assert receipt["checks"]["reviewed_source_files_unmodified"] is False


def test_anonymous_200_denies_even_with_exact_built_code(checkout):
    read,_=fake_docker(source_hashes(checkout))
    def leaked(port):
        rows=deny_all(port)
        rows["all_denied"]=False
        rows["results"][1]["status"]=200
        return rows
    with patch("scripts.verify_ci_built_image_provenance.subprocess.run",
               side_effect=fake_git):
        receipt=evaluate_ci(root=checkout,docker_read=read,status_probe=leaked)
    assert receipt["gate"]=="HOLD"
    assert receipt["checks"]["anonymous_admin_gets_all_401"] is False


def test_checkout_rejects_symlinked_installed_source(checkout):
    f=checkout/"src/auto_router/security.py"
    f.unlink()
    f.symlink_to("main.py")
    with pytest.raises(ValueError):
        source_hashes(checkout)


def test_writes_only_new_private_ci_artifact(checkout):
    path=checkout/"ci-artifacts/router-ci-synthetic.json"
    write_receipt(path,{
        "schema":"auto-router.ci-built-image-provenance.v1",
        "production_ready":False,"gate":"HOLD",
    })
    assert path.is_file()
    assert path.stat().st_mode&0o777==0o600
    with pytest.raises(ValueError):
        write_receipt(path,{"gate":"PASS_CI_BUILD_ONLY"})


def test_rejects_symlink_or_non_ci_artifact_path(checkout):
    path=checkout/"ci-artifacts/router-ci-evil.json"
    path.parent.mkdir()
    path.symlink_to(checkout/"src/auto_router/main.py")
    with pytest.raises(ValueError):
        write_receipt(path,{"gate":"PASS_CI_BUILD_ONLY"})
    with pytest.raises(ValueError):
        write_receipt(checkout/"arbitrary.json",{"gate":"PASS_CI_BUILD_ONLY"})


def test_mount_validator_rejects_missing_mount():
    read=lambda args:json.dumps([{"Destination":"/app/config","Type":"bind"}])
    assert mounts_safe(read) is False
