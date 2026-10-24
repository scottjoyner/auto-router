"""Offline-only Ed25519 fleet publisher envelope verifier.

A valid receipt proves a configured signing KEY authorized for a reporter ROLE,
not physical GPU residency, actual inference, tenant ownership, or safe routing.
This module is NOT connected to the live /api/fleet/node-report endpoint.

The in-memory replay fence resets on restart. A future implementation must
supply independently anchored durable counters/key custody before acceptance.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_SCHEMA = "fleet-publisher-envelope.v1"
_DOMAIN = b"auto-router:fleet-publisher:v1\x00"
_FIELDS = frozenset({
    "schema", "publisher_id", "node_id", "role", "key_id", "sequence",
    "issued_at_ms", "expires_at_ms", "payload", "signature",
})
_ALIAS = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z", re.ASCII)
_SIGN = re.compile(r"[A-Za-z0-9_-]{86}\Z", re.ASCII)
_MAX_WIRE_BYTES = 16384
_MAX_LEASE_MS = 120000
_MAX_FUTURE_SKEW_MS = 15000
_MAX_SEQUENCE = 2**63 - 1


class PublisherEnvelopeError(ValueError):
    """Untrusted document rejected (message never contains source content)."""


@dataclass(frozen=True)
class TrustedPublisher:
    """Operator-pinned public key and publisher authorization, no secret key."""

    publisher_id: str
    node_id: str
    role: str
    key_id: str
    verify_key: Ed25519PublicKey
    allowed_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ("model_catalog", "gpu_presence"):
            raise ValueError("unsupported configured fleet publisher role")
        if not all(
            isinstance(x, str) and _ALIAS.fullmatch(x)
            for x in (self.publisher_id, self.node_id, self.key_id)
        ):
            raise ValueError("invalid configured fleet publisher identity")
        if not isinstance(self.verify_key, Ed25519PublicKey):
            raise ValueError("publisher verification key must be Ed25519")
        if any(not isinstance(x, str) or not _ALIAS.fullmatch(x) for x in self.allowed_aliases):
            raise ValueError("invalid configured alias")
        if self.role == "model_catalog" and not self.allowed_aliases:
            raise ValueError("model catalog publisher requires pinned allowed aliases")
        if self.role == "gpu_presence" and self.allowed_aliases:
            raise ValueError("presence publisher cannot be assigned model aliases")


@dataclass
class InMemoryReplayFence:
    """Test/preview only. Restart or rollback loses the high-water mark."""

    _highwater: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def consume(self, publisher_id: str, sequence: int) -> None:
        with self._lock:
            previous = self._highwater.get(publisher_id, 0)
            if sequence <= previous:
                raise PublisherEnvelopeError("publisher sequence replay or rollback")
            self._highwater[publisher_id] = sequence


class LocalSQLiteReplayFence:
    """Offline-only atomic, restart-persistent fence, NOT rollback resistant.

    This stores independent high-water values with SQLite BEGIN IMMEDIATE and
    FULL synchronous commit. It protects against ordinary process restarts and
    concurrent verifier instances sharing the same DB file. A privileged actor
    can replace or restore the database; therefore it is never production
    replay authority and can never set replay_protection_durable=True.

    The path must be an explicit absolute local file. No network/database
    environment or secret material is consulted, and callers must create an
    independent *externally anchored* high-water authority before admission.
    """

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        if not path.is_absolute() or str(path) == ":memory:":
            raise ValueError("local replay research requires an absolute SQLite path")
        if path.is_symlink():
            raise ValueError("symlink database paths are not supported")
        self.path = path
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS publisher_replay_highwater ("
                    "publisher_id TEXT PRIMARY KEY NOT NULL,"
                    "sequence INTEGER NOT NULL CHECK (sequence > 0))"
                )
        except sqlite3.Error as exc:
            raise PublisherEnvelopeError("local replay research database is unavailable") from exc
        self._fingerprint = self._file_fingerprint()

    def _file_fingerprint(self) -> tuple[int, int]:
        # Detect an on-disk replacement while this process remains alive.
        # Not a safe external checkpoint: inode/dev are resettable/cloneable.
        if self.path.is_symlink():
            raise PublisherEnvelopeError("local replay database file replaced")
        try:
            stat = self.path.stat()
        except OSError as exc:
            raise PublisherEnvelopeError("local replay database file unavailable") from exc
        return stat.st_dev, stat.st_ino

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            return conn
        except Exception:
            conn.close()
            raise

    def consume(self, publisher_id: str, sequence: int) -> None:
        if not _safe_identifier(publisher_id) or not _is_int(sequence) or not (
            1 <= sequence <= _MAX_SEQUENCE
        ):
            raise PublisherEnvelopeError("local replay fence identity or sequence rejected")
        if self._file_fingerprint() != self._fingerprint:
            raise PublisherEnvelopeError("local replay database file replaced")
        try:
            with closing(self._connect()) as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    if self._file_fingerprint() != self._fingerprint:
                        raise PublisherEnvelopeError("local replay database file replaced")
                    changed = conn.execute(
                        "INSERT INTO publisher_replay_highwater (publisher_id, sequence)"
                        " VALUES (?, ?)"
                        " ON CONFLICT(publisher_id) DO UPDATE SET sequence=excluded.sequence"
                        " WHERE excluded.sequence > publisher_replay_highwater.sequence",
                        (publisher_id, sequence),
                    )
                    if changed.rowcount != 1:
                        raise PublisherEnvelopeError("publisher sequence replay or rollback")
                    conn.execute("COMMIT")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise PublisherEnvelopeError("local replay research database is unavailable") from exc



@dataclass(frozen=True)
class PublisherDiagnosticReceipt:
    publisher_id: str
    node_id: str
    role: str
    key_id: str
    sequence: int
    signed_publisher_identity_verified: bool = True
    publisher_custody_verified: bool = False
    replay_protection_durable: bool = False
    loaded_model_attested: bool = False
    gpu_residency_verified: bool = False
    subject_owner_bound: bool = False
    production_admission: str = "NO_GO"


def _safe_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_ALIAS.fullmatch(value))


def _is_int(value: Any) -> bool:
    return type(value) is int


def signing_message(envelope: Mapping[str, Any]) -> bytes:
    """Bounded deterministic signed bytes; exclude ONLY detached signature."""
    if not isinstance(envelope, dict) or set(envelope) != _FIELDS:
        raise PublisherEnvelopeError("publisher envelope shape rejected")
    unsigned = {k: v for k, v in envelope.items() if k != "signature"}
    try:
        canonical = json.dumps(
            unsigned, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PublisherEnvelopeError("publisher envelope is not canonical JSON") from exc
    if len(canonical) > _MAX_WIRE_BYTES:
        raise PublisherEnvelopeError("publisher envelope exceeds size limit")
    return _DOMAIN + canonical


def _signature_bytes(signature: Any) -> bytes:
    if not isinstance(signature, str) or not _SIGN.fullmatch(signature):
        raise PublisherEnvelopeError("publisher signature format rejected")
    try:
        decoded = base64.b64decode(signature + "==", altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PublisherEnvelopeError("publisher signature encoding rejected") from exc
    if len(decoded) != 64 or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != signature:
        raise PublisherEnvelopeError("publisher signature not canonical")
    return decoded


class SignedFleetPublisherVerifier:
    """Verify configured reporters with a pluggable offline replay fence.

    The default is volatile. Supplying LocalSQLiteReplayFence protects normal
    restarts but NOT storage rollback or key custody. Never authorizes routing.
    """

    def __init__(
        self, trust_roots: Mapping[str, TrustedPublisher],
        *, fence: InMemoryReplayFence | LocalSQLiteReplayFence | None = None,
    ):
        self.trust_roots = dict(trust_roots)
        self.fence = fence if fence is not None else InMemoryReplayFence()
        for key, root in self.trust_roots.items():
            if key != root.publisher_id:
                raise ValueError("publisher trust root identity mismatch")
        if len({root.key_id for root in self.trust_roots.values()}) != len(self.trust_roots):
            raise ValueError("publisher key IDs must not be reused across producers")

    def verify(
        self, envelope: dict[str, Any], *, now_ms: int | None = None,
    ) -> PublisherDiagnosticReceipt:
        message = signing_message(envelope)
        if envelope["schema"] != _SCHEMA:
            raise PublisherEnvelopeError("publisher envelope schema rejected")
        for key in ("publisher_id", "node_id", "role", "key_id"):
            if not _safe_identifier(envelope[key]):
                raise PublisherEnvelopeError("publisher identity is invalid")
        root = self.trust_roots.get(envelope["publisher_id"])
        if root is None:
            raise PublisherEnvelopeError("publisher trust root not found")
        if (envelope["node_id"], envelope["role"], envelope["key_id"]) != (
            root.node_id, root.role, root.key_id,
        ):
            raise PublisherEnvelopeError("publisher node, role or key mismatch")
        seq = envelope["sequence"]
        issued = envelope["issued_at_ms"]
        expires = envelope["expires_at_ms"]
        if not (_is_int(seq) and 1 <= seq <= _MAX_SEQUENCE):
            raise PublisherEnvelopeError("publisher sequence is invalid")
        if not (_is_int(issued) and _is_int(expires) and 0 < issued < expires):
            raise PublisherEnvelopeError("publisher timestamp is invalid")
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if not _is_int(now):
            raise PublisherEnvelopeError("verifier clock is invalid")
        if (issued > now + _MAX_FUTURE_SKEW_MS or expires <= now
                or expires - issued > _MAX_LEASE_MS):
            raise PublisherEnvelopeError("publisher envelope expired or skewed")

        data = envelope["payload"]
        if not isinstance(data, dict):
            raise PublisherEnvelopeError("publisher payload must be an object")
        if root.role == "model_catalog":
            if set(data) != {"library", "loaded"} or data["loaded"] != []:
                raise PublisherEnvelopeError("publisher catalog must not claim model residency")
            aliases = data["library"]
            if not isinstance(aliases, list) or len(aliases) > 16 or not all(
                _safe_identifier(alias) and alias in root.allowed_aliases
                for alias in aliases
            ) or aliases != sorted(set(aliases)):
                raise PublisherEnvelopeError("publisher catalog aliases are not pinned")
        elif root.role == "gpu_presence":
            if (set(data) != {"gpu_canary_guarded", "dispatch_admitted"}
                    or type(data["gpu_canary_guarded"]) is not bool
                    or data["dispatch_admitted"] is not False):
                raise PublisherEnvelopeError("publisher GPU presence is not deny-only")

        try:
            root.verify_key.verify(_signature_bytes(envelope["signature"]), message)
        except InvalidSignature as exc:
            raise PublisherEnvelopeError("publisher Ed25519 signature mismatch") from exc

        # Only after cryptographic authorization; each publisher has a separate
        # counter. Any supplied SQLite state is still locally rollbackable.
        self.fence.consume(root.publisher_id, seq)
        return PublisherDiagnosticReceipt(
            publisher_id=root.publisher_id, node_id=root.node_id,
            role=root.role, key_id=root.key_id, sequence=seq,
        )