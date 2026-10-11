"""Disposable restart/replay experiments for issue #37.

Only the test's tmp_path SQLite file is written. Nothing is connected to the
router's production outbox, AssistX custody, providers, or authentication.
The gateway remains the same disposable fixture across SQLite reopens.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from test_two_user_shadow_receipts import _FixtureGateway, _Principal, _ShadowReceipts


def _login(gateway: _FixtureGateway, subject: str) -> str:
    principal = _Principal("same-tenant", subject)
    key = Ed25519PrivateKey.generate()
    gateway.register(principal, key.public_key())
    challenge = gateway.challenge(principal)
    return gateway.login(
        principal, challenge, key.sign(gateway.proof_message(principal, challenge))
    )


class _DisposableSQLiteReplay:
    """Fixture-only transactional, immutable attempt records with owner-gated reads."""

    def __init__(self, path, gateway: _FixtureGateway):
        self.path = str(path)
        self.gateway = gateway
        db = sqlite3.connect(self.path, timeout=3)
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS shadow_receipt (
                    attempt_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    root_request_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)
            db.commit()
        finally:
            db.close()

    def accept(self, delegation: str, chunks: list[bytes], *, client_metadata=None):
        # Only independently signed gateway data can mint an identity and attempt.
        row = _ShadowReceipts(self.gateway).append(
            delegation, chunks, client_metadata=client_metadata
        )
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
        db = sqlite3.connect(self.path, timeout=3)
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT payload FROM shadow_receipt WHERE attempt_id = ?",
                (row["attempt_id"],),
            ).fetchone()
            if previous is not None:
                if previous[0] != canonical:
                    raise ValueError("conflicting replay for immutable attempt")
            else:
                db.execute(
                    """INSERT INTO shadow_receipt
                       (attempt_id, tenant_id, subject_id, root_request_id, payload)
                       VALUES (?, ?, ?, ?, ?)""",
                    (row["attempt_id"], row["tenant_id"], row["subject_id"],
                     row["root_request_id"], canonical),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return row

    def read(self, session: str, *, root_request_id: str | None = None):
        principal = self.gateway.owner(session)
        db = sqlite3.connect(self.path, timeout=3)
        try:
            if root_request_id is None:
                results = db.execute(
                    """SELECT payload FROM shadow_receipt
                       WHERE tenant_id = ? AND subject_id = ? ORDER BY rowid""",
                    (principal.tenant, principal.subject),
                ).fetchall()
            else:
                results = db.execute(
                    """SELECT payload FROM shadow_receipt
                       WHERE tenant_id = ? AND subject_id = ?
                         AND root_request_id = ? ORDER BY rowid""",
                    (principal.tenant, principal.subject, root_request_id),
                ).fetchall()
            return [json.loads(item[0]) for item in results]
        finally:
            db.close()


def test_reopen_preserves_owner_isolation_with_colliding_trace_ids(tmp_path):
    gateway = _FixtureGateway()
    alice = _login(gateway, "alice")
    bob = _login(gateway, "bob")
    path = tmp_path / "disposable-shadow.db"
    spool = _DisposableSQLiteReplay(path, gateway)
    a = gateway.delegate(alice, "auto/local")
    b = gateway.delegate(bob, "auto/local")
    metadata = {"trace_id": "deliberate-collision", "subject_id": "spoofed"}
    a_row = spool.accept(a, [b"data: [DONE]\n\n"], client_metadata=metadata)
    b_row = spool.accept(b, [b"data: [DONE]\n\n"], client_metadata=metadata)
    reopened = _DisposableSQLiteReplay(path, gateway)
    assert a_row["client_trace_fingerprint"] == b_row["client_trace_fingerprint"]
    assert a_row["root_request_id"] != b_row["root_request_id"]
    assert reopened.read(alice) == [a_row]
    assert reopened.read(bob) == [b_row]
    assert reopened.read(alice, root_request_id=b_row["root_request_id"]) == []
    assert reopened.read(bob, root_request_id=a_row["root_request_id"]) == []
    assert reopened.accept(a, [b"data: [DONE]\n\n"], client_metadata=metadata) == a_row
    assert len(reopened.read(alice)) == 1


def test_reopen_rejects_changed_attempt_without_overwriting_original(tmp_path):
    gateway = _FixtureGateway()
    alice = _login(gateway, "alice")
    path = tmp_path / "immutable-shadow.db"
    signed_attempt = gateway.delegate(alice, "auto/local")
    row = _DisposableSQLiteReplay(path, gateway).accept(
        signed_attempt, [b"data: [DONE]\n\n"]
    )
    reopened = _DisposableSQLiteReplay(path, gateway)
    with pytest.raises(ValueError, match="conflicting replay"):
        reopened.accept(signed_attempt, [b'data: {"choices":[]}\n\n'])
    assert _DisposableSQLiteReplay(path, gateway).read(alice) == [row]


def test_expired_delegation_cannot_append_after_reopen_but_prior_row_is_readable(tmp_path):
    clock = [1000.0]
    gateway = _FixtureGateway(clock=lambda: clock[0])
    alice = _login(gateway, "alice")
    bob = _login(gateway, "bob")
    path = tmp_path / "expiry-shadow.db"
    delegation = gateway.delegate(alice, "auto/local")
    receipt = _DisposableSQLiteReplay(path, gateway).accept(
        delegation, [b'data: {"choices":[]}\n\n']
    )
    assert receipt["terminal_status"] == "incomplete"
    assert receipt["response_delivered"] == "unknown"
    assert receipt["provider_execution_attested"] is False
    clock[0] = 1061.0
    reopened = _DisposableSQLiteReplay(path, gateway)
    with pytest.raises(PermissionError, match="expired"):
        reopened.accept(delegation, [b"data: [DONE]\n\n"])
    assert reopened.read(alice) == [receipt]
    assert reopened.read(bob) == []
    with pytest.raises(PermissionError, match="unauthenticated"):
        reopened.read("spoofed-session")
