"""Mock-only Ed25519 Ling receipts. Never opens an inference socket or model."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

# The resident x1 system Python lacks the router's full web-service dependencies.
# Load this isolated pure-policy candidate directly so offline tests require
# only cryptography and pytest, without importing or starting the router.
import importlib.util
import sys
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "joyner_ling_receipt_candidate_test", ROOT / "src/auto_router/joyner_ling_receipt_candidate.py"
)
assert spec is not None and spec.loader is not None
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)

NOW = 1_800_000_000
DIGEST = "a" * 64


def enc(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def fixture(tmp_path):
    key = Ed25519PrivateKey.generate()
    payload = {"model": "joyner-ling", "messages": [{"role": "user", "content": "synthetic"}],
               "stream": True, "max_tokens": 64}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    claims = {
        "iss": "assistx", "aud": "joyner-ling",
        "scopes": ["inference:ling"], "model_alias": "joyner-ling",
        "max_attempts": 1, "max_output_tokens": 64,
        "iat": NOW - 1, "nbf": NOW - 1, "exp": NOW + 55,
        "principal_id": "user-synthetic-A", "task_id": "task-1",
        "claim_id": "claim-1", "agent_id": "hermes-synthetic",
        "jti": "synthetic-123", "projection_generation": 42,
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "model_sha256": DIGEST,
    }
    header = {"alg": "EdDSA", "typ": "assistx-joyner-ling+jwt", "kid": "joyner-ling-v1"}
    kwargs = {
        "public_key": key.public_key(),
        "trusted_caller_principal": "user-synthetic-A",
        "trusted_projection_generation": 42,
        "trusted_running_model_sha256": DIGEST,
        "database_path": tmp_path / "ling-receipts.sqlite",
        "now": NOW,
    }
    return key, header, claims, body, kwargs


def sign(key, header, claims):
    a = enc(json.dumps(header, sort_keys=True).encode())
    b = enc(json.dumps(claims, sort_keys=True).encode())
    return a + "." + b + "." + enc(key.sign((a + "." + b).encode()))


def submit(f, *, header=None, claims=None, body=None, updates=None):
    key, default_header, default_claims, default_body, kw = f
    kw = {**kw, **(updates or {})}
    return c.validate_and_reserve(
        sign(key, header if header is not None else default_header,
             claims if claims is not None else default_claims),
        body if body is not None else default_body, **kw
    )


def deny(reason, f, **kwargs):
    with pytest.raises(c.LingReceiptDenied, match=reason):
        submit(f, **kwargs)


def test_signed_receipt_reserved_without_authorizing_cutover(tmp_path):
    f = fixture(tmp_path)
    result = submit(f)
    assert result.verified_signature
    assert result.replay_reserved
    assert result.principal_id == "user-synthetic-A"
    assert result.projection_generation == 42
    assert not result.approved_for_proxy_cutover
    assert not result.approved_for_training
    assert (tmp_path / "ling-receipts.sqlite").stat().st_mode & 0o077 == 0
    with sqlite3.connect(f[4]["database_path"]) as conn:
        row = conn.execute("SELECT principal_id,task_id,projection_generation FROM ling_receipt").fetchone()
        assert row == ("user-synthetic-A", "task-1", 42)
        assert conn.execute("SELECT COUNT(*) FROM ling_receipt").fetchone()[0] == 1


def test_replay_rejected_across_separate_sqlite_connections(tmp_path):
    f = fixture(tmp_path)
    submit(f)
    deny("ling_receipt_replayed", f)


def test_two_concurrent_uses_only_one_success(tmp_path):
    f = fixture(tmp_path)
    def action(_i):
        try:
            submit(f)
            return "ok"
        except c.LingReceiptDenied as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(action, (0, 1)))
    assert sorted(results) == ["ling_receipt_replayed", "ok"]


@pytest.mark.parametrize("field,value,reason", [
    ("aud", "auto-router", "ling_token_scope_mismatch"),
    ("scopes", ["inference"], "ling_token_scope_mismatch"),
    ("model_alias", "auto/code", "ling_token_scope_mismatch"),
    ("max_attempts", 2, "ling_token_scope_mismatch"),
    ("max_attempts", True, "ling_token_scope_mismatch"),
    ("model_sha256", "b" * 64, "running_model_digest_mismatch"),
    ("principal_id", "user-other", "caller_principal_mismatch"),
    ("projection_generation", 41, "stale_or_incorrect_projection_generation"),
    ("iat", NOW + 60, "ling_token_expired_or_outside_window"),
    ("exp", NOW - 1, "ling_token_expired_or_outside_window"),
    ("exp", NOW + 500, "ling_token_expired_or_outside_window"),
    ("max_output_tokens", 32, "signed_output_budget_exceeded"),
    ("jti", "", "missing_or_invalid_ling_identity"),
])
def test_signed_wrong_claims_rejected(tmp_path, field, value, reason):
    f = fixture(tmp_path)
    claims = {**f[2], field: value}
    deny(reason, f, claims=claims)
    assert not f[4]["database_path"].exists()


def test_signed_request_cannot_be_reused_for_other_prompt_or_model(tmp_path):
    f = fixture(tmp_path)
    changed = f[3].replace(b"synthetic", b"realprompt")
    deny("signed_request_bytes_mismatch", f, body=changed)
    changed = f[3].replace(b"joyner-ling", b"toolcall-v5")
    deny("signed_request_bytes_mismatch", f, body=changed)


def test_signed_integer_stream_flag_rejected(tmp_path):
    f = fixture(tmp_path)
    payload = json.loads(f[3])
    payload["stream"] = 1  # Python 1 == True must not count as JSON true.
    body = json.dumps(payload, separators=(",", ":")).encode()
    claims = {**f[2], "request_sha256": hashlib.sha256(body).hexdigest()}
    deny("ling_request_model_or_stream_invalid", f, claims=claims, body=body)


def test_signed_wrong_model_payload_rejected(tmp_path):
    f = fixture(tmp_path)
    body = f[3].replace(b"joyner-ling", b"toolcall-v5")
    claims = {**f[2], "request_sha256": hashlib.sha256(body).hexdigest()}
    deny("ling_request_model_or_stream_invalid", f, claims=claims, body=body)


def test_untrusted_caller_or_model_digest_denied(tmp_path):
    f = fixture(tmp_path)
    deny("caller_principal_mismatch", f,
         updates={"trusted_caller_principal": "user-other"})
    deny("running_model_digest_mismatch", f,
         updates={"trusted_running_model_sha256": "b" * 64})
    deny("trusted_running_model_digest_missing", f,
         updates={"trusted_running_model_sha256": ""})


def test_wrong_jwt_type_and_key_rejected(tmp_path):
    f = fixture(tmp_path)
    deny("ling_token_domain_mismatch", f,
         header={**f[1], "typ": "assistx-executor+jwt"})
    deny("ling_token_domain_mismatch", f,
         header={**f[1], "alg": "HS256"})
    other = Ed25519PrivateKey.generate()
    with pytest.raises(c.LingReceiptDenied, match="invalid_ling_signature"):
        c.validate_and_reserve(
            sign(other, f[1], f[2]), f[3], **f[4]
        )


def test_unsigned_malformed_and_duplicate_json_rejected(tmp_path):
    f = fixture(tmp_path)
    for token in ("", "abc.def", "a.b.c.d", "%%%..%%%", "a" * 10000):
        with pytest.raises(c.LingReceiptDenied):
            c.validate_and_reserve(token, f[3], **f[4])
    dup_payload = b'{"model":"joyner-ling","model":"toolcall","stream":true,"max_tokens":64,"messages":[{"role":"user","content":"x"}]}'
    claims = {**f[2], "request_sha256": hashlib.sha256(dup_payload).hexdigest()}
    deny("invalid_ling_request_json", f, claims=claims, body=dup_payload)


def test_receipt_highwater_fence_prevents_generation_regression(tmp_path):
    f = fixture(tmp_path)
    submit(f)
    newer = {**f[2], "projection_generation": 43, "jti": "synthetic-new"}
    submit(f, claims=newer, updates={"trusted_projection_generation": 43})
    older = {**f[2], "jti": "synthetic-old"}
    deny("projection_generation_regressed", f, claims=older)
    with sqlite3.connect(f[4]["database_path"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ling_receipt").fetchone()[0] == 2


def test_missing_receipt_storage_or_insecure_directory_denied(tmp_path):
    f = fixture(tmp_path)
    deny("durable_receipt_directory_unavailable", f,
         updates={"database_path": tmp_path / "absent" / "state.sqlite"})
    public_dir = tmp_path / "public"
    public_dir.mkdir(mode=0o755)
    public_dir.chmod(0o755)
    deny("durable_receipt_directory_not_private", f,
         updates={"database_path": public_dir / "receipts.sqlite"})


def test_existing_world_readable_db_denied(tmp_path):
    f = fixture(tmp_path)
    store = f[4]["database_path"]
    store.write_text("not an acceptable receipt database")
    store.chmod(0o644)
    deny("durable_receipt_database_not_private", f)


def test_unavailable_or_corrupt_sqlite_fails_closed(tmp_path):
    f = fixture(tmp_path)
    path = f[4]["database_path"]
    path.write_bytes(b"not a sqlite database")
    path.chmod(0o600)
    deny("durable_receipt_store_unavailable", f)


def test_request_without_explicit_output_budget_denied(tmp_path):
    f = fixture(tmp_path)
    payload = {"model": "joyner-ling", "messages": [{"role": "user", "content": "x"}],
               "stream": True}
    body = json.dumps(payload).encode()
    claims = {**f[2], "request_sha256": hashlib.sha256(body).hexdigest()}
    deny("ling_output_budget_invalid", f, claims=claims, body=body)


def test_no_raw_request_body_or_token_saved_to_database(tmp_path):
    f = fixture(tmp_path)
    receipt = submit(f)
    raw = f[4]["database_path"].read_bytes()
    assert f[3] not in raw
    assert b'"messages"' not in raw
    assert b'"content"' not in raw
    assert receipt.request_sha256.encode() in raw
    assert receipt.approved_for_proxy_cutover is False


def test_first_database_creation_race_preserves_one_use(tmp_path, monkeypatch):
    """An O_EXCL loser verifies the winner's private DB, then denies replay."""
    import os

    f = fixture(tmp_path)
    target = f[4]["database_path"]
    original = os.open
    collided = []

    def another_thread_created_file(path, flags, mode=0o777):
        if str(path) == str(target) and flags & os.O_EXCL and not collided:
            fd = original(path, flags, 0o600)
            os.close(fd)
            collided.append(True)
            raise FileExistsError("simulated concurrent first creator")
        return original(path, flags, mode)

    monkeypatch.setattr(c.os, "open", another_thread_created_file)
    result = submit(f)
    assert result.replay_reserved
    assert collided == [True]
    assert target.stat().st_mode & 0o077 == 0
    deny("ling_receipt_replayed", f)


def test_creation_race_refuses_nonregular_winning_inode(tmp_path, monkeypatch):
    """A malicious symlink in an O_EXCL race must never become a receipt DB."""
    import os

    f = fixture(tmp_path)
    target = f[4]["database_path"]
    elsewhere = tmp_path / "elsewhere.sqlite"
    elsewhere.write_bytes(b"not-a-receipt")
    original = os.open
    collided = []

    def competing_symlink(path, flags, mode=0o777):
        if str(path) == str(target) and flags & os.O_EXCL and not collided:
            target.symlink_to(elsewhere)
            collided.append(True)
            raise FileExistsError("simulated path swap")
        return original(path, flags, mode)

    monkeypatch.setattr(c.os, "open", competing_symlink)
    deny("durable_receipt_database_not_private", f)
    assert collided == [True]
    assert elsewhere.read_bytes() == b"not-a-receipt"
