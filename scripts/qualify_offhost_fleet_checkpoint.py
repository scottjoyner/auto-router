#!/usr/bin/env python3
"""Read-only off-host checkpoint witness; SSH executes temporary stdin Python.

NO service installation, no network listener, no production endpoints, no
persisted signing keys, and no use of the live router's model-report route.
The observer public key is self-presented over this SSH connection and is NOT
independently pinned. Result is a negative-rollback engineering witness only.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from auto_router.fleet_publisher_checkpoint import (
    ObserverCheckpointError,
    TrustedObserver,
    compare_latest_checkpoint,
)
from auto_router.fleet_publisher_envelope import LocalSQLiteReplayFence

A = "joyner-model-catalog"
B = "joyner-gpu-heartbeat"
HOST = "lenovo"
_DOMAIN = b"auto-router:temporary-offhost-observer:v1\x00"
_MAX_OUTPUT = 15000


class OffhostWitnessError(ValueError):
    """No accepted off-host witness; messages omit raw telemetry."""


def _decode(value: Any, length: int) -> bytes:
    if not isinstance(value, str) or len(value) > 120:
        raise OffhostWitnessError("off-host evidence has invalid encoding")
    try:
        decoded = base64.b64decode(
            value + "=" * ((-len(value)) % 4), altchars=b"-_", validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise OffhostWitnessError("off-host evidence encoding rejected") from exc
    if len(decoded) != length or base64.urlsafe_b64encode(
        decoded
    ).decode().rstrip("=") != value:
        raise OffhostWitnessError("off-host evidence not canonical")
    return decoded


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def verify_transcript(
    data: dict[str, Any], *, challenge: str,
    pinned_observer_public_key: bytes | None = None,
    require_pinned_identity: bool = False,
) -> TrustedObserver:
    """Validate a challenge and optionally an operator-supplied public-key pin.

    A pin is proof of key consistency, not proof of independent key custody.
    Without one, the key is self-presented and can be swapped by the sender.
    """
    if require_pinned_identity and pinned_observer_public_key is None:
        raise OffhostWitnessError("independently pinned observer key required")
    if (pinned_observer_public_key is not None and
            (not isinstance(pinned_observer_public_key, bytes)
             or len(pinned_observer_public_key) != 32)):
        raise OffhostWitnessError("observer public-key pin format rejected")
    if not isinstance(data, dict) or set(data) != {
        "schema", "observer_public_key", "challenge", "host_role",
        "old_checkpoint", "latest_checkpoint", "challenge_proof",
        "separate_custody_verified", "production_admission",
    }:
        raise OffhostWitnessError("off-host transcript shape rejected")
    if (data["schema"] != "fleet-offhost-research-transcript.v1"
            or data["challenge"] != challenge
            or re.fullmatch(r"[0-9a-f]{32}", challenge) is None
            or data["host_role"] != "disposable-observer"
            or data["separate_custody_verified"] is not False
            or data["production_admission"] != "NO_GO"):
        raise OffhostWitnessError("off-host transcript claim rejected")
    old = data["old_checkpoint"]
    new = data["latest_checkpoint"]
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise OffhostWitnessError("off-host checkpoints missing")
    if (old.get("generation") != 1 or new.get("generation") != 2 or
            old.get("highwater") != {A: 1, B: 1} or
            new.get("highwater") != {A: 2, B: 1}):
        raise OffhostWitnessError("off-host generation CAS result unexpected")
    public_bytes = _decode(data["observer_public_key"], 32)
    if (pinned_observer_public_key is not None
            and not hmac.compare_digest(public_bytes, pinned_observer_public_key)):
        raise OffhostWitnessError("observer signing key differs from operator pin")
    key = Ed25519PublicKey.from_public_bytes(public_bytes)
    transcript_message = (
        _DOMAIN + bytes.fromhex(challenge) +
        hashlib.sha256(_canonical(new)).digest()
    )
    try:
        key.verify(_decode(data["challenge_proof"], 64), transcript_message)
    except InvalidSignature as exc:
        raise OffhostWitnessError("off-host challenge signature rejected") from exc
    return TrustedObserver(
        "lenovo-research-observer", "lenovo-research-observer-ephemeral-v1",
        key, (A, B),
    )


def check_remote_evidence(
    data: dict[str, Any], *, challenge: str, now_ms: int,
    pinned_observer_public_key: bytes | None = None,
    require_pinned_identity: bool = False,
) -> dict[str, Any]:
    """Run rollback tests, allowing a strict externally supplied public-key pin."""
    observer = verify_transcript(
        data, challenge=challenge,
        pinned_observer_public_key=pinned_observer_public_key,
        require_pinned_identity=require_pinned_identity,
    )
    with tempfile.TemporaryDirectory(prefix="r9700-local-fence-test-") as folder:
        store = Path(folder) / "local.sqlite3"
        backup = Path(folder) / "old-local.sqlite3"
        fence = LocalSQLiteReplayFence(store)
        fence.consume(A, 1)
        fence.consume(B, 1)
        before = compare_latest_checkpoint(
            data["old_checkpoint"], trusted_observer=observer,
            externally_pinned_generation=1, local_database=store,
            now_ms=now_ms,
        )
        if before.production_admission != "NO_GO":
            raise OffhostWitnessError("remote proof unexpectedly admitted routing")
        shutil.copyfile(store, backup)
        fence.consume(A, 2)
        current = compare_latest_checkpoint(
            data["latest_checkpoint"], trusted_observer=observer,
            externally_pinned_generation=2, local_database=store,
            now_ms=now_ms,
        )
        if current.production_admission != "NO_GO":
            raise OffhostWitnessError("remote proof unexpectedly admitted routing")

        # Roll back ONLY the local research state; the separately signed
        # checkpoint remains at generation 2, which must detect the conflict.
        shutil.copyfile(backup, store)
        blocked_local_rollback = False
        try:
            compare_latest_checkpoint(
                data["latest_checkpoint"], trusted_observer=observer,
                externally_pinned_generation=2, local_database=store,
                now_ms=now_ms,
            )
        except ObserverCheckpointError:
            blocked_local_rollback = True
        if not blocked_local_rollback:
            raise OffhostWitnessError("local rollback not detected")

        blocked_old_checkpoint = False
        try:
            compare_latest_checkpoint(
                data["old_checkpoint"], trusted_observer=observer,
                externally_pinned_generation=2, local_database=store,
                now_ms=now_ms,
            )
        except ObserverCheckpointError:
            blocked_old_checkpoint = True
        if not blocked_old_checkpoint:
            raise OffhostWitnessError("older signed generation not rejected")

        # The caller can still *falsely* accept old generation 1 if it loses
        # custody of its latest-generation anchor, thus production stays NO_GO.
        old_with_rolled_anchor = compare_latest_checkpoint(
            data["old_checkpoint"], trusted_observer=observer,
            externally_pinned_generation=1, local_database=store,
            now_ms=now_ms,
        )
        if old_with_rolled_anchor.production_admission != "NO_GO":
            raise OffhostWitnessError("rollback experiment unexpectedly admitted")
    return {
        "schema": "offhost-checkpoint-qualification.v1",
        "read_only_production": True,
        "physical_remote_test": True,
        "remote_host": HOST,
        "remote_key_provisioned": False,
        "remote_key_trust": (
            "OPERATOR_SUPPLIED_PUBLIC_KEY_PIN_NOT_CUSTODY_PROOF"
            if pinned_observer_public_key is not None
            else "SELF_PRESENTED_SSH_HANDOFF_ONLY"
        ),
        "observer_identity_pin_checked": pinned_observer_public_key is not None,
        "independent_admin_custody_verified": False,
        "checkpoint_genesis_is_synthetic": True,
        "signed_remote_checkpoint_verified": True,
        "challenge_bound_transcript_verified": True,
        "remote_generation_cas_observed": True,
        "local_db_rollback_detected_with_current_remote_checkpoint": True,
        "old_checkpoint_rejected_against_generation_2": True,
        "combined_generation_anchor_rollback_unprevented": True,
        "external_authority_verified": False,
        "model_gpu_residency_verified": False,
        "production_admission": "NO_GO",
    }


def run_physical(
    host: str, source: Path, *, timeout: int = 20,
    pinned_observer_public_key: bytes | None = None,
    require_pinned_identity: bool = False,
) -> dict[str, Any]:
    if require_pinned_identity and pinned_observer_public_key is None:
        raise OffhostWitnessError("independently pinned observer key required")
    if host != HOST:
        raise OffhostWitnessError("only the pinned research host is supported")
    if not source.is_file() or source.stat().st_size > 12000:
        raise OffhostWitnessError("remote research source missing or oversized")
    challenge = secrets.token_hex(16)
    try:
        outcome = subprocess.run(
            ["ssh", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=yes",
             "-o", "ConnectTimeout=6", host, "python3", "-", challenge],
            input=source.read_bytes(), capture_output=True, timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OffhostWitnessError("remote observer qualification unavailable") from exc
    if outcome.returncode != 0 or len(outcome.stdout) > _MAX_OUTPUT:
        raise OffhostWitnessError("remote observer qualification failed closed")
    try:
        data = json.loads(outcome.stdout)
    except (ValueError, UnicodeError) as exc:
        raise OffhostWitnessError("remote observer output invalid") from exc
    return check_remote_evidence(
        data, challenge=challenge, now_ms=int(time.time() * 1000),
        pinned_observer_public_key=pinned_observer_public_key,
        require_pinned_identity=require_pinned_identity,
    )


def read_observer_public_pin(path: Path) -> bytes:
    """Read exactly 32 public bytes without following a final symlink.

    The file is operator supplied; ownership/custody CANNOT be inferred from
    a successful read or from any Unix permissions.
    """
    if not path.is_absolute():
        raise OffhostWitnessError("observer public-key pin path must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != 32:
                raise OffhostWitnessError("observer public-key pin file is invalid")
            public_bytes = os.read(fd, 33)
        finally:
            os.close(fd)
    except OSError as exc:
        raise OffhostWitnessError("observer public-key pin file is unavailable") from exc
    if len(public_bytes) != 32:
        raise OffhostWitnessError("observer public-key pin file has wrong length")
    return public_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST, choices=[HOST])
    parser.add_argument(
        "--require-pinned-identity", action="store_true",
        help="Fail closed unless an explicit raw 32-byte Ed25519 public key is supplied",
    )
    parser.add_argument(
        "--observer-public-key-file", type=Path,
        help="Previously provisioned raw public-key pin; does not certify separate custody",
    )
    args = parser.parse_args()
    pinned_key = (
        read_observer_public_pin(args.observer_public_key_file)
        if args.observer_public_key_file is not None else None
    )
    source = Path(__file__).resolve().with_name("temporary_offhost_observer.py")
    result = run_physical(
        args.host, source, pinned_observer_public_key=pinned_key,
        require_pinned_identity=args.require_pinned_identity,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())