"""Synthetic only: integrated Ed25519 replay + streaming + NAS-first audit."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import secrets
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "joyner_ling_signed_stream_witness",
    ROOT / "scripts/joyner_ling_signed_stream_witness.py",
)
w = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = w
SPEC.loader.exec_module(w)


def test_signed_token_is_bound_to_exact_synthetic_body(tmp_path):
    private = Ed25519PrivateKey.generate()
    body = w.synthetic_body()
    now = int(time.time())
    token = w.prepare_signed(body, private, timestamp=now, jti=secrets.token_hex(12))
    kwargs = {
        "public_key": private.public_key(),
        "trusted_caller_principal": w.PRINCIPAL,
        "trusted_projection_generation": w.GENERATION,
        "trusted_running_model_sha256": w.LING_MODEL_SHA256,
        "database_path": tmp_path / "one-use.sqlite",
        "now": now,
    }
    receipt = w.validate_and_reserve(token, body, **kwargs)
    assert receipt.verified_signature and receipt.replay_reserved
    assert receipt.request_sha256 == hashlib.sha256(body).hexdigest()
    with pytest.raises(w.LingReceiptDenied, match="ling_receipt_replayed"):
        w.validate_and_reserve(token, body, **kwargs)
    with pytest.raises(w.LingReceiptDenied, match="signed_request_bytes_mismatch"):
        w.validate_and_reserve(token, body + b" ", **kwargs)


def test_mock_stream_signed_replay_and_anonymous_audit(tmp_path):
    def case():
        private = Ed25519PrivateKey.generate()
        body = w.synthetic_body()
        now = int(time.time())
        token = w.prepare_signed(body, private, timestamp=now, jti=secrets.token_hex(12))
        info = {"backend_calls": 0}

        def verify(signed, raw):
            receipt = w.validate_and_reserve(
                signed,
                raw,
                public_key=private.public_key(),
                trusted_caller_principal=w.PRINCIPAL,
                trusted_projection_generation=w.GENERATION,
                trusted_running_model_sha256=w.LING_MODEL_SHA256,
                database_path=tmp_path / "replay.sqlite",
                now=now,
            )
            info["reservation_verified"] = True
            info["reservation_provenance"] = {
                "synthetic_principal": receipt.principal_id,
                "jti_sha256": hashlib.sha256(receipt.jti.encode()).hexdigest(),
            }
            return receipt

        async def backend(_body, receipt):
            info["backend_calls"] += 1
            assert receipt.verified_signature
            yield b'data: {"content":"READY"}\n\n'
            yield b"data: [DONE]\n\n"

        app = w.LingShadowTransport(verify, backend)
        audit = w.Audit(tmp_path)

        async def go():
            return [
                await w.run_asgi(app, body, tk, audit, label, info)
                for label, tk in (("first", token), ("replay", token), ("anonymous", None))
            ]

        responses = asyncio.run(go())
        assert [r["http_status"] for r in responses] == [200, 401, 401]
        assert responses[0]["stream_done_marker_observed"]
        assert info["backend_calls"] == 1
        assert audit.close() == hashlib.sha256(audit.path.read_bytes()).hexdigest()
        recorded = json.loads(audit.path.read_text())
        assert len(recorded["requests"]) == 3
        assert all(x["state"] == "FINISHED" for x in recorded["requests"])
        assert [x["http_code"] for x in recorded["requests"]] == [200, 401, 401]
        assert [x["auth_verdict"] for x in recorded["requests"]] == [
            "pass",
            "denied",
            "denied",
        ]
        assert (
            recorded["requests"][0]["signed_reservation_provenance"]["synthetic_principal"]
            == w.PRINCIPAL
        )
        assert all(
            x.get("result", {}).get("backend_bytes_read", 0) == 0 for x in recorded["requests"][1:]
        )
        assert not recorded["cutover_authorized"]
        assert token not in audit.path.read_text()
        assert "READY" not in audit.path.read_text()

    case()


def test_no_backend_dispatch_when_pending_nas_fails(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    body = w.synthetic_body()
    now = int(time.time())
    token = w.prepare_signed(body, private, timestamp=now, jti=secrets.token_hex(12))
    calls = []

    def verify(_signed, _body):
        calls.append("verify")
        raise AssertionError("should not verify when NAS unavailable")

    async def backend(_body, receipt):
        calls.append("backend")
        yield b"data: [DONE]\n\n"

    app = w.LingShadowTransport(verify, backend)
    audit = w.Audit(tmp_path)
    monkeypatch.setattr(
        audit, "pending", lambda *a, **k: (_ for _ in ()).throw(OSError("NAS unavailable"))
    )
    with pytest.raises(OSError, match="NAS unavailable"):
        asyncio.run(w.run_asgi(app, body, token, audit, "blocked", {}))
    assert calls == []


def test_limits_and_trust_boundary():
    body = json.loads(w.synthetic_body())
    assert body["stream"] is True
    assert body["model"] == "joyner-ling"
    assert body["max_tokens"] <= 96
    assert w.MODEL_SERVER == ("127.0.0.1", 1236)
    assert w.MAX_STREAM_BYTES <= 128_000
    assert w.PRINCIPAL.startswith("synthetic-")
