"""Issue #37: isolated two-human shadow-receipt contract tests.

No application middleware is installed here. The signer, user public keys,
request roots and receipts are disposable fixture state, not production custody.
The real auto-router SSE collector is exercised from the #35/#36 stack.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_router.stream_lifecycle import StreamUsageCollector


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    if not data or len(data) > 8192:
        raise ValueError("invalid delegated token segment")
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class _Principal:
    tenant: str
    subject: str


class _FixtureGateway:
    """Independent Ed25519 user authentication plus gateway-signed delegation.

    A test's caller-supplied request JSON, traceparent, IP and service token
    have NO route to the owner, root ID, attempt ID or model authority.
    """

    def __init__(self, clock=None) -> None:
        self.clock = clock or time.time
        self.signer = Ed25519PrivateKey.generate()
        self.users = {}
        self.challenges = {}
        self.sessions = {}
        self.roots = {}

    def register(self, principal: _Principal, public_key, models=("auto/local",)) -> None:
        self.users[principal] = (public_key, frozenset(models))

    def challenge(self, principal: _Principal) -> str:
        if principal not in self.users:
            raise PermissionError("unknown fixture user")
        nonce = secrets.token_urlsafe(24)
        self.challenges[nonce] = (principal, self.clock() + 30)
        return nonce

    @staticmethod
    def proof_message(principal: _Principal, nonce: str) -> bytes:
        return _json_bytes({
            "purpose": "auto-router-p0-shadow-login",
            "tenant": principal.tenant,
            "subject": principal.subject,
            "challenge": nonce,
        })

    def login(self, principal: _Principal, nonce: str, signature: bytes) -> str:
        challenge = self.challenges.pop(nonce, None)  # single use, even on failure
        if challenge is None or challenge[0] != principal or challenge[1] < self.clock():
            raise PermissionError("invalid or expired challenge")
        pub, _models = self.users[principal]
        try:
            pub.verify(signature, self.proof_message(principal, nonce))
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise PermissionError("invalid principal signature") from exc
        handle = secrets.token_urlsafe(24)
        self.sessions[handle] = (principal, self.clock() + 120)
        return handle

    def owner(self, session: str) -> _Principal:
        saved = self.sessions.get(session)
        if saved is None or saved[1] <= self.clock():
            raise PermissionError("unauthenticated/expired fixture session")
        return saved[0]

    def delegate(self, session: str, model: str, *, retry_root: str | None = None) -> str:
        principal = self.owner(session)
        if model not in self.users[principal][1]:
            raise PermissionError("model is outside authenticated user scope")
        if retry_root is not None:
            if self.roots.get(retry_root) != principal:
                raise PermissionError("root is not owned by caller")
            root = retry_root
        else:
            root = uuid.uuid4().hex
            self.roots[root] = principal
        now = int(self.clock())
        payload = {
            "iss": "disposable-shadow-gateway",
            "aud": "auto-router-shadow-only",
            "tenant": principal.tenant,
            "subject": principal.subject,
            "root_request_id": root,
            "attempt_id": uuid.uuid4().hex,
            "model_handle": model,
            "iat": now,
            "exp": now + 60,
        }
        raw = _json_bytes(payload)
        return f"{_b64(raw)}.{_b64(self.signer.sign(raw))}"

    def verify(self, token: str) -> dict[str, Any]:
        try:
            payload_part, sig_part = token.split(".")
            raw, sig = _unb64(payload_part), _unb64(sig_part)
            self.signer.public_key().verify(sig, raw)
            payload = json.loads(raw)
        except (ValueError, TypeError, InvalidSignature, json.JSONDecodeError) as exc:
            raise PermissionError("invalid test delegation") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "iss", "aud", "tenant", "subject", "root_request_id",
            "attempt_id", "model_handle", "iat", "exp",
        }:
            raise PermissionError("invalid delegation schema")
        if payload["iss"] != "disposable-shadow-gateway" or payload["aud"] != "auto-router-shadow-only":
            raise PermissionError("invalid delegation audience")
        if type(payload["iat"]) is not int or type(payload["exp"]) is not int:
            raise PermissionError("invalid delegation times")
        if payload["iat"] > self.clock() + 1 or payload["exp"] <= self.clock():
            raise PermissionError("expired or premature delegation")
        if not all(isinstance(payload[field], str) and 0 < len(payload[field]) <= 128
                   for field in ("tenant", "subject", "root_request_id", "attempt_id", "model_handle")):
            raise PermissionError("invalid delegation identifiers")
        return payload


class _ShadowReceipts:
    """Memory-only event ledger; neither outbox nor AssistX SQLite is touched."""

    def __init__(self, gateway: _FixtureGateway) -> None:
        self.gateway = gateway
        self.rows: dict[tuple[str, str, str], dict[str, Any]] = {}

    def append(
        self,
        delegation: str,
        chunks: list[bytes],
        *,
        interruption: str | None = None,
        client_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bound = self.gateway.verify(delegation)
        if interruption not in (None, "client_cancelled", "transport_exception"):
            raise ValueError("unexpected interruption type")
        collector = StreamUsageCollector()
        for chunk in chunks:
            collector.feed(chunk)
        collector.finish()
        if interruption == "client_cancelled":
            status, evidence = "cancelled", "client_cancelled"
        elif interruption == "transport_exception":
            status, evidence = "failed", "transport_exception"
        elif collector.terminal_status == "completed":
            status, evidence = "completed", collector.terminal_evidence
        elif collector.terminal_status == "failed":
            status, evidence = "incomplete", collector.terminal_evidence
        else:
            status, evidence = "incomplete", "eof_without_terminal"
        # A client trace ID is correlation input, not an identity. Keep a
        # short fingerprint only, so two adversarially reused IDs are visible.
        claimed_trace = (client_metadata or {}).get("trace_id")
        trace_fingerprint = (
            hashlib.sha256(claimed_trace.encode("utf-8")).hexdigest()[:16]
            if isinstance(claimed_trace, str) and len(claimed_trace) <= 128 else None
        )
        row = {
            "tenant_id": bound["tenant"],
            "subject_id": bound["subject"],
            "root_request_id": bound["root_request_id"],
            "attempt_id": bound["attempt_id"],
            "model_handle": bound["model_handle"],
            "producer_id": "shadow-fixture",
            "node_id": "configured-test-node",
            "instance_identity_source": "configured_route_not_runtime_attestation",
            "provider_execution_attested": False,
            "first_network_chunk_observed": bool(chunks),
            "first_token_observed": False,
            "terminal_status": status,
            "terminal_evidence": evidence,
            "acceptance_state": "completed" if status == "completed" else "possibly_accepted",
            "response_delivered": "unknown",
            "client_trace_fingerprint": trace_fingerprint,
        }
        key = (bound["tenant"], bound["subject"], bound["attempt_id"])
        previous = self.rows.get(key)
        if previous is not None and previous != row:
            raise ValueError("conflicting offline replay for attempt")
        self.rows[key] = row
        return dict(row)

    def read(self, session: str, *, root_request_id: str | None = None) -> list[dict[str, Any]]:
        owner = self.gateway.owner(session)  # never grant from trace ID or JSON actor
        return [
            dict(row) for (tenant, subject, _attempt), row in self.rows.items()
            if (tenant, subject) == (owner.tenant, owner.subject)
            and (root_request_id is None or row["root_request_id"] == root_request_id)
        ]


@pytest.fixture
def users():
    gateway = _FixtureGateway()
    # These are two separately generated test credentials, not a shared
    # service bearer impersonating two human subjects.
    alice = _Principal("tenant-shared", "alice")
    bob = _Principal("tenant-shared", "bob")
    keys = {alice: Ed25519PrivateKey.generate(), bob: Ed25519PrivateKey.generate()}
    sessions = {}
    for owner, private_key in keys.items():
        gateway.register(owner, private_key.public_key())
        challenge = gateway.challenge(owner)
        sessions[owner] = gateway.login(
            owner, challenge, private_key.sign(gateway.proof_message(owner, challenge))
        )
    return gateway, _ShadowReceipts(gateway), keys, sessions, alice, bob


@pytest.mark.asyncio
async def test_two_authenticated_users_overlap_on_same_configured_model_and_cannot_cross_read(users):
    gateway, ledger, _keys, sessions, alice, bob = users
    ready = asyncio.Event()
    arrived = []

    async def stream(owner):
        delegation = gateway.delegate(sessions[owner], "auto/local")
        arrived.append(owner)
        if len(arrived) == 2:
            ready.set()
        await ready.wait()
        await asyncio.sleep(0)  # overlapping requests to one fixture model
        return ledger.append(
            delegation,
            [b'data: {"choices":[{"delta":{"content":"private prompt response"}}]}\n\n',
             b"data: [DONE]\n\n"],
            client_metadata={
                "trace_id": "intentionally-shared-client-trace",
                "subject_id": "attacker-forged-owner",
                "model_instance_id": "spoofed-gpu",
                "claim_id": "spoofed-claim",
            },
        )

    receipt_a, receipt_b = await asyncio.gather(stream(alice), stream(bob))
    assert receipt_a["subject_id"] == "alice"
    assert receipt_b["subject_id"] == "bob"
    assert receipt_a["root_request_id"] != receipt_b["root_request_id"]
    assert receipt_a["attempt_id"] != receipt_b["attempt_id"]
    assert receipt_a["client_trace_fingerprint"] == receipt_b["client_trace_fingerprint"]
    assert receipt_a["model_handle"] == receipt_b["model_handle"] == "auto/local"
    assert receipt_a["terminal_status"] == receipt_b["terminal_status"] == "completed"
    assert receipt_a["provider_execution_attested"] is False
    assert ledger.read(sessions[alice], root_request_id=receipt_b["root_request_id"]) == []
    assert ledger.read(sessions[bob], root_request_id=receipt_a["root_request_id"]) == []
    assert len(ledger.read(sessions[alice])) == len(ledger.read(sessions[bob])) == 1
    assert "private prompt response" not in str(ledger.rows)
    assert "spoofed-gpu" not in str(ledger.rows)
    assert "spoofed-claim" not in str(ledger.rows)


@pytest.mark.parametrize("chunks,interruption,want_status,want_evidence", [
    ([b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'], None,
     "incomplete", "eof_without_terminal"),
    ([b"data: [DO", b"NE]\n\n"], None, "completed", "openai_sse_done"),
    ([b'event: response.completed\ndata: {"type":"response.completed"}\n\n'], None,
     "completed", "responses_completed"),
    ([b'data: {"type":"response.failed"}\n\n', b"data: [DONE]\n\n"], None,
     "incomplete", "responses_failed"),
    ([b'data: {"type":"response.incomplete"}\n\n'], None,
     "incomplete", "responses_incomplete"),
    ([b"data: [DONE]\n\n"], "transport_exception", "failed", "transport_exception"),
    ([b'data: {"choices":[]}\n\n'], "client_cancelled", "cancelled", "client_cancelled"),
])
def test_terminal_outcomes_never_upgrade_missing_or_failed_provider_evidence(
    users, chunks, interruption, want_status, want_evidence
):
    gateway, ledger, _keys, sessions, alice, _bob = users
    row = ledger.append(
        gateway.delegate(sessions[alice], "auto/local"),
        chunks, interruption=interruption,
    )
    assert (row["terminal_status"], row["terminal_evidence"]) == (want_status, want_evidence)
    assert row["acceptance_state"] == (
        "completed" if want_status == "completed" else "possibly_accepted"
    )
    assert row["response_delivered"] == "unknown"
    assert row["first_token_observed"] is False


def test_owner_bound_retry_and_offline_replay_cannot_reassign_or_overwrite(users):
    gateway, ledger, _keys, sessions, alice, bob = users
    first = gateway.delegate(sessions[alice], "auto/local")
    row = ledger.append(first, [b"data: [DONE]\n\n"])
    assert ledger.append(first, [b"data: [DONE]\n\n"]) == row
    with pytest.raises(ValueError, match="conflicting offline replay"):
        ledger.append(first, [b'data: {"choices":[]}\n\n'])
    with pytest.raises(PermissionError, match="root is not owned"):
        gateway.delegate(sessions[bob], "auto/local", retry_root=row["root_request_id"])
    retry = gateway.delegate(sessions[alice], "auto/local", retry_root=row["root_request_id"])
    retry_row = ledger.append(retry, [b'data: {"choices":[]}\n\n'])
    assert retry_row["root_request_id"] == row["root_request_id"]
    assert retry_row["attempt_id"] != row["attempt_id"]
    assert len(ledger.read(sessions[alice])) == 2
    assert ledger.read(sessions[bob]) == []


def test_signature_auth_is_distinct_and_cannot_be_replayed_or_forged(users):
    gateway, ledger, keys, sessions, alice, bob = users
    challenge = gateway.challenge(alice)
    bad_sig = keys[bob].sign(gateway.proof_message(alice, challenge))
    with pytest.raises(PermissionError):
        gateway.login(alice, challenge, bad_sig)
    with pytest.raises(PermissionError):
        gateway.login(alice, challenge, keys[alice].sign(gateway.proof_message(alice, challenge)))
    token = gateway.delegate(sessions[alice], "auto/local")
    raw, sig = token.split(".")
    payload = json.loads(_unb64(raw))
    payload["subject"] = "bob"
    with pytest.raises(PermissionError):
        ledger.append(_b64(_json_bytes(payload)) + "." + sig, [b"data: [DONE]\n\n"])
    with pytest.raises(PermissionError):
        gateway.delegate(sessions[bob], "unauthorized/model")
    with pytest.raises(PermissionError):
        ledger.read("fabricated-session")
    with pytest.raises(PermissionError):
        gateway.challenge(_Principal("tenant-shared", "unregistered"))


def test_expired_delegations_and_sessions_fail_closed():
    now = [2000.0]
    gateway = _FixtureGateway(clock=lambda: now[0])
    user = _Principal("tenant-shared", "alice")
    key = Ed25519PrivateKey.generate()
    gateway.register(user, key.public_key())
    challenge = gateway.challenge(user)
    session = gateway.login(user, challenge, key.sign(gateway.proof_message(user, challenge)))
    token = gateway.delegate(session, "auto/local")
    now[0] = 2061
    with pytest.raises(PermissionError, match="expired"):
        _ShadowReceipts(gateway).append(token, [b"data: [DONE]\n\n"])
    now[0] = 2121
    with pytest.raises(PermissionError):
        gateway.owner(session)
