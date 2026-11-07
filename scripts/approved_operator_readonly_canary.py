#!/usr/bin/env python3
"""Operator-approved, status-only deployed admin authentication preflight.

This tool is NOT run by CI, never retrieves production secrets, and never
starts, replaces, or stops a container. A reviewed operator must supply the
expected image/source SHA pins and an already-open credential pipe.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import select
import stat
import sys

from scripts.admin_positive_auth_ci_canary import SAFE_PATHS
from scripts.verify_deployed_admin_release_identity import (
    FILES, _identity_lease, evaluate, inspect,
)

SCHEMA="auto-router.approved-operator-readonly-canary.v1"
MAX_SECRET_BYTES=512


def read_private_pipe(fd: int) -> str:
    # Reject argv secrets, files, sockets, reused stdin, and unknown FDs.
    if type(fd) is not int or fd<3 or fd>32:
        raise ValueError("operator credential must use an explicitly assigned pipe fd")
    info=os.fstat(fd)
    if not stat.S_ISFIFO(info.st_mode):
        raise ValueError("credential source must be a pipe, not a file")
    ready,_,_=select.select([fd],[],[],3)
    if not ready:
        raise TimeoutError("credential pipe was not available")
    raw=os.read(fd,MAX_SECRET_BYTES+1)
    if not 16<=len(raw)<=MAX_SECRET_BYTES or any(ch<=32 or ch>=127 for ch in raw):
        raise ValueError("operator credential is missing or invalid")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("operator credential must be ASCII") from exc


def operator_status(path: str, credential: str,
                    *, factory=http.client.HTTPConnection) -> int | None:
    if path not in SAFE_PATHS or not credential or len(credential)>MAX_SECRET_BYTES:
        raise ValueError("only safe admin read routes and bounded credential are allowed")
    conn=factory("127.0.0.1",8088,timeout=2)
    try:
        conn.request("GET",path,headers={
            "X-Admin-Token":credential,
            "Accept":"application/json",
            "Connection":"close",
            "User-Agent":"auto-router-approved-status-only-operator",
        })
        return conn.getresponse().status  # No response body is read, ever.
    except (OSError,http.client.HTTPException):
        return None
    finally:
        conn.close()


def verify(*, image_sha256: str, source_hashes: dict[str,str], credential_fd: int,
           inspect_fn=inspect, gate_fn=evaluate, lease_fn=_identity_lease,
           read_credential=read_private_pipe, status_fn=operator_status) -> dict:
    base={
        "schema":SCHEMA,"read_only":True,"production_mutation":False,
        "response_bodies_read":False,"credential_logged":False,
        "production_ready":False,"real_operator_client_reachability_verified":False,
    }
    # Do not consume the approved credential until image, installed bytes,
    # entrypoint, port, and all six anonymous-denial probes are validated.
    actual=inspect_fn()
    first_lease=actual.get("identity_lease")
    pre=gate_fn(actual,image_sha256,source_hashes)
    if pre.get("gate")!="PASS_PRE_CUTOVER_INSPECTION_ONLY":
        return {**base,"gate":"HOLD","reason":"deployed_provenance_or_anonymous_auth_failed"}
    if not first_lease or lease_fn()!=first_lease:
        return {**base,"gate":"HOLD","reason":"container_changed_before_authorized_probe"}
    credential=read_credential(credential_fd)
    # Never format, log or return the credential; only status codes.
    rows=[]
    for path in SAFE_PATHS:
        status=status_fn(path,credential)
        rows.append({"path":path,"status":status,
                     "accepted":type(status) is int and status==200})
    after=lease_fn()
    all_good=after==first_lease and len(rows)==len(SAFE_PATHS) and all(
        item["accepted"] for item in rows
    )
    return {
        **base,"gate":"PASS_READ_ONLY_OPERATOR_PROBE_ONLY" if all_good else "HOLD",
        "checked_paths":rows,
        "same_container_before_and_after":after==first_lease,
        "independently_approved_rollback_verified":False,
        "production_ready":False,
    }


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-approved-read-only-operator",action="store_true")
    parser.add_argument("--expected-image-sha256",required=True)
    parser.add_argument("--credential-fd",type=int,required=True)
    for filename in FILES:
        parser.add_argument("--expected-"+filename.removesuffix(".py").replace("_","-")+"-sha256",
                            required=True)
    args=parser.parse_args()
    if not args.explicit_approved_read_only_operator:
        parser.error("explicit human-approved read-only operator invocation required")
    expected={name:getattr(args,"expected_"+name.removesuffix(".py")+"_sha256")
              for name in FILES}
    try:
        result=verify(image_sha256=args.expected_image_sha256,
                      source_hashes=expected,credential_fd=args.credential_fd)
    except (ValueError,TimeoutError,OSError,RuntimeError,TypeError,
            KeyError,http.client.HTTPException) as exc:
        # Do not print exception text: it can contain credential-related data.
        result={"schema":SCHEMA,"gate":"HOLD","error_type":type(exc).__name__,
                "read_only":True,"production_mutation":False,
                "credential_logged":False,"production_ready":False}
    print(json.dumps(result,sort_keys=True))
    return 0 if result["gate"]=="PASS_READ_ONLY_OPERATOR_PROBE_ONLY" else 2


if __name__=="__main__":
    sys.exit(main())
