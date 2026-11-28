#!/usr/bin/env python3
"""Read-only attestation of the *installed* Auto-Router security source.

Operator provides reviewed SHA-256 pins; hashes and stable safety markers are
compared against the importable package and *effective* mounted provider config.
A match is source identity only, never service health or deployment authority.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

MAX_CODE_BYTES = 700_000
MAX_CONFIG_BYTES = 400_000
REQUIRED_MARKER = b"AssistX event delivery transport failure"
UNSAFE_MARKER = b"response.text[:500]"
XWING_OPTIN_MARKER = b"LMSTUDIO_XWING_API_KEY_FILE"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class IdentityHold(Exception):
    pass


def _safe_bytes(path: Path, limit: int) -> bytes:
    if not path.is_absolute() or ".." in path.parts:
        raise IdentityHold("invalid_path")
    for part in (path, *path.parents):
        try:
            mode = part.lstat().st_mode
        except OSError:
            raise IdentityHold("path_unavailable") from None
        if stat.S_ISLNK(mode):
            raise IdentityHold("symlink_path")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        raise IdentityHold("file_unavailable") from None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise IdentityHold("file_not_regular_or_bounded")
        if before.st_mode & 0o022:
            raise IdentityHold("file_group_or_other_writable")
        data = os.read(fd, limit + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size,
                              s.st_mtime_ns, s.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after):
            raise IdentityHold("file_changed_during_read")
        return data
    finally:
        os.close(fd)


def inspect(package_root: Path, config: Path, expected: dict[str, str]) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "router-installed-source-identity/v1",
        "verdict": "HOLD",
        "installed_module_identity_matched": False,
        "effective_config_identity_matched": False,
        "deployed_image_provenance_verified": False,
        "deployment_authorized": False,
        "asgi_lifespan_started": False,
        "provider_calls": 0,
        "archive_writes": 0,
    }
    try:
        if set(expected) != {"providers.py", "event_dispatcher.py", "providers.yaml"}:
            raise IdentityHold("missing_pin")
        if any(not isinstance(v, str) or not SHA256.fullmatch(v)
               for v in expected.values()):
            raise IdentityHold("invalid_sha256_pin")
        if not package_root.is_absolute() or not config.is_absolute():
            raise IdentityHold("unqualified_path")
        p = _safe_bytes(package_root / "providers.py", MAX_CODE_BYTES)
        e = _safe_bytes(package_root / "event_dispatcher.py", MAX_CODE_BYTES)
        c = _safe_bytes(config, MAX_CONFIG_BYTES)
        if UNSAFE_MARKER in p or UNSAFE_MARKER in e:
            raise IdentityHold("upstream_error_echo_marker_present")
        if REQUIRED_MARKER not in e:
            raise IdentityHold("sanitized_event_error_marker_absent")
        if XWING_OPTIN_MARKER not in c:
            raise IdentityHold("xwing_optional_token_file_marker_absent")
        actual = {
            "providers.py": hashlib.sha256(p).hexdigest(),
            "event_dispatcher.py": hashlib.sha256(e).hexdigest(),
            "providers.yaml": hashlib.sha256(c).hexdigest(),
        }
        if actual["providers.py"] != expected["providers.py"] or (
            actual["event_dispatcher.py"] != expected["event_dispatcher.py"]
        ):
            raise IdentityHold("installed_source_digest_mismatch")
        report["installed_module_identity_matched"] = True
        if actual["providers.yaml"] != expected["providers.yaml"]:
            raise IdentityHold("effective_config_digest_mismatch")
        report["effective_config_identity_matched"] = True
    except IdentityHold as exc:
        report["reason"] = str(exc)
        return report
    report["verdict"] = "SOURCE_IDENTITY_MATCH_ONLY"
    report["reason"] = "operator_deployment_and_runtime_gates_still_required"
    return report


def package_root() -> Path:
    spec = importlib.util.find_spec("auto_router")
    locations = list(spec.submodule_search_locations or ()) if spec else []
    if len(locations) != 1:
        raise IdentityHold("package_location_ambiguous")
    root = Path(locations[0])
    if not root.is_absolute():
        raise IdentityHold("package_not_absolute")
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only installed source identity gate")
    parser.add_argument("--providers-sha256", required=True)
    parser.add_argument("--dispatcher-sha256", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--config-path", type=Path, default=Path("/app/config/providers.yaml"))
    args = parser.parse_args()
    pins = {"providers.py": args.providers_sha256,
            "event_dispatcher.py": args.dispatcher_sha256,
            "providers.yaml": args.config_sha256}
    try:
        result = inspect(package_root(), args.config_path, pins)
    except IdentityHold as exc:
        result = {"schema": "router-installed-source-identity/v1",
                  "verdict": "HOLD", "reason": str(exc),
                  "deployment_authorized": False, "asgi_lifespan_started": False,
                  "provider_calls": 0, "archive_writes": 0}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "SOURCE_IDENTITY_MATCH_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
