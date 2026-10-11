"""Isolated signed-offline-replay contract for issue #37.

The gateway verifies the original delegation BEFORE capture. A separate
disposable spool signer then seals an already-observed, payload-free receipt.
A later receiver accepts only that trusted spool signature, not a stale client
delegation. All SQLite files live under pytest tmp_path. This is NOT a deployed
gateway, production spool, encrypted store or provider execution attestation.
"""
from __future__ import annotations

import base64
import json
import sqlite3

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from test_two_user_shadow_receipts import _FixtureGateway, _Principal, _ShadowReceipts


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _session(gateway, subject):
    principal = _Principal("shared-tenant", subject)
    secret = Ed25519PrivateKey.generate()
    gateway.register(principal, secret.public_key())
    nonce = gateway.challenge(principal)
    return gateway.login(
        principal, nonce, secret.sign(gateway.proof_message(principal, nonce))
    )


class _OfflineSpool:
    """Fixture-only capture; a valid delegation is needed ONLY at capture."""

    def __init__(self, path, gateway, signer):
        self.path, self.gateway, self.signer = str(path), gateway, signer
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sealed "
                "(attempt_id TEXT PRIMARY KEY, body BLOB NOT NULL, signature BLOB NOT NULL)"
            )

    def capture(self, delegation, chunks, *, client_metadata=None):
        receipt = _ShadowReceipts(self.gateway).append(
            delegation, chunks, client_metadata=client_metadata
        )
        assert receipt["provider_execution_attested"] is False
        assert receipt["response_delivered"] == "unknown"
        body = _canonical({"schema": "shadow-spool-v1", "receipt": receipt})
        # No request/response bodies are included: only bounded receipt metadata.
        if len(body) > 4096:
            raise ValueError("oversized shadow receipt")
        signature = self.signer.sign(body)
        with sqlite3.connect(self.path) as db:
            original = db.execute(
                "SELECT body, signature FROM sealed WHERE attempt_id=?",
                (receipt["attempt_id"],),
            ).fetchone()
            if original is None:
                db.execute(
                    "INSERT INTO sealed(attempt_id, body, signature) VALUES (?, ?, ?)",
                    (receipt["attempt_id"], body, signature),
                )
            elif original != (body, signature):
                raise ValueError("immutable capture conflict")
        return receipt

    def pending(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT body, signature FROM sealed ORDER BY rowid").fetchall()


class _OfflineReceiver:
    """Verifies one pinned spool public key and uses actor-gated receipt reads."""

    def __init__(self, path, gateway, spool_public_key):
        self.path, self.gateway, self.spool_public_key = (
            str(path), gateway, spool_public_key
        )
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS receipts ("
                "attempt_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, "
                "subject TEXT NOT NULL, root TEXT NOT NULL, body BLOB NOT NULL)"
            )

    def ingest(self, body, signature):
        if not isinstance(body, bytes) or len(body) > 4096 or not isinstance(signature, bytes):
            raise PermissionError("untrusted offline event")
        try:
            self.spool_public_key.verify(signature, body)
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise PermissionError("untrusted offline signer") from exc
        try:
            event = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PermissionError("invalid sealed event") from exc
        if not isinstance(event, dict) or set(event) != {"schema", "receipt"}:
            raise PermissionError("invalid sealed schema")
        row = event["receipt"]
        if event["schema"] != "shadow-spool-v1" or not isinstance(row, dict):
            raise PermissionError("invalid sealed schema")
        for field in ("tenant_id", "subject_id", "root_request_id", "attempt_id"):
            value = row.get(field)
            if not isinstance(value, str) or not 0 < len(value) <= 128:
                raise PermissionError("invalid bound receipt")
        if row.get("provider_execution_attested") is not False:
            raise PermissionError("spool cannot attest provider execution")
        if row.get("response_delivered") != "unknown":
            raise PermissionError("spool cannot attest client delivery")
        if row.get("producer_id") != "shadow-fixture":
            raise PermissionError("unknown fixture receipt source")

        with sqlite3.connect(self.path, timeout=3) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT body FROM receipts WHERE attempt_id=?", (row["attempt_id"],)
            ).fetchone()
            if previous is None:
                db.execute(
                    "INSERT INTO receipts (attempt_id, tenant, subject, root, body) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        row["attempt_id"], row["tenant_id"], row["subject_id"],
                        row["root_request_id"], body,
                    ),
                )
            elif previous[0] != body:
                raise ValueError("conflicting offline replay")
        return row

    def read(self, session, *, root_request_id=None):
        principal = self.gateway.owner(session)
        with sqlite3.connect(self.path) as db:
            if root_request_id is None:
                rows = db.execute(
                    "SELECT body FROM receipts WHERE tenant=? AND subject=? ORDER BY rowid",
                    (principal.tenant, principal.subject),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT body FROM receipts "
                    "WHERE tenant=? AND subject=? AND root=? ORDER BY rowid",
                    (principal.tenant, principal.subject, root_request_id),
                ).fetchall()
        return [json.loads(data[0])["receipt"] for data in rows]


def test_sealed_capture_replays_after_original_delegation_expired(tmp_path):
    clock = [2000.0]
    gateway = _FixtureGateway(clock=lambda: clock[0])
    alice, bob = _session(gateway, "alice"), _session(gateway, "bob")
    signer = Ed25519PrivateKey.generate()
    spool = _OfflineSpool(tmp_path / "sealed.db", gateway, signer)
    old_token = gateway.delegate(alice, "auto/local")
    captured = spool.capture(
        old_token, [b"data: [DONE]\n\n"],
        client_metadata={"trace_id": "shared", "subject_id": "bob"},
    )
    clock[0] = 2061.0
    with pytest.raises(PermissionError, match="expired"):
        spool.capture(old_token, [b"data: [DONE]\n\n"])

    receiver = _OfflineReceiver(tmp_path / "replayed.db", gateway, signer.public_key())
    for body, signature in _OfflineSpool(tmp_path / "sealed.db", gateway, signer).pending():
        assert receiver.ingest(body, signature) == captured
        assert receiver.ingest(body, signature) == captured
    assert receiver.read(alice) == [captured]
    assert receiver.read(bob) == []
    assert receiver.read(bob, root_request_id=captured["root_request_id"]) == []
    assert len(receiver.read(alice)) == 1


def test_modified_or_wrong_signer_sealed_event_rejected(tmp_path):
    gateway = _FixtureGateway()
    alice = _session(gateway, "alice")
    signer = Ed25519PrivateKey.generate()
    spool = _OfflineSpool(tmp_path / "spool.db", gateway, signer)
    spool.capture(gateway.delegate(alice, "auto/local"), [b"data: [DONE]\n\n"])
    body, signature = spool.pending()[0]
    receiver = _OfflineReceiver(tmp_path / "inbox.db", gateway, signer.public_key())
    with pytest.raises(PermissionError, match="untrusted offline signer"):
        receiver.ingest(body + b" ", signature)
    with pytest.raises(PermissionError, match="untrusted offline signer"):
        receiver.ingest(body, Ed25519PrivateKey.generate().sign(body))
    assert receiver.read(alice) == []


def test_spool_signature_cannot_upgrade_provider_or_delivery_claims(tmp_path):
    gateway = _FixtureGateway()
    alice = _session(gateway, "alice")
    signer = Ed25519PrivateKey.generate()
    spool = _OfflineSpool(tmp_path / "spool.db", gateway, signer)
    receipt = spool.capture(
        gateway.delegate(alice, "auto/local"),
        [b"data: [DONE]\n\n"],
        client_metadata={
            "provider_execution_attested": True,
            "response_delivered": True,
            "model_instance_id": "forged",
        },
    )
    assert receipt["provider_execution_attested"] is False
    receiver = _OfflineReceiver(tmp_path / "inbox.db", gateway, signer.public_key())
    body, signature = spool.pending()[0]
    assert receiver.ingest(body, signature)["response_delivered"] == "unknown"
    altered = json.loads(body)
    altered["receipt"]["provider_execution_attested"] = True
    forged_body = _canonical(altered)
    # Even if the spool signer is compromised, receiver refuses this schema.
    with pytest.raises(PermissionError, match="spool cannot attest"):
        receiver.ingest(forged_body, signer.sign(forged_body))


def test_spool_receiver_rejects_conflicting_signed_duplicate_attempt(tmp_path):
    gateway = _FixtureGateway()
    alice = _session(gateway, "alice")
    signer = Ed25519PrivateKey.generate()
    spool = _OfflineSpool(tmp_path / "spool.db", gateway, signer)
    spool.capture(gateway.delegate(alice, "auto/local"), [b"data: [DONE]\n\n"])
    body, signature = spool.pending()[0]
    receiver = _OfflineReceiver(tmp_path / "inbox.db", gateway, signer.public_key())
    original = receiver.ingest(body, signature)
    altered = json.loads(body)
    altered["receipt"]["terminal_status"] = "incomplete"
    modified_body = _canonical(altered)
    with pytest.raises(ValueError, match="conflicting offline replay"):
        receiver.ingest(modified_body, signer.sign(modified_body))
    assert receiver.read(alice) == [original]
