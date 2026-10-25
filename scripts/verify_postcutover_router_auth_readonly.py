#!/usr/bin/env python3
"""Read-only Router post-cutover client/Tailnet auth check. NOT a deployer.

Refuses all HTTP/SSH/Docker exec until an explicitly pinned NEW Router image
is already running and operator provides --authorize-readonly-check.
Only fixed GET requests; no HTTP bodies, no production writes, no model calls.
Successful checks NEVER authorize deployment, rollback or provider execution.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess

OLD_VULNERABLE = "sha256:182d3d3c8aa6b8afbc45bb5094493f74baa58dbb5c0b0b54555b5718f03c1dd8"
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")
ROUTER = "git-router-1"
CLIENTS = ("assistx-api", "assistx-worker", "assistx-hermes-adapter")
# These known passive GET routes are safe to probe without a token, even if
# authentication is accidentally missing. Do NOT call providers/health here:
# it may trigger downstream provider probes before an auth bypass is detected.
ADMIN_READS = ("/admin/providers", "/admin/circuits",
               "/admin/usage", "/admin/context")
PUBLIC_READS = ("/health", "/v1/models")

# Source delivered over trusted SSH stdin. It uses Python's no-proxy opener,
# never sends a credential, never reads a response body and never redirects.
REMOTE_CHECK = r'''
import ipaddress,json,sys,urllib.request,urllib.error
ip=ipaddress.ip_address(sys.argv[1])
if ip.version!=4 or ip not in ipaddress.ip_network("100.64.0.0/10"):
    raise SystemExit(2)
# Candidate safety check: only passive, side-effect-free administrative GETs.
paths=("/admin/providers","/admin/circuits","/admin/usage","/admin/context")
public=("/health","/v1/models")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def status(path):
    req=urllib.request.Request("http://"+str(ip)+":8088"+path,method="GET")
    try:
        with opener.open(req,timeout=3) as response:return response.status
    except urllib.error.HTTPError as exc:
        code=exc.code
        exc.close()
        return code
    except (OSError,TimeoutError,urllib.error.URLError):return 0
ok=all([status(path)==401 for path in paths])
public_ok=all([status(path)==200 for path in public])
print(json.dumps({"direct_tailnet_admin_denied":ok,"public_gets_ok":public_ok,
                   "admin_get_count":len(paths),"public_get_count":len(public),
                   "body_read":False,"credentials_sent":False,"provider_calls_requested":0}))
raise SystemExit(0 if ok and public_ok else 2)
'''

# Only reads existing admin-token from each client's own environment, in memory.
# Exact fixed Router URL and GET paths; avoids all proxy/redirect behavior.
CLIENT_CHECK = r'''
import json,os,urllib.request,urllib.error,urllib.parse
base=os.environ.get("AUTO_ROUTER_BASE_URL","").strip()
p=urllib.parse.urlsplit(base)
token=os.environ.get("AUTO_ROUTER_ADMIN_TOKEN","")
if (p.scheme!="http" or p.netloc!="host.docker.internal:8088"
    or p.path not in ("","/") or p.query or p.fragment
    or not token.strip()):
    raise SystemExit(2)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def status(path):
    req=urllib.request.Request("http://host.docker.internal:8088"+path,
                               method="GET",headers={"X-Admin-Token":token})
    try:
        with opener.open(req,timeout=3) as response:return response.status
    except urllib.error.HTTPError as exc:
        code=exc.code
        exc.close()
        return code
    except (OSError,TimeoutError,urllib.error.URLError):return 0
paths=("/admin/providers","/admin/context","/admin/admission")
ok=all([status(path)==200 for path in paths])
print(json.dumps({"authorized_admin_read_ok":ok,"admin_read_count":len(paths),
                   "body_read":False,"token_emitted":False,"mutations":0,
                   "provider_requests_sent":0}))
raise SystemExit(0 if ok else 2)
'''


class PostCutoverDenied(RuntimeError):
    pass


def command(argv: list[str], *, input_text: str | None = None,
            timeout: int = 12, max_output: int = 4096) -> tuple[int, str]:
    """No shell; callers own a fixed, allowlisted command."""
    try:
        result = subprocess.run(argv, input=input_text, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise PostCutoverDenied("bounded check unavailable") from None
    if len(result.stdout) > max_output:
        raise PostCutoverDenied("oversized check output")
    return result.returncode, result.stdout


def image_readback() -> tuple[str, bool]:
    rc, body = command(["docker", "inspect", ROUTER], timeout=8,
                       max_output=2_000_000)
    if rc != 0:
        raise PostCutoverDenied("Router inspect unavailable")
    try:
        data = json.loads(body)
        if (not isinstance(data, list) or len(data) != 1
                or data[0].get("Name") != "/" + ROUTER):
            raise ValueError("identity mismatch")
        return data[0]["Image"], data[0]["State"]["Status"] == "running"
    except (TypeError, KeyError, ValueError):
        raise PostCutoverDenied("invalid Router image readback") from None


def eligible_image(expected: str, actual: str, running: bool,
                   opt_in: bool) -> list[str]:
    problems = []
    if not IMAGE_ID.fullmatch(expected) or expected == OLD_VULNERABLE:
        problems.append("PIN_NOT_A_NEW_IMMUTABLE_IMAGE")
    if actual != expected:
        problems.append("RUNNING_IMAGE_MISMATCH")
    if not running:
        problems.append("ROUTER_NOT_RUNNING")
    if not opt_in:
        problems.append("OPERATOR_READONLY_OPT_IN_REQUIRED")
    return problems


def strict_tailnet_address() -> str:
    rc, raw = command(["tailscale", "ip", "-4"], timeout=8)
    if rc:
        raise PostCutoverDenied("local Tailnet identity unavailable")
    addresses = raw.strip().splitlines()
    if len(addresses) != 1:
        raise PostCutoverDenied("ambiguous local Tailnet identity")
    try:
        value = ipaddress.ip_address(addresses[0])
    except ValueError:
        raise PostCutoverDenied("invalid Tailnet address") from None
    if value.version != 4 or value not in ipaddress.ip_network("100.64.0.0/10"):
        raise PostCutoverDenied("unapproved Tailnet address")
    return str(value)


def remote_negative_check(ip: str) -> dict:
    rc, raw = command([
        "ssh", "-T", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5",
        "xwing", "python3", "-", ip,
    ], input_text=REMOTE_CHECK, timeout=45)
    return checked_result(raw, rc, {
        "direct_tailnet_admin_denied": True,
        "public_gets_ok": True,
        "admin_get_count": len(ADMIN_READS),
        "public_get_count": len(PUBLIC_READS),
        "body_read": False, "credentials_sent": False, "provider_calls_requested": 0,
    })


def real_client_check(name: str) -> dict:
    if name not in CLIENTS:
        raise PostCutoverDenied("unapproved client container")
    rc, raw = command(["docker", "exec", "-i", name, "python3", "-"],
                      input_text=CLIENT_CHECK, timeout=20)
    return checked_result(raw, rc, {
        "authorized_admin_read_ok": True, "admin_read_count": 3,
        "body_read": False, "token_emitted": False,
        "mutations": 0, "provider_requests_sent": 0,
    })


def checked_result(raw: str, rc: int, required: dict) -> dict:
    if rc != 0:
        raise PostCutoverDenied("client or Tailnet check denied")
    try:
        doc = json.loads(raw)
    except ValueError:
        raise PostCutoverDenied("invalid readback") from None
    if not isinstance(doc, dict) or not all(
        type(doc.get(key)) is type(value) and doc.get(key) == value
        for key, value in required.items()
    ):
        raise PostCutoverDenied("incomplete or overstated readback")
    return doc


def hold_report(verdict: str, problems: list[str]) -> dict:
    return {
        "schema": 1, "verdict": verdict, "blockers": sorted(problems),
        "production_cutover_authorized": False, "rollback_verified": False,
        "provider_effects_authorized": False, "independent_custody_verified": False,
        "no_http_body_read": True, "no_admin_mutations": True,
        "no_model_requests": True, "secrets_emitted": False,
    }


def validate(expected: str, opt_in: bool) -> tuple[dict, int]:
    # Unpinned / old image must be rejected before any Docker/SSH/HTTP operations.
    if not IMAGE_ID.fullmatch(expected) or expected == OLD_VULNERABLE:
        return hold_report("UNSAFE_IMAGE_PIN_HOLD", ["PIN_NOT_A_NEW_IMMUTABLE_IMAGE"]), 2
    try:
        actual, running = image_readback()
        problems = eligible_image(expected, actual, running, opt_in)
        if problems:
            return hold_report("IMAGE_OR_APPROVAL_HOLD", problems), 2
        ip = strict_tailnet_address()
        remote_negative_check(ip)
        for name in CLIENTS:
            # Check the local image again between remote client calls.
            if image_readback() != (expected, True):
                raise PostCutoverDenied("Router image changed during validation")
            real_client_check(name)
        if image_readback() != (expected, True):
            raise PostCutoverDenied("Router changed during validation")
    except PostCutoverDenied:
        return hold_report("POSTCUTOVER_ACCEPTANCE_FAILED_HOLD",
                           ["BOUND_CHECK_FAILED_OR_IMAGE_DRIFT"]), 2
    # This is an acceptance receipt, not a release decision or rollback proof.
    return hold_report("READONLY_POSTCUTOVER_AUTH_CHECKS_PASS_RELEASE_HOLD", []), 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Router post-cutover read-only acceptance")
    parser.add_argument("--expected-candidate-image", required=True)
    parser.add_argument("--authorize-readonly-check", action="store_true")
    args = parser.parse_args()
    report, status = validate(args.expected_candidate_image,
                              args.authorize_readonly_check)
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
