#!/usr/bin/env python3
"""Read-only candidate Auto-Router configuration audit; never grants release.

Operator supplies candidate environment through a trusted secret manager.
Only fixed booleans are reported, never token bytes, hashes, paths or lengths.
No network, Docker interaction, repair, restart or credential mutation.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path

_SECRET_FIELDS = (
    "AUTO_ROUTER_ADMIN_TOKEN",
    "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN",
    "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS",
)
_PLACEHOLDERS = frozenset({
    "change-me", "changeme", "password", "admin", "secret", "test",
    "replace-me", "set-me", "example", "sample", "default",
})


def check(env: dict[str, str], compose: str, dockerfile: str) -> dict:
    values = [env.get(key, "") for key in _SECRET_FIELDS]
    configured = all(
        isinstance(value, str)
        and 32 <= len(value) <= 4096
        and value.strip() == value
        and value.lower() not in _PLACEHOLDERS
        and all(32 <= ord(char) <= 126 for char in value)
        for value in values
    )
    separated = configured and all(
        not hmac.compare_digest(values[i], values[j])
        for i in range(len(values))
        for j in range(i + 1, len(values))
    )
    basic_user = env.get("AUTO_ROUTER_ASSISTX_BASIC_AUTH_USER", "")
    host_port = env.get("AUTO_ROUTER_HOST_PORT", "")
    checks = {
        "candidate_loopback_ipv4_only": env.get("AUTO_ROUTER_BIND") == "127.0.0.1",
        "candidate_host_port_valid": (
            isinstance(host_port, str) and 1 <= len(host_port) <= 5
            and host_port.isdecimal() and 1 <= int(host_port) <= 65535
        ),
        "admin_inference_and_callback_credentials_present": configured,
        "admin_inference_and_callback_credentials_distinct": separated,
        "callback_basic_username_explicit": (
            isinstance(basic_user, str) and 1 <= len(basic_user) <= 128
            and basic_user.strip() == basic_user
        ),
        "compose_admin_token_explicit_failclosed": (
            "AUTO_ROUTER_ADMIN_TOKEN: ${AUTO_ROUTER_ADMIN_TOKEN:-}" in compose
        ),
        "compose_basic_example_password_removed": (
            "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS: ${AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS:-}"
            in compose
            and "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS:-change-me" not in compose
        ),
        "compose_host_bind_defaults_loopback": (
            "${AUTO_ROUTER_BIND:-127.0.0.1}" in compose
        ),
        "image_entrypoint_secure_live": (
            'CMD ["uvicorn", "auto_router.secure_live:app"' in dockerfile
        ),
        "strict_offline_requested": env.get("AUTO_ROUTER_STRICT_OFFLINE") == "true",
        "executor_auth_required": env.get("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED") == "true",
        "model_autoload_disabled": env.get("AUTO_ROUTER_AUTOLOAD_ENABLED") == "false",
        "agentgateway_disabled": env.get("AUTO_ROUTER_AGENTGATEWAY_ENABLED") == "false",
        "prompt_logging_disabled": env.get("AUTO_ROUTER_LOG_PROMPTS") == "false",
    }
    return {
        "schema": "auto-router.candidate-release-configuration.v1",
        "checks": checks,
        "configuration_checks_complete": all(checks.values()),
        "deployed_image_provenance_verified": False,
        "live_ipv4_ipv6_and_docker_peer_admin_denial_verified": False,
        "authorized_admin_client_compatibility_verified": False,
        "restorable_backup_and_rollback_verified": False,
        "live_production_changed": False,
        "decision": "HOLD",
        "production_admission": "NO_GO",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-read-only-candidate-review",
                        action="store_true", required=True)
    parser.add_argument("--compose-file", type=Path,
                        default=Path("docker-compose.yml"))
    parser.add_argument("--dockerfile", type=Path,
                        default=Path("Dockerfile"))
    args = parser.parse_args(argv)
    try:
        source = args.compose_file.read_text(encoding="utf-8")
        dockerfile = args.dockerfile.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        source, dockerfile = "", ""
    print(json.dumps(check(dict(os.environ), source, dockerfile), sort_keys=True))
    return 2  # Candidate source/env can never authorize real cutover.


if __name__ == "__main__":
    raise SystemExit(main())
