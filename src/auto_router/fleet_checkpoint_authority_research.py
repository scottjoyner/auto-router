"""Offline simulated checkpoint-issuing authority for fleet publisher research.

Never networked, never wired to a live router, and never deployed. The database
and observer signing key in these fixtures share the operator's custody, so
restoring both defeats rollback protection. Even valid Ed25519 checkpoint
readback is NOT a physical independent witness or production admission.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .fleet_publisher_checkpoint import checkpoint_signing_message
from .fleet_publisher_envelope import (
    PublisherEnvelopeError,
    SignedFleetPublisherVerifier,
    TrustedPublisher,
)

_SCHEMA = "fleet-independent-checkpoint.v1"
_MAX_GENERATION = 2**63 - 1
_MAX_LEASE_MS = 30000


class ResearchAuthorityError(ValueError):
    """Invalid offline authority operation; never echoes untrusted payloads."""


def _is_int(value: Any) -> bool:
    return type(value) is int


def _fingerprint(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def _configuration(
    *,
    observer_id: str, key_id: str, private_key: Ed25519PrivateKey,
    trust_roots: Mapping[str, TrustedPublisher],
) -> str:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("offline observer key must be Ed25519")
    if not observer_id or not key_id or not trust_roots:
        raise ValueError("offline observer configuration cannot be empty")
    ids: dict[str, dict[str, str]] = {}
    for pid, root in trust_roots.items():
        if pid != root.publisher_id:
            raise ValueError("publisher id does not match trust root")
        raw = root.verify_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if hashlib.sha256(raw).hexdigest() == _fingerprint(private_key):
            raise ValueError("observer key cannot be reused for publisher")
        ids[pid] = {
            "node": root.node_id, "role": root.role,
            "key_id": root.key_id, "fingerprint": hashlib.sha256(raw).hexdigest(),
        }
    return json.dumps({
        "observer_id": observer_id, "key_id": key_id,
        "observer_key_fingerprint": _fingerprint(private_key),
        "publishers": ids,
    }, sort_keys=True, separators=(",", ":"))


class OfflineCheckpointAuthority:
    """CAS / signed readback research fixture. NOT trusted production authority."""

    def __init__(
        self, path: Path, *,
        observer_id: str, key_id: str,
        private_key: Ed25519PrivateKey,
        trust_roots: Mapping[str, TrustedPublisher],
        bootstrap_highwater: Mapping[str, int],
        initial_issued_at_ms: int,
    ) -> None:
        if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
            raise ValueError("offline authority requires a non-symlink absolute SQLite path")
        if not path.parent.is_dir() or not _is_int(initial_issued_at_ms):
            raise ValueError("offline authority parent or time invalid")
        self.path = path
        self.observer_id = observer_id
        self.key_id = key_id
        self.private_key = private_key
        self.trust_roots = dict(trust_roots)
        self.configuration = _configuration(
            observer_id=observer_id, key_id=key_id,
            private_key=private_key, trust_roots=self.trust_roots,
        )
        highwater = dict(bootstrap_highwater)
        if (set(highwater) != set(self.trust_roots)
                or any(not _is_int(value) or not 1 <= value < _MAX_GENERATION
                       for value in highwater.values())):
            raise ValueError("offline bootstrap requires exactly pinned initial counters")
        self._initialize(highwater, initial_issued_at_ms)
        self._fingerprint = self._file_identity()

    def _file_identity(self) -> tuple[int, int]:
        if self.path.is_symlink():
            raise ResearchAuthorityError("offline authority database path replaced")
        try:
            s = self.path.stat()
        except OSError as exc:
            raise ResearchAuthorityError("offline authority database unavailable") from exc
        return s.st_dev, s.st_ino

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        try:
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("PRAGMA synchronous=FULL")
            return c
        except Exception:
            c.close()
            raise

    def _checkpoint(
        self, *, generation: int, highwater: dict[str, int],
        revoked: list[str], issued_at_ms: int,
    ) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "schema": _SCHEMA,
            "observer_id": self.observer_id,
            "key_id": self.key_id,
            "generation": generation,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": issued_at_ms + _MAX_LEASE_MS,
            "highwater": highwater,
            "revoked_publishers": revoked,
            "signature": "0" * 86,
        }
        sign = self.private_key.sign(checkpoint_signing_message(doc))
        doc["signature"] = base64.urlsafe_b64encode(sign).decode().rstrip("=")
        return doc

    def _initialize(self, counters: dict[str, int], now: int) -> None:
        if now <= 0:
            raise ValueError("initial issuance time must be positive")
        try:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS observer_state ("
                        "id INTEGER PRIMARY KEY CHECK(id=1),"
                        "configuration TEXT NOT NULL,"
                        "generation INTEGER NOT NULL,"
                        "checkpoint TEXT NOT NULL)"
                    )
                    row = conn.execute(
                        "SELECT configuration FROM observer_state WHERE id=1"
                    ).fetchone()
                    if row is None:
                        genesis = self._checkpoint(
                            generation=1, highwater=counters, revoked=[], issued_at_ms=now,
                        )
                        conn.execute(
                            "INSERT INTO observer_state "
                            "(id,configuration,generation,checkpoint) VALUES(1,?,?,?)",
                            (self.configuration, 1, json.dumps(genesis, sort_keys=True)),
                        )
                    elif row[0] != self.configuration:
                        raise ResearchAuthorityError("observer trust configuration changed")
                    conn.execute("COMMIT")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise ResearchAuthorityError("offline observer database unavailable") from exc

    def latest(self) -> dict[str, Any]:
        """Read the stored signed checkpoint. NOT verified latest external state."""
        if self._file_identity() != self._fingerprint:
            raise ResearchAuthorityError("offline observer database replaced")
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT configuration, generation, checkpoint FROM observer_state WHERE id=1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise ResearchAuthorityError("offline observer state unreadable") from exc
        if not row or row[0] != self.configuration:
            raise ResearchAuthorityError("offline observer configuration unavailable")
        try:
            checkpoint = json.loads(row[2])
        except (TypeError, ValueError) as exc:
            raise ResearchAuthorityError("offline observer checkpoint malformed") from exc
        if not isinstance(checkpoint, dict) or checkpoint.get("generation") != row[1]:
            raise ResearchAuthorityError("offline observer checkpoint generation mismatch")
        return checkpoint

    def _transition(
        self, *, expected_generation: int, issued_at_ms: int,
        envelope: dict[str, Any] | None = None,
        revoke_publisher: str | None = None,
    ) -> dict[str, Any]:
        if not _is_int(expected_generation) or not 1 <= expected_generation < _MAX_GENERATION:
            raise ResearchAuthorityError("offline observer CAS generation invalid")
        if not _is_int(issued_at_ms) or issued_at_ms <= 0:
            raise ResearchAuthorityError("offline observer clock invalid")
        if (envelope is None) == (revoke_publisher is None):
            raise ResearchAuthorityError("exactly one offline transition is required")
        # Signature, expiry, pinned key/node/role/payload validation MUST happen
        # before state mutation; the SQLite CAS below is the research replay fence.
        publisher_id = None
        sequence = None
        if envelope is not None:
            try:
                receipt = SignedFleetPublisherVerifier(self.trust_roots).verify(
                    envelope, now_ms=issued_at_ms,
                )
            except PublisherEnvelopeError as exc:
                raise ResearchAuthorityError("signed publisher report rejected") from exc
            publisher_id = receipt.publisher_id
            sequence = receipt.sequence
        elif revoke_publisher not in self.trust_roots:
            raise ResearchAuthorityError("unknown publisher cannot be revoked")
        if self._file_identity() != self._fingerprint:
            raise ResearchAuthorityError("offline observer database replaced")
        try:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if self._file_identity() != self._fingerprint:
                        raise ResearchAuthorityError("offline observer database replaced")
                    row = conn.execute(
                        "SELECT configuration,generation,checkpoint "
                        "FROM observer_state WHERE id=1"
                    ).fetchone()
                    if not row or row[0] != self.configuration:
                        raise ResearchAuthorityError("observer trust configuration invalid")
                    if row[1] != expected_generation:
                        raise ResearchAuthorityError("observer generation CAS conflict")
                    old = json.loads(row[2])
                    if not isinstance(old, dict) or old.get("generation") != row[1]:
                        raise ResearchAuthorityError("observer checkpoint state malformed")
                    highwater = dict(old["highwater"])
                    revoked = list(old["revoked_publishers"])
                    if set(highwater) != set(self.trust_roots):
                        raise ResearchAuthorityError("observer highwater map mismatch")
                    if envelope is not None:
                        if publisher_id in revoked:
                            raise ResearchAuthorityError("publisher has been revoked")
                        if sequence is None or sequence <= highwater[publisher_id]:
                            raise ResearchAuthorityError("publisher sequence replay or rollback")
                        highwater[publisher_id] = sequence
                    else:
                        if revoke_publisher in revoked:
                            raise ResearchAuthorityError("publisher already revoked")
                        revoked.append(revoke_publisher)
                        revoked.sort()
                    next_doc = self._checkpoint(
                        generation=row[1] + 1, highwater=highwater, revoked=revoked,
                        issued_at_ms=issued_at_ms,
                    )
                    conn.execute(
                        "UPDATE observer_state SET generation=?,checkpoint=? WHERE id=1",
                        (next_doc["generation"], json.dumps(next_doc, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    return next_doc
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise ResearchAuthorityError("offline observer commit failed") from exc

    def publish_signed_report(
        self, envelope: dict[str, Any], *, expected_generation: int,
        issued_at_ms: int,
    ) -> dict[str, Any]:
        """Research-only operator method. NO production endpoint/authorization."""
        return self._transition(
            expected_generation=expected_generation,
            issued_at_ms=issued_at_ms, envelope=envelope,
        )

    def revoke(self, publisher_id: str, *, expected_generation: int,
               issued_at_ms: int) -> dict[str, Any]:
        """Research-only explicit operator revocation. No live key changes."""
        return self._transition(
            expected_generation=expected_generation, issued_at_ms=issued_at_ms,
            revoke_publisher=publisher_id,
        )