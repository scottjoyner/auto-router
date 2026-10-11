"""Issue #37: real disposable PostgreSQL shared-owner end-to-end acceptance.

Requires *explicit* P0_37_TEST_EPHEMERAL=1 and P0_37_TEST_PG_DSN
for a dedicated loopback container at port 15437. The test creates and
deletes only uniquely named disposable databases in that isolated container.
No production Postgres, provider, gateway, NAS or real user is accessed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_p0_37_verified_user_delegation import _app, _request, _token

from auto_router.user_delegation import verify_user_delegation
from auto_router.user_root_ledger import RootOwnerConflict, RootOwnerStorageError
from auto_router.user_root_postgres import PostgresRootOwnerAuthority

psycopg = pytest.importorskip("psycopg", reason="optional owner-postgres driver")


def _claims(signer, subject, *, root=None, attempt=None, jti=None, model="auto/code"):
    changes = {"jti": jti} if jti else None
    token = _token(
        signer, subject, root=root, attempt=attempt,
        claims_changes=changes, model=model,
    )
    return (
        verify_user_delegation(
            token, public_key=signer.public_key(),
            key_id="fixture-human-key-1", model=model,
        ),
        token,
    )


@pytest.fixture
def disposable_pg():
    if os.environ.get("P0_37_TEST_EPHEMERAL") != "1":
        pytest.skip("disposable PostgreSQL opt-in was not enabled")
    raw = os.environ.get("P0_37_TEST_PG_DSN", "")
    if not raw:
        pytest.skip("no disposable PostgreSQL DSN supplied")
    try:
        config = psycopg.conninfo.conninfo_to_dict(raw)
    except Exception:
        pytest.skip("invalid disposable PostgreSQL connection configuration")
    # Deny any remote or default production database: tests create databases.
    if config.get("host") != "127.0.0.1" or config.get("port") != "15437":
        pytest.fail("P0 tests refuse non-loopback/non-disposable PostgreSQL")
    if config.get("dbname") != "postgres":
        pytest.fail("P0 tests require a disposable administrative database")
    database = "p0_37_exp_" + uuid.uuid4().hex[:20]
    admin = psycopg.connect(raw, autocommit=True, connect_timeout=3)
    try:
        admin.execute("CREATE DATABASE " + database)
        sandbox_dsn = psycopg.conninfo.make_conninfo(raw, dbname=database)
        yield sandbox_dsn
    finally:
        admin.execute("DROP DATABASE IF EXISTS " + database + " WITH (FORCE)")
        admin.close()


@pytest.fixture
def authority(disposable_pg):
    owner_id = "p0-disposable-" + uuid.uuid4().hex[:16]
    secret = b"\xab" * 32
    instance = PostgresRootOwnerAuthority(
        disposable_pg, secret, owner_id, bootstrap=True, require_tls=False
    )
    return instance


def _clone(authority, *, secret=None, authority_id=None):
    return PostgresRootOwnerAuthority(
        authority._dsn,
        authority._secret if secret is None else secret,
        authority.authority_id if authority_id is None else authority_id,
        require_tls=False,
    )


def test_shared_primary_rejects_cross_owner_after_authority_reopen(authority):
    signer = Ed25519PrivateKey.generate()
    root = str(uuid.uuid4())
    alice, signed = _claims(signer, "alice", root=root)
    bob, conflict = _claims(signer, "bob", root=root)
    assert authority.reserve(alice, signed) is True
    restarted = _clone(authority)
    with pytest.raises(RootOwnerConflict, match="root owner"):
        restarted.reserve(bob, conflict)
    assert restarted.reserve(alice, signed) is False
    alice_retry, retry = _claims(signer, "alice", root=root)
    assert restarted.reserve(alice_retry, retry) is True


def test_two_independent_postgres_connections_race_on_one_root(authority):
    signer = Ed25519PrivateKey.generate()
    root = str(uuid.uuid4())
    identities = [
        _claims(signer, "alice", root=root),
        _claims(signer, "bob", root=root),
    ]
    rendezvous = Barrier(2)

    def write(item):
        other = _clone(authority)
        rendezvous.wait(timeout=5)
        try:
            return other.reserve(*item)
        except RootOwnerConflict:
            return "DENIED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, identities))
    assert sorted(str(result) for result in results) == ["DENIED", "True"]


def test_concurrent_distinct_people_use_same_postgres_primary(authority):
    signer = Ed25519PrivateKey.generate()
    items = [_claims(signer, "alice"), _claims(signer, "bob")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: _clone(authority).reserve(*item), items))
    assert results == [True, True]


def test_attempt_or_gateway_jti_cannot_cross_owners(authority):
    signer = Ed25519PrivateKey.generate()
    attempt = str(uuid.uuid4())
    a, a_token = _claims(signer, "alice", attempt=attempt)
    b, b_token = _claims(signer, "bob", attempt=attempt)
    assert authority.reserve(a, a_token) is True
    with pytest.raises(RootOwnerConflict, match="attempt"):
        authority.reserve(b, b_token)
    jti = "shared-jti"
    a2, signed2 = _claims(signer, "alice", jti=jti)
    b2, signed3 = _claims(signer, "bob", jti=jti)
    assert authority.reserve(a2, signed2)
    with pytest.raises(RootOwnerConflict, match="jti"):
        authority.reserve(b2, signed3)


def test_wrong_key_and_authority_id_cannot_take_over_primary(authority):
    with pytest.raises(RootOwnerStorageError, match="identity"):
        _clone(authority, secret=b"\xf2" * 32)
    with pytest.raises(RootOwnerStorageError, match="identity"):
        _clone(authority, authority_id="wrong-disposable-primary")


def test_storage_contains_only_keyed_identifiers_no_plaintext(authority):
    signer = Ed25519PrivateKey.generate()
    record, token = _claims(signer, "alice")
    assert authority.reserve(record, token)
    with psycopg.connect(authority._dsn) as connection:
        roots = connection.execute(
            "SELECT root_hash, owner_hash, model_hash "
            "FROM p0_verified_user_owner_v1.root_owner"
        ).fetchall()
        attempts = connection.execute(
            "SELECT attempt_hash, root_hash, signer_jti_hash, token_hash "
            "FROM p0_verified_user_owner_v1.attempt_owner"
        ).fetchall()
    assert len(roots) == len(attempts) == 1
    assert all(len(value) == 64 for value in (*roots[0], *attempts[0]))
    rendered = str(roots + attempts)
    for raw in ("alice", "same-tenant", token, record["root_request_id"]):
        assert raw not in rendered


def test_two_actual_router_asgi_instances_share_one_bound_authority(
    authority, tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    root = str(uuid.uuid4())
    path_a, path_b = tmp_path / "router-a", tmp_path / "router-b"
    path_a.mkdir()
    path_b.mkdir()
    app_a, observed_a, _ = _app(
        path_a, monkeypatch, signer, owner_authority=authority,
    )
    app_b, observed_b, ledger_b = _app(
        path_b, monkeypatch, signer, owner_authority=_clone(authority),
    )
    signed_a = _token(signer, "alice", root=root)
    signed_b = _token(signer, "bob", root=root)
    with TestClient(app_a) as a, TestClient(app_b) as b:
        assert _request(a, signed_a, asserted_user="bob").status_code == 200
        assert _request(b, signed_b, asserted_user="alice").status_code == 409
        assert _request(b, signed_a, asserted_user="alice").status_code == 409
        new_attempt = _token(signer, "alice", root=root)
        assert _request(b, new_attempt, asserted_user="bob").status_code == 200
    assert [row["actor"]["subject"] for row in observed_a] == ["alice"]
    assert [row["actor"]["subject"] for row in observed_b] == ["alice"]
    with __import__("sqlite3").connect(ledger_b.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 1


def test_disposable_postgres_outage_denies_before_real_router_forward(
    authority, tmp_path, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    app, observed, idempotency = _app(
        tmp_path, monkeypatch, signer, owner_authority=authority,
    )
    # Only poison this fixture authority connection, never a production one.
    authority._dsn = "host=127.0.0.1 port=1 dbname=unreachable connect_timeout=1"
    with TestClient(app) as client:
        response = _request(client, _token(signer, "alice"))
    assert response.status_code == 503
    assert observed == []
    with __import__("sqlite3").connect(idempotency.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inference_request_idempotency"
        ).fetchone()[0] == 0


def test_independent_python_process_rejects_conflicting_human(
    authority, tmp_path,
):
    signer = Ed25519PrivateKey.generate()
    root = str(uuid.uuid4())
    alice, signed_a = _claims(signer, "alice", root=root)
    bob, signed_b = _claims(signer, "bob", root=root)
    assert authority.reserve(alice, signed_a)
    payload = json.dumps({"claims": bob, "token": signed_b})
    program = (
        "import json,sys\n"
        "from auto_router.user_root_postgres import PostgresRootOwnerAuthority\n"
        "from auto_router.user_root_ledger import RootOwnerConflict\n"
        "a=PostgresRootOwnerAuthority(sys.argv[1],bytes.fromhex(sys.argv[2]),"
        "sys.argv[3],require_tls=False)\n"
        "try:\n"
        " a.reserve(json.loads(sys.argv[4])['claims'],"
        "json.loads(sys.argv[4])['token'])\n"
        "except RootOwnerConflict:\n"
        " print('DENIED')\n"
        "else:\n"
        " print('UNEXPECTED_ACCEPT')\n"
    )
    proc = subprocess.run(
        [
            sys.executable, "-c", program,
            authority._dsn, authority._secret.hex(), authority.authority_id, payload,
        ],
        capture_output=True, text=True, timeout=9, check=True,
    )
    assert proc.stdout.strip() == "DENIED"
