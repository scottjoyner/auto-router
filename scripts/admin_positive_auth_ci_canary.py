#!/usr/bin/env python3
"""CI-only positive and negative fixture-auth canary, with no response-body reads.

Only 5 explicitly safelisted administrative GET routes are eligible. The
provider-health route is deliberately excluded: its handler may probe
providers and update local signals, so it receives anonymous-only coverage.

Never run this against production, never read a real token, never send POST.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys

from scripts.admin_auth_readonly_canary import ADMIN_PATHS

# Matches the committed, purely synthetic CI Compose fixture. Not an operator
# credential, not an environment-secret lookup, never accepted as user input.
FIXTURE_TOKEN = "smoke-admin-token"
WRONG_TOKEN = "ci-invalid-operator-fixture"
SAFE_PATHS = (
    "/admin/providers",
    "/admin/context",
    "/admin/circuits",
    "/admin/agent-jobs",
    "/admin/admission",
)
assert set(SAFE_PATHS) < set(ADMIN_PATHS)
assert "/admin/providers/health" not in SAFE_PATHS


def request_status(port: int, path: str, token: str,
                   *, connection_factory=http.client.HTTPConnection) -> int | None:
    if type(port) is not int or port != 8088 or path not in SAFE_PATHS:
        raise ValueError("CI fixture canary requires 8088 and a safe read-only admin path")
    if token not in ("", WRONG_TOKEN, FIXTURE_TOKEN):
        raise ValueError("only public synthetic CI fixture credentials may be supplied")
    headers={
        "Accept":"application/json",
        "User-Agent":"auto-router-synthetic-auth-ci-status-only",
        "Connection":"close",
    }
    if token:
        headers["X-Admin-Token"]=token
    conn=connection_factory("127.0.0.1",port,timeout=2)
    try:
        conn.request("GET",path,headers=headers)
        resp=conn.getresponse()
        return resp.status  # Never read(), print or preserve the response body.
    except (OSError,http.client.HTTPException):
        return None
    finally:
        conn.close()


def check(*, port: int=8088, status_request=request_status) -> dict:
    rows=[]
    for path in SAFE_PATHS:
        for name,token,expected in (
            ("anonymous","",401),
            ("invalid_fixture",WRONG_TOKEN,401),
            ("valid_synthetic_fixture",FIXTURE_TOKEN,200),
        ):
            status=status_request(port,path,token)
            rows.append({
                "path":path,
                "identity_class":name,
                "status":status,
                "expected_status":expected,
                "pass":type(status) is int and status==expected,
            })
    return {
        "schema":"auto-router.synthetic-positive-admin-auth-ci.v1",
        "ci_only":True,
        "read_only":True,
        "response_bodies_read":False,
        "production_credential_used":False,
        "inference_request_sent":False,
        "provider_health_probed":False,
        "paths_tested":list(SAFE_PATHS),
        "checks":rows,
        "gate":"PASS_CI_FIXTURE_ONLY" if len(rows)==15 and all(x["pass"] for x in rows) else "HOLD",
        "production_ready":False,
    }


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-ci-synthetic-only",action="store_true")
    args=parser.parse_args()
    if not args.explicit_ci_synthetic_only:
        parser.error("explicit synthetic-only CI opt-in required")
    if os.environ.get("CI","").lower()!="true" or os.environ.get("GITHUB_ACTIONS","").lower()!="true":
        print(json.dumps({
            "schema":"auto-router.synthetic-positive-admin-auth-ci.v1",
            "gate":"HOLD","reason":"not_hosted_ci","production_ready":False,
        },sort_keys=True))
        return 2
    receipt=check()
    print(json.dumps(receipt,sort_keys=True))
    return 0 if receipt["gate"]=="PASS_CI_FIXTURE_ONLY" else 2


if __name__=="__main__":
    sys.exit(main())
