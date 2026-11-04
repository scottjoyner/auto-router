#!/usr/bin/env python3
"""Read-only verification of x1 recovery .deb cache copies.

NEVER authorizes package installation, service restarts, or boot changes.
The manifest hash is pinned; do not trust a package's self-reported digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
from pathlib import Path
from typing import Any

MANIFEST_SHA256 = "9b899a5c15a20df66bf9ae97d5c098aa19d3da3eca76fba20c632752eaf77cbc"
EXPECTED_PLAN_SHA256 = "33475a9a474331a27e389c071142a95585a1b82dcc2467a3f39b714cc3263f21"
EXPECTED_PACKAGES = {
    "libc6": ("2.43-2ubuntu2.4", "amd64"),
    "libc-bin": ("2.43-2ubuntu2.4", "amd64"),
    "libc-dev-bin": ("2.43-2ubuntu2.4", "amd64"),
    "libc6-dev": ("2.43-2ubuntu2.4", "amd64"),
    "libc6-dbg": ("2.43-2ubuntu2.4", "amd64"),
    "libc-gconv-modules-extra": ("2.43-2ubuntu2.4", "amd64"),
    "dpkg-dev": ("1.23.7ubuntu1", "all"),
    "libdpkg-perl": ("1.23.7ubuntu1", "all"),
    "make": ("4.4.1-3", "amd64"),
}
MAX_PACKAGE_BYTES = 30_000_000
MAX_TOTAL_BYTES = 50_000_000


def audit_manifest(directory: Path, manifest_data: bytes) -> dict[str, Any]:
    reasons: list[str] = []
    if hashlib.sha256(manifest_data).hexdigest() != MANIFEST_SHA256:
        reasons.append("untrusted_manifest_digest")
    try:
        payload = json.loads(manifest_data)
    except (UnicodeDecodeError, ValueError):
        payload = {}
        reasons.append("invalid_manifest_json")
    entries = payload.get("packages", {}) if isinstance(payload, dict) else {}
    if not isinstance(entries, dict) or set(entries) != set(EXPECTED_PACKAGES):
        entries = {}
        reasons.append("unexpected_package_manifest_set")
    if payload.get("apt_simulation_sha256_expected") != EXPECTED_PLAN_SHA256:
        reasons.append("unexpected_apt_plan_identity")

    totals = 0
    verified: list[str] = []
    for name, (version, architecture) in EXPECTED_PACKAGES.items():
        entry = entries.get(name)
        if not isinstance(entry, dict):
            reasons.append("missing_or_invalid_package_" + name)
            continue
        expected_filename = f"{name}_{version}_{architecture}.deb"
        if (
            entry.get("file") != expected_filename
            or entry.get("name") != name
            or entry.get("version") != version
            or entry.get("architecture") != architecture
        ):
            reasons.append("metadata_mismatch_" + name)
            continue
        path = directory / expected_filename
        try:
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= MAX_PACKAGE_BYTES:
                raise ValueError("invalid type or size")
            if entry.get("size") != st.st_size:
                raise ValueError("size differs")
            totals += st.st_size
            if totals > MAX_TOTAL_BYTES:
                raise ValueError("aggregate cap exceeded")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != entry.get("sha256"):
                raise ValueError("hash differs")
            p = subprocess.run(
                ["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"],
                capture_output=True,
                text=True,
                check=False,
                timeout=6,
            )
            fields = [line.split(":", 1)[-1].strip() for line in p.stdout.splitlines()]
            if p.returncode != 0 or fields != [name, version, architecture]:
                raise ValueError("deb control metadata mismatch")
            verified.append(name)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            reasons.append("package_verification_failed_" + name)
    return {
        "scope": "read_only_apt_recovery_bundle_audit",
        "bundle_verified": not reasons and len(verified) == len(EXPECTED_PACKAGES),
        "blocked": True,
        "may_install_packages": False,
        "may_restart_services": False,
        "may_reboot": False,
        "verified_package_count": len(verified),
        "observed_package_bytes": totals,
        "hold_reasons": sorted(set(reasons)),
        "required_physical_hold_reasons": [
            "full_offline_root_efi_image_not_verified",
            "out_of_band_console_not_verified",
            "real_package_scripts_not_rehearsed",
            "owner_maintenance_approval_required",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-dir",
        default="/nas/backups/x1-370-offline-apt-debs-20261103-cskmisux",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    directory = Path(args.bundle_dir)
    try:
        st = directory.lstat()
        if not stat.S_ISDIR(st.st_mode):
            raise OSError("bundle directory invalid")
        manifest = directory / "manifest.json"
        mst = manifest.lstat()
        if not stat.S_ISREG(mst.st_mode) or mst.st_size > 50_000:
            raise OSError("manifest not regular or too large")
        report = audit_manifest(directory, manifest.read_bytes())
    except OSError:
        report = {
            "scope": "read_only_apt_recovery_bundle_audit",
            "bundle_verified": False,
            "blocked": True,
            "may_install_packages": False,
            "hold_reasons": ["bundle_missing_or_unreadable"],
        }
    print(json.dumps(report, sort_keys=True))
    return 2 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
