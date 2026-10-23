#!/usr/bin/env python3
"""Write CI-only exact-source + disposable-image administrative canary evidence.

Produces hashes and booleans only: no credentials, environment dump, URL
contents, provider config, or response body. Does NOT authorize deployment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CANARY = ROOT / "scripts" / "verify_admin_canary_ci.py"

class EvidenceDenied(RuntimeError):
    pass

def checked_output(*argv: str) -> str:
    return subprocess.run(argv, cwd=ROOT, check=True, capture_output=True,
                          text=True, timeout=24).stdout.strip()

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def build_evidence(env: dict[str, str] | None = None) -> dict:
    e = dict(os.environ if env is None else env)
    if (e.get("GITHUB_ACTIONS") != "true"
        or e.get("AUTO_ROUTER_CANARY_ENV") != "disposable-github-ci"
        or e.get("AUTO_ROUTER_CANARY_SYNTHETIC_ADMIN_TOKEN") != "smoke-admin-token"):
        raise EvidenceDenied("requires disposable GitHub integration environment")
    commit=e.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise EvidenceDenied("exact GitHub source commit not provided")
    current=checked_output("git","rev-parse","HEAD")
    if current != commit:
        raise EvidenceDenied("CI checkout identity differs from reported source")
    # Verify the tracked source and verification script are not dirty.
    if checked_output("git","status","--porcelain","--untracked-files=no"):
        raise EvidenceDenied("tracked CI checkout differs from source commit")
    image_id=checked_output("docker","inspect","auto-router-smoke",
                            "--format","{{.Image}}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise EvidenceDenied("missing exact container image identity")
    # Ensure the full application tested here is a disposable CI container.
    service=checked_output("docker","inspect","auto-router-smoke",
                           "--format","{{index .Config.Labels \"com.docker.compose.service\"}}")
    if service != "llm-router":
        raise EvidenceDenied("unqualified compose service")
    result=subprocess.run(["python3",str(CANARY)],cwd=ROOT,env=e,
                          capture_output=True,text=True,timeout=45)
    if result.returncode!=0:
        raise EvidenceDenied("full-ASGI admin canary failed")
    try:
        canary=json.loads(result.stdout.strip())
    except (ValueError,TypeError) as exc:
        raise EvidenceDenied("missing machine-readable canary proof") from exc
    if (canary.get("result")!="DISPOSABLE_FULL_ASGI_ADMIN_AUTH_PASS"
        or canary.get("checked_routes")!=10
        or canary.get("public_client_reads_pass") is not True
        or canary.get("denied_missing_and_wrong_token") is not True
        or canary.get("authorized_read_pass") is not True
        or canary.get("effective_snapshot",{}).get("sanitized") is not True
        or canary.get("production_tested") is not False
        or canary.get("synthetic_credentials_only") is not True):
        raise EvidenceDenied("incomplete canary proof")
    return {
        "schema":1,"status":"DISPOSABLE_CI_ADMIN_AUTH_EVIDENCE",
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "source_commit":commit,
        "git_source_tree":checked_output("git","rev-parse","HEAD^{tree}"),
        "ci_container_image_id":image_id,
        "source_sha256":{
            name:digest(ROOT/name) for name in (
                "src/auto_router/main.py",
                "src/auto_router/effective_provider_snapshot.py",
                "src/auto_router/security.py",
                "src/auto_router/secure_live.py",
                "scripts/verify_admin_canary_ci.py",
            )
        },
        "canary_result":canary["result"],
        "checked_admin_route_count":canary["checked_routes"],
        "missing_and_wrong_token_rejected":True,
        "public_client_reads_preserved":True,
        "synthetic_operator_auth_accepted":True,
        "sanitized_provider_response":True,
        "provider_count":canary["effective_snapshot"]["provider_count"],
        "network":"disposable_GitHub_integration_stack_only",
        "production_image_identity_qualified":False,
        "production_deployment_authorized":False,
        "production_cutover_performed":False,
        "rollback_proven":False,
        "independent_archival_custody_proven":False,
    }

def main():
    document=build_evidence()
    out=Path(os.environ.get("AUTO_ROUTER_CI_EVIDENCE_PATH",
                            "/tmp/auto-router-admin-ci-evidence.json"))
    if not out.is_absolute() or out.is_symlink() or out.exists():
        raise EvidenceDenied("unsafe evidence destination")
    fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w") as file:
        json.dump(document,file,sort_keys=True,indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    print(json.dumps({"status":document["status"],
                      "source_commit":document["source_commit"],
                      "ci_container_image_id":document["ci_container_image_id"],
                      "evidence_sha256":digest(out),
                      "not_production":True},sort_keys=True))

if __name__=="__main__":
    main()
