#!/usr/bin/env python3
"""Disposable offline observer witness: runs via SSH stdin, no installed service.

All Ed25519 private-key bytes exist ONLY in this process's memory. SQLite files
live in a TemporaryDirectory, which is cleaned before the process exits. This
is a reproducible proof of PHYSICAL separation, never independent admin custody.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sqlite3
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DOMAIN = b"auto-router:fleet-independent-checkpoint:v1\x00"
PROBE_DOMAIN = b"auto-router:temporary-offhost-observer:v1\x00"
A = "joyner-model-catalog"
B = "joyner-gpu-heartbeat"
OBSERVER_ID = "lenovo-research-observer"
KEY_ID = "lenovo-research-observer-ephemeral-v1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sign_checkpoint(key: Ed25519PrivateKey, generation: int,
                     highwater: dict[str, int], now_ms: int) -> dict:
    checkpoint = {
        "schema": "fleet-independent-checkpoint.v1",
        "observer_id": OBSERVER_ID,
        "key_id": KEY_ID,
        "generation": generation,
        "issued_at_ms": now_ms,
        "expires_at_ms": now_ms + 30000,
        "highwater": highwater,
        "revoked_publishers": [],
    }
    signature = key.sign(DOMAIN + _canonical(checkpoint))
    return {**checkpoint, "signature": _b64(signature)}


def _transaction_step(db: Path, key: Ed25519PrivateKey, seq: int, now_ms: int) -> dict:
    connection = sqlite3.connect(str(db), isolation_level=None)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            old = connection.execute(
                "SELECT generation, catalog_seq, gpu_seq FROM observer"
            ).fetchone()
            if old is None or seq != old[1] + 1:
                raise ValueError("non-monotonic synthetic highwater")
            nxt = old[0] + 1
            connection.execute(
                "UPDATE observer SET generation=?,catalog_seq=?",
                (nxt, seq),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return _sign_checkpoint(key, nxt, {A: seq, B: old[2]}, now_ms)


def produce_transcript(challenge: str) -> dict:
    """Generate an ephemeral synthetic, signed observer before cleanup."""
    if not isinstance(challenge, str) or not re.fullmatch(r"[0-9a-f]{32}", challenge):
        raise ValueError("expected 16-byte lowercase hex challenge")
    signing_key = Ed25519PrivateKey.generate()
    public_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    now_ms = int(time.time() * 1000)
    with tempfile.TemporaryDirectory(prefix="fleet-observer-research-") as root:
        db = Path(root) / "observer.sqlite3"
        connection = sqlite3.connect(str(db))
        try:
            connection.execute(
                "CREATE TABLE observer (generation INTEGER NOT NULL,"
                "catalog_seq INTEGER NOT NULL,gpu_seq INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO observer (generation,catalog_seq,gpu_seq) VALUES(1,1,1)"
            )
            connection.commit()
        finally:
            connection.close()
        old = _sign_checkpoint(signing_key, 1, {A: 1, B: 1}, now_ms)
        new = _transaction_step(db, signing_key, 2, now_ms)
        connection = sqlite3.connect(str(db))
        try:
            state = connection.execute(
                "SELECT generation,catalog_seq,gpu_seq FROM observer"
            ).fetchone()
        finally:
            connection.close()
        if state != (2, 2, 1):
            raise AssertionError("observer CAS state inconsistent")
        # A challenge-bound receipt proves the same ephemeral key signed the
        # response for this invocation; it does NOT attest its machine custody.
        proof = signing_key.sign(
            PROBE_DOMAIN + bytes.fromhex(challenge) + hashlib.sha256(
                _canonical(new)
            ).digest()
        )
        return {
            "schema": "fleet-offhost-research-transcript.v1",
            "observer_public_key": _b64(public_key),
            "challenge": challenge,
            "host_role": "disposable-observer",
            "old_checkpoint": old,
            "latest_checkpoint": new,
            "challenge_proof": _b64(proof),
            "separate_custody_verified": False,
            "production_admission": "NO_GO",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("challenge")
    args = parser.parse_args()
    print(json.dumps(produce_transcript(args.challenge), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())