"""Offline independent-observer checkpoint comparison, never routing authority.

A correctly signed observation is useful only if the *latest* generation is
furnished by a separately controlled, authenticated authority. This module
does not fetch/checkpoint/publish evidence and does not control any trust root.
It rejects a local SQLite rollback relative to that externally pinned view,
but cannot detect simultaneous rollback of the local DB and the generation
anchor. No production deployment or GPU model admission is authorized.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_SCHEMA = "fleet-independent-checkpoint.v1"
_DOMAIN = b"auto-router:fleet-independent-checkpoint:v1\x00"
_FIELDS = frozenset({
    "schema", "observer_id", "key_id", "generation", "issued_at_ms",
    "expires_at_ms", "highwater", "revoked_publishers", "signature",
})
_ALIAS = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z", re.ASCII)
_SIGN = re.compile(r"[A-Za-z0-9_-]{86}\Z", re.ASCII)
_MAX_BYTES = 8192
_MAX_LEASE_MS = 120000
_MAX_FUTURE_SKEW_MS = 15000
_MAX_SEQ = 2**63 - 1


class ObserverCheckpointError(ValueError):
    """Fail-closed reason with no arbitrary user-provided text embedded."""


def _name(x: Any) -> bool:
    return isinstance(x, str) and bool(_ALIAS.fullmatch(x))


def _integer(x: Any) -> bool:
    return type(x) is int


@dataclass(frozen=True)
class TrustedObserver:
    observer_id: str
    key_id: str
    verify_key: Ed25519PublicKey
    publisher_ids: tuple[str, ...]
    publisher_keys: tuple[Ed25519PublicKey, ...] = ()

    def __post_init__(self) -> None:
        if not _name(self.observer_id) or not _name(self.key_id):
            raise ValueError("invalid pinned independent observer identity")
        if not isinstance(self.verify_key, Ed25519PublicKey):
            raise ValueError("independent observer requires an Ed25519 public key")
        if not self.publisher_ids or len(self.publisher_ids) > 16:
            raise ValueError("independent observer requires pinned publisher identities")
        if (any(not _name(x) for x in self.publisher_ids)
                or len(set(self.publisher_ids)) != len(self.publisher_ids)):
            raise ValueError("invalid independent observer publisher identities")
        observer_bytes = self.verify_key.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        )
        for publisher_key in self.publisher_keys:
            if not isinstance(publisher_key, Ed25519PublicKey):
                raise ValueError("invalid pinned publisher verification key")
            if observer_bytes == publisher_key.public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
            ):
                raise ValueError("observer key cannot reuse a publisher signing role")


def checkpoint_signing_message(checkpoint: Mapping[str, Any]) -> bytes:
    if not isinstance(checkpoint, dict) or set(checkpoint) != _FIELDS:
        raise ObserverCheckpointError("checkpoint field set rejected")
    try:
        unsigned = {key: value for key, value in checkpoint.items() if key != "signature"}
        blob = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ensure_ascii=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ObserverCheckpointError("checkpoint canonical JSON rejected") from exc
    if len(blob) > _MAX_BYTES:
        raise ObserverCheckpointError("checkpoint exceeds size limit")
    return _DOMAIN + blob


def _signature(value: Any) -> bytes:
    if not isinstance(value, str) or not _SIGN.fullmatch(value):
        raise ObserverCheckpointError("checkpoint signature format rejected")
    try:
        raw = base64.b64decode(value + "==", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ObserverCheckpointError("checkpoint signature encoding rejected") from exc
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise ObserverCheckpointError("checkpoint signature not canonical")
    return raw


@dataclass(frozen=True)
class IndependentCheckpointAssessment:
    observer_signature_valid: bool
    local_state_matches_checkpoint: bool
    generation_matches_external_anchor: bool
    publisher_custody_verified: bool = False
    external_authority_custody_verified: bool = False
    externally_anchored_latest_verified: bool = False
    live_replay_protection_verified: bool = False
    gpu_residency_verified: bool = False
    production_admission: str = "NO_GO"


def snapshot_local_highwater(path: Path) -> dict[str, int]:
    """Read-only one-shot local snapshot. No CREATE, INSERT, UPDATE or migrations."""
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ObserverCheckpointError("local checkpoint comparison database missing")
    try:
        # Opening in 'ro' mode prevents SQLite from creating a missing database.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            rows = conn.execute(
                "SELECT publisher_id, sequence FROM publisher_replay_highwater"
            ).fetchall()
            conn.execute("COMMIT")
    except sqlite3.Error as exc:
        raise ObserverCheckpointError("local checkpoint comparison database invalid") from exc
    if len(rows) > 16:
        raise ObserverCheckpointError("local checkpoint has too many publishers")
    result: dict[str, int] = {}
    for publisher, sequence in rows:
        if not _name(publisher) or not _integer(sequence) or not 1 <= sequence <= _MAX_SEQ:
            raise ObserverCheckpointError("local checkpoint value invalid")
        if publisher in result:
            raise ObserverCheckpointError("local checkpoint duplicate publisher")
        result[publisher] = sequence
    return result


def compare_latest_checkpoint(
    checkpoint: dict[str, Any] | None,
    *,
    trusted_observer: TrustedObserver,
    externally_pinned_generation: int | None,
    local_database: Path,
    now_ms: int,
) -> IndependentCheckpointAssessment:
    """Compare only against separately pinned *latest* observer generation.

    The caller MUST obtain externally_pinned_generation and this checkpoint
    from a trustworthy independently operated service. This code cannot prove
    their provenance or atomic freshness; it never returns production GO.
    """
    if checkpoint is None:
        raise ObserverCheckpointError("independent checkpoint unavailable")
    signing_bytes = checkpoint_signing_message(checkpoint)
    if checkpoint["schema"] != _SCHEMA:
        raise ObserverCheckpointError("independent checkpoint schema rejected")
    if checkpoint["observer_id"] != trusted_observer.observer_id:
        raise ObserverCheckpointError("independent observer identity rejected")
    if checkpoint["key_id"] != trusted_observer.key_id:
        raise ObserverCheckpointError("independent observer key rejected")
    generation = checkpoint["generation"]
    if not (_integer(generation) and 1 <= generation <= _MAX_SEQ):
        raise ObserverCheckpointError("independent checkpoint generation invalid")
    if not (_integer(externally_pinned_generation) and
            generation == externally_pinned_generation):
        raise ObserverCheckpointError("checkpoint generation differs from external anchor")
    issued = checkpoint["issued_at_ms"]
    expires = checkpoint["expires_at_ms"]
    if not (_integer(now_ms) and _integer(issued) and _integer(expires)):
        raise ObserverCheckpointError("independent checkpoint timestamps invalid")
    if not (0 < issued < expires and expires > now_ms and
            issued <= now_ms + _MAX_FUTURE_SKEW_MS and
            expires - issued <= _MAX_LEASE_MS):
        raise ObserverCheckpointError("independent checkpoint expired or skewed")
    highwater = checkpoint["highwater"]
    if (not isinstance(highwater, dict) or
            set(highwater) != set(trusted_observer.publisher_ids) or
            any(not _integer(value) or value < 1 or value > _MAX_SEQ
                for value in highwater.values())):
        raise ObserverCheckpointError("independent checkpoint publisher map rejected")
    revoked = checkpoint["revoked_publishers"]
    if (not isinstance(revoked, list) or
            revoked != sorted(set(revoked)) or
            any(not _name(name) or name not in trusted_observer.publisher_ids
                for name in revoked)):
        raise ObserverCheckpointError("independent checkpoint revocation set rejected")
    if revoked:
        # This research verifier does not implement revocation-driven admission.
        raise ObserverCheckpointError("independent checkpoint includes revoked publisher")
    try:
        trusted_observer.verify_key.verify(_signature(checkpoint["signature"]), signing_bytes)
    except InvalidSignature as exc:
        raise ObserverCheckpointError("independent checkpoint Ed25519 mismatch") from exc

    snapshot = snapshot_local_highwater(local_database)
    if snapshot != highwater:
        raise ObserverCheckpointError("local replay highwater differs from independent checkpoint")
    return IndependentCheckpointAssessment(
        observer_signature_valid=True,
        local_state_matches_checkpoint=True,
        generation_matches_external_anchor=True,
    )