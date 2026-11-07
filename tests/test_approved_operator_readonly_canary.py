"""All mocks; no production credentials, Docker lifecycle, or network calls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.approved_operator_readonly_canary import (
    SAFE_PATHS,operator_status,read_private_pipe,verify,
)

LEASE="sha256:"+"a"*64+"|2026-11-04T16:01Z|true"
PINS={x:format(i+1,"064x") for i,x in enumerate((
    "main.py","security.py","main_live.py","secure_live.py",
))}
TOKEN="operator-fixture-unit-12345678"


def _inspect():
    return {"identity_lease":LEASE}


def _pass(*args):
    return {"gate":"PASS_PRE_CUTOVER_INSPECTION_ONLY"}


def test_no_credential_read_until_provenance_passes():
    def no_secret(fd):
        raise AssertionError("credential must not be read on a failed gate")
    denied=verify(image_sha256="a"*64,source_hashes=PINS,credential_fd=3,
        inspect_fn=_inspect,gate_fn=lambda *args:{"gate":"HOLD"},
        read_credential=no_secret,lease_fn=lambda:LEASE)
    assert denied["gate"]=="HOLD"
    assert denied["production_ready"] is False


def test_changed_container_denies_before_credential():
    def no_secret(fd):
        raise AssertionError("credential must not be read after a restart")
    denied=verify(image_sha256="a"*64,source_hashes=PINS,credential_fd=3,
        inspect_fn=_inspect,gate_fn=_pass,
        lease_fn=lambda:LEASE+"-restarted",read_credential=no_secret)
    assert denied["gate"]=="HOLD"
    assert denied["reason"]=="container_changed_before_authorized_probe"


def test_authorized_probe_is_sequential_status_only_and_not_prod_approved():
    observed=[]
    def probe(path,credential):
        assert credential==TOKEN
        observed.append(path)
        return 200
    granted=verify(image_sha256="a"*64,source_hashes=PINS,credential_fd=3,
        inspect_fn=_inspect,gate_fn=_pass,lease_fn=lambda:LEASE,
        read_credential=lambda _:TOKEN,status_fn=probe)
    assert granted["gate"]=="PASS_READ_ONLY_OPERATOR_PROBE_ONLY"
    assert observed==list(SAFE_PATHS)
    assert granted["production_ready"] is False
    assert granted["independently_approved_rollback_verified"] is False
    assert TOKEN not in json.dumps(granted)


def test_denied_operator_endpoint_holds():
    received=verify(image_sha256="a"*64,source_hashes=PINS,credential_fd=3,
        inspect_fn=_inspect,gate_fn=_pass,lease_fn=lambda:LEASE,
        read_credential=lambda _:TOKEN,
        status_fn=lambda p,t:401 if p=="/admin/context" else 200)
    assert received["gate"]=="HOLD"
    assert sum(not x["accepted"] for x in received["checked_paths"])==1


def test_changed_container_after_operator_probe_holds():
    leases=iter([LEASE,LEASE+"-restarted"])
    checked=verify(image_sha256="a"*64,source_hashes=PINS,credential_fd=3,
        inspect_fn=_inspect,gate_fn=_pass,lease_fn=lambda:next(leases),
        read_credential=lambda _:TOKEN,status_fn=lambda p,t:200)
    assert checked["gate"]=="HOLD"
    assert checked["same_container_before_and_after"] is False


def test_actual_status_request_never_reads_body_and_only_get():
    calls=[]
    class Conn:
        def __init__(self,host,port,timeout):
            assert (host,port,timeout)==("127.0.0.1",8088,2)
        def request(self,method,path,headers):
            assert method=="GET"
            assert headers["X-Admin-Token"]==TOKEN
            assert path in SAFE_PATHS
            calls.append(path)
        def getresponse(self):
            class Resp:
                status=200
                def read(self,*args):
                    raise AssertionError("must never read response body")
            return Resp()
        def close(self):pass
    assert operator_status("/admin/context",TOKEN,factory=Conn)==200
    assert calls==["/admin/context"]


def test_unsafe_health_and_mutations_denied_before_network():
    for path in ("/admin/providers/health","/admin/circuits/reset","/v1/models"):
        with pytest.raises(ValueError):
            operator_status(path,TOKEN,factory=lambda *a,**k:None)


def test_pipe_reader_accepts_only_bounded_private_pipe():
    rd,wr=os.pipe()
    try:
        os.write(wr,TOKEN.encode("ascii"))
        assert read_private_pipe(rd)==TOKEN
    finally:
        os.close(rd)
        os.close(wr)


def test_regular_file_fd_rejected(tmp_path):
    p=tmp_path/"token"
    p.write_text(TOKEN)
    with p.open("rb") as f:
        with pytest.raises(ValueError,match="pipe"):
            read_private_pipe(f.fileno())


@pytest.mark.parametrize("candidate",[
    b"short",b"a"*513,b"contains space here",b"contains\nnewline",
    b"\x00"*22,b"\xff"*22,
])
def test_malformed_pipe_credentials_rejected(candidate):
    rd,wr=os.pipe()
    try:
        os.write(wr,candidate)
        with pytest.raises(ValueError):
            read_private_pipe(rd)
    finally:
        os.close(rd)
        os.close(wr)


@pytest.mark.parametrize("fd",[0,1,2,-1,33,False,"3"])
def test_unknown_or_standard_fds_refused(fd):
    with pytest.raises(ValueError):
        read_private_pipe(fd)


def test_cli_missing_explicit_approval_stops_before_any_docker_action():
    root=Path(__file__).resolve().parents[1]
    p=subprocess.run([sys.executable,"-B","-m","scripts.approved_operator_readonly_canary",
                      "--expected-image-sha256","a"*64,"--credential-fd","3",
                      *sum(([f"--expected-{name}-sha256",digest]
                            for name,digest in zip(
                                ("main","security","main-live","secure-live"),PINS.values())),[])],
                     cwd=root,capture_output=True,text=True,check=False,timeout=4)
    assert p.returncode!=0
    assert "explicit-approved-read-only-operator" in p.stderr
    assert TOKEN not in p.stdout+p.stderr
