"""Review-only verifier/custody for signed owner-bound terminal receipts.

Not deployed and not wired into serving. A trusted, independently reviewed
receipt issuer MUST attest the observed terminal outcome. This module cannot
generate those attestations and does not authorize inference, rerouting,
revocation or cross-host lease fencing.

SQLite is a local shared-filesystem transaction prototype, NOT a globally
authoritative multi-host receipt service.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

from .owner_attempt_ledger import _private_file, _private_parent, OwnerAttemptStoreError
from .owner_binding import _identity, OwnerBindingError

DOMAIN = "auto-router-terminal-receipt-v1"
STATUSES = frozenset({"completed", "failed", "cancelled", "possibly_accepted"})
_FIELDS = frozenset({
    "schema", "audience", "receipt_id", "request_id", "owner_hash",
    "claim_hash", "jti_hash", "attempt", "projection_generation",
    "model_id", "runtime_instance_id", "fence_epoch", "outcome",
    "output_sha256", "completion_tokens", "finished_epoch_s",
})
HEX = frozenset("0123456789abcdef")


class ReceiptDenied(ValueError):
    pass


class ReceiptStorageUnavailable(RuntimeError):
    pass


def digest(domain: str, values: list[str]) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(domain.encode() + b"\0" + encoded).hexdigest()


def _text(v: Any, name: str, maximum: int = 160) -> str:
    if type(v) is not str or not 1 <= len(v) <= maximum or v.strip() != v or any(
        ord(c) < 33 or c.isspace() for c in v
    ):
        raise ReceiptDenied(f"invalid {name}")
    return v


def _hex(value: Any, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in HEX for c in value):
        raise ReceiptDenied(f"invalid {name}")
    return value


def canonical(payload: Mapping[str, Any]) -> bytes:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ReceiptDenied("receipt schema fields differ")
    if payload["schema"] != 1 or payload["audience"] != DOMAIN:
        raise ReceiptDenied("unsupported receipt schema or audience")
    for name in ("receipt_id", "request_id", "model_id", "runtime_instance_id"):
        _text(payload[name], name, maximum=160)
    for name in ("owner_hash", "claim_hash", "jti_hash"):
        _hex(payload[name], name)
    if (type(payload["attempt"]) is not int or not 1 <= payload["attempt"] <= 100 or
        type(payload["projection_generation"]) is not int or payload["projection_generation"] < 1 or
        type(payload["fence_epoch"]) is not int or payload["fence_epoch"] < 1 or
        type(payload["finished_epoch_s"]) is not int or payload["finished_epoch_s"] < 1 or
        type(payload["completion_tokens"]) is not int or not 0 <= payload["completion_tokens"] <= 10_000_000):
        raise ReceiptDenied("receipt counters or fence invalid")
    if payload["outcome"] not in STATUSES:
        raise ReceiptDenied("unknown terminal outcome")
    if payload["outcome"] == "completed":
        _hex(payload["output_sha256"], "completed output digest")
        if payload["completion_tokens"] < 1:
            raise ReceiptDenied("completed output has no observed tokens")
    elif payload["output_sha256"] is not None or payload["completion_tokens"] != 0:
        raise ReceiptDenied("non-completion cannot claim verified output")
    return json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def expected_binding(*, owner: Mapping[str, Any], claim: Mapping[str, Any],
                     request_id: str, model_id: str, runtime_instance_id: str,
                     fence_epoch: int) -> dict[str, Any]:
    """Caller must supply *independently authenticated* authority and epoch."""
    try:
        issuer, subject = _identity(owner, origin="trusted gateway owner")
    except OwnerBindingError as exc:
        raise ReceiptDenied(str(exc)) from exc
    key_names=("task_id","claim_id","agent_id","projection_generation","jti","attempt")
    for key in key_names:
        if key not in claim:
            raise ReceiptDenied("trusted claim identity missing "+key)
    if type(claim["projection_generation"]) is not int or claim["projection_generation"] < 1:
        raise ReceiptDenied("trusted projection generation missing")
    if type(claim["attempt"]) is not int or not 1 <= claim["attempt"] <= 100:
        raise ReceiptDenied("trusted attempt invalid")
    if type(fence_epoch) is not int or fence_epoch < 1:
        raise ReceiptDenied("missing trusted fencing epoch")
    task=_text(claim["task_id"],"task_id")
    claim_id=_text(claim["claim_id"],"claim_id")
    agent=_text(claim["agent_id"],"agent_id")
    jti=_text(claim["jti"],"token jti",maximum=256)
    return {
        "request_id":_text(request_id,"request_id"),
        "model_id":_text(model_id,"model_id"),
        "runtime_instance_id":_text(runtime_instance_id,"runtime_instance_id"),
        "owner_hash":digest("terminal-owner-v1",[issuer,subject]),
        "claim_hash":digest("terminal-claim-v1",[task,claim_id,agent,
                    str(claim["projection_generation"])]),
        "jti_hash":digest("terminal-jti-v1",[jti]),
        "attempt":claim["attempt"],
        "projection_generation":claim["projection_generation"],
        "fence_epoch":fence_epoch,
    }


def _decode64(signature: Any) -> bytes:
    if type(signature) is not str or not 80 <= len(signature) <= 92:
        raise ReceiptDenied("receipt signature encoding invalid")
    try:
        raw=base64.b64decode(
            (signature+"="*((-len(signature))%4)).encode("ascii"),
            altchars=b"-_", validate=True,
        )
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != signature:
            raise ReceiptDenied("receipt signature encoding is noncanonical")
        return raw
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise ReceiptDenied("receipt signature base64 invalid") from exc


class SignedTerminalCustody:
    def __init__(self, path: str, *, verifying_key_pem: bytes, signer_id: str):
        if not path or not Path(path).is_absolute():
            raise ReceiptStorageUnavailable("private absolute receipt DB required")
        self.path=Path(path)
        try:
            _private_parent(self.path)
            _private_file(self.path)
        except OwnerAttemptStoreError as exc:
            raise ReceiptStorageUnavailable(str(exc)) from exc
        self.signer_id=_text(signer_id,"trusted receipt signer")
        try:
            key=serialization.load_pem_public_key(verifying_key_pem)
        except (ValueError,TypeError) as exc:
            raise ReceiptDenied("trusted receipt verification key invalid") from exc
        if not isinstance(key, Ed25519PublicKey):
            raise ReceiptDenied("trusted receipt key must be Ed25519")
        self.key=key
        try:
            with self._connect() as db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS signed_terminal_receipts (
                       request_hash TEXT PRIMARY KEY,
                       receipt_hash TEXT UNIQUE NOT NULL,
                       owner_hash TEXT NOT NULL,
                       claim_hash TEXT NOT NULL,
                       model_id TEXT NOT NULL,
                       runtime_instance_id TEXT NOT NULL,
                       fence_epoch INTEGER NOT NULL,
                       outcome TEXT NOT NULL,
                       payload BLOB NOT NULL,
                       signature TEXT NOT NULL,
                       signer_id TEXT NOT NULL,
                       recorded_epoch_s INTEGER NOT NULL
                    )
                """)
        except sqlite3.Error as exc:
            raise ReceiptStorageUnavailable("terminal receipt schema unavailable") from exc

    def _connect(self)->sqlite3.Connection:
        try:
            db=sqlite3.connect(self.path,timeout=5,isolation_level=None)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA busy_timeout=5000")
            return db
        except sqlite3.Error as exc:
            raise ReceiptStorageUnavailable("terminal receipt DB unavailable") from exc

    def accept(self, *, envelope: Mapping[str, Any], expected: Mapping[str, Any],
               now_s: int | None=None)->str:
        """Persist signed terminal observation or deny a duplicate/conflict.

        The independent authoritative claim + current epoch MUST be checked by
        the caller before passing expected. No bare request JSON is authority.
        """
        if not isinstance(envelope,Mapping) or set(envelope)!={"payload","signature","signer_id"}:
            raise ReceiptDenied("invalid signed receipt envelope")
        if envelope["signer_id"]!=self.signer_id:
            raise ReceiptDenied("untrusted receipt signer")
        payload=envelope["payload"]
        signed=canonical(payload)
        if len(signed)>4096:
            raise ReceiptDenied("oversized terminal receipt")
        signature=_decode64(envelope["signature"])
        if len(signature)!=64:
            raise ReceiptDenied("incorrect signature length")
        try:
            self.key.verify(signature,signed)
        except InvalidSignature as exc:
            raise ReceiptDenied("terminal receipt signature invalid") from exc
        for field in ("request_id","owner_hash","claim_hash","jti_hash","attempt",
                      "projection_generation","model_id","runtime_instance_id","fence_epoch"):
            if payload[field]!=expected.get(field):
                raise ReceiptDenied("terminal receipt independent authority mismatch: "+field)
        now_s=int(time.time()) if now_s is None else now_s
        if type(now_s) is not int or now_s<1 or abs(payload["finished_epoch_s"]-now_s)>300:
            raise ReceiptDenied("terminal receipt clock witness stale or future")
        receipt_hash=digest("terminal-receipt-v1",[payload["receipt_id"]])
        request_hash=digest("terminal-request-v1",[payload["request_id"]])
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                prior=db.execute(
                    "SELECT receipt_hash,payload,signature,signer_id FROM signed_terminal_receipts "
                    "WHERE request_hash=?",(request_hash,)).fetchone()
                if prior:
                    if prior!=(receipt_hash,signed,envelope["signature"],self.signer_id):
                        raise ReceiptDenied("conflicting terminal result for same request")
                    return "duplicate"
                try:
                    db.execute("""
                      INSERT INTO signed_terminal_receipts (
                        request_hash,receipt_hash,owner_hash,claim_hash,model_id,
                        runtime_instance_id,fence_epoch,outcome,payload,signature,
                        signer_id,recorded_epoch_s
                      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,(request_hash,receipt_hash,payload["owner_hash"],payload["claim_hash"],
                         payload["model_id"],payload["runtime_instance_id"],payload["fence_epoch"],
                         payload["outcome"],signed,envelope["signature"],self.signer_id,now_s))
                except sqlite3.IntegrityError as exc:
                    raise ReceiptDenied("receipt ID reused for another request") from exc
            return "recorded"
        except ReceiptDenied:
            raise
        except (sqlite3.Error,OSError) as exc:
            raise ReceiptStorageUnavailable("terminal receipt could not be committed") from exc

    def get(self, request_id:str)->dict[str,Any]|None:
        request_hash=digest("terminal-request-v1",[_text(request_id,"request_id")])
        try:
            with self._connect() as db:
                row=db.execute("SELECT payload,signature,signer_id,owner_hash,claim_hash,"
                               "model_id,runtime_instance_id,fence_epoch,outcome,receipt_hash "
                               "FROM signed_terminal_receipts WHERE request_hash=?",
                               (request_hash,)).fetchone()
                if not row:
                    return None
                payload=json.loads(row[0])
                body=canonical(payload)
                if body!=row[0] or row[2]!=self.signer_id:
                    raise ReceiptDenied("stored receipt corrupted")
                if (payload["request_id"] != request_id or
                    payload["owner_hash"] != row[3] or
                    payload["claim_hash"] != row[4] or
                    payload["model_id"] != row[5] or
                    payload["runtime_instance_id"] != row[6] or
                    payload["fence_epoch"] != row[7] or
                    payload["outcome"] != row[8] or
                    digest("terminal-receipt-v1",[payload["receipt_id"]]) != row[9]):
                    raise ReceiptDenied("stored receipt indexed fields diverged")
                try:
                    self.key.verify(_decode64(row[1]),body)
                except InvalidSignature as exc:
                    raise ReceiptDenied("stored receipt signature invalid") from exc
                return {"payload":payload,"signature":row[1],"signer_id":row[2]}
        except ReceiptDenied:
            raise
        except (sqlite3.Error,OSError,ValueError,TypeError) as exc:
            raise ReceiptStorageUnavailable("terminal receipt lookup failed") from exc
