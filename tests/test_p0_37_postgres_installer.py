"""P0 #37: guarded runtime PostgreSQL installation (no remote DB used)."""
from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

from auto_router.user_delegation import install_user_delegation_shadow


@pytest.fixture
def configured_app(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_ROUTER_P0_VERIFIED_USER_ADMISSION_ENABLED", "true")
    monkeypatch.delenv("AUTO_ROUTER_P0_USER_DELEGATION_SHADOW_ENABLED", raising=False)
    monkeypatch.setenv("AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTO_ROUTER_INTERNAL_SERVICE_TOKEN", "fixture-only-service-bearer")
    private = Ed25519PrivateKey.generate()
    public = tmp_path / "gateway-public.pem"
    public.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public.chmod(0o600)
    monkeypatch.setenv("AUTO_ROUTER_P0_USER_DELEGATION_VERIFY_KEY_FILE", str(public))
    monkeypatch.setenv("AUTO_ROUTER_P0_USER_DELEGATION_KEY_ID", "p0-fixture-signing-key")
    secret = tmp_path / "authority-hmac.bin"
    secret.write_bytes(os.urandom(32))
    secret.chmod(0o600)
    monkeypatch.setenv("AUTO_ROUTER_P0_ROOT_OWNER_HMAC_KEY_FILE", str(secret))
    monkeypatch.setenv("AUTO_ROUTER_P0_OWNER_AUTHORITY", "postgres")
    monkeypatch.setenv("AUTO_ROUTER_P0_OWNER_PG_AUTHORITY_ID", "p0-test-authority")
    monkeypatch.delenv("AUTO_ROUTER_P0_OWNER_PG_DSN_FILE", raising=False)
    return FastAPI(), tmp_path


def test_default_off_does_not_attempt_postgresql_even_if_configured(
    configured_app, monkeypatch,
):
    app, _ = configured_app
    monkeypatch.delenv("AUTO_ROUTER_P0_VERIFIED_USER_ADMISSION_ENABLED")
    install_user_delegation_shadow(app)
    assert not getattr(app.state, "p0_user_delegation_shadow_installed", False)


def test_postgres_authority_dsn_file_must_be_explicit(
    configured_app,
):
    app, _ = configured_app
    with pytest.raises(RuntimeError, match="owner authority not ready"):
        install_user_delegation_shadow(app)
    assert not getattr(app.state, "p0_user_delegation_shadow_installed", False)


def test_insecure_dsn_file_fails_closed_before_any_connection(
    configured_app, monkeypatch,
):
    app, path = configured_app
    connection_file = path / "p0-connection.txt"
    connection_file.write_text(
        "host=127.0.0.1 dbname=postgres user=none sslmode=disable"
    )
    connection_file.chmod(0o644)
    monkeypatch.setenv("AUTO_ROUTER_P0_OWNER_PG_DSN_FILE", str(connection_file))
    with pytest.raises(RuntimeError, match="owner authority not ready"):
        install_user_delegation_shadow(app)
    assert not getattr(app.state, "p0_user_delegation_shadow_installed", False)


def test_non_verified_tls_dsn_is_denied_before_any_postgres_auth(
    configured_app, monkeypatch,
):
    app, path = configured_app
    connection_file = path / "p0-connection.txt"
    connection_file.write_text(
        "host=127.0.0.1 dbname=postgres user=none sslmode=require"
    )
    connection_file.chmod(0o600)
    monkeypatch.setenv("AUTO_ROUTER_P0_OWNER_PG_DSN_FILE", str(connection_file))
    with pytest.raises(RuntimeError, match="owner authority not ready"):
        install_user_delegation_shadow(app)
    assert not getattr(app.state, "p0_user_delegation_shadow_installed", False)


def test_invalid_owner_authority_mode_is_denied_without_fallback(
    configured_app, monkeypatch,
):
    app, _ = configured_app
    monkeypatch.setenv("AUTO_ROUTER_P0_OWNER_AUTHORITY", "replicated-local-db")
    with pytest.raises(RuntimeError, match="owner authority not ready"):
        install_user_delegation_shadow(app)
    assert not getattr(app.state, "p0_user_delegation_shadow_installed", False)
