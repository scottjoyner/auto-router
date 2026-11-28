from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_router import security

TOKEN = "constant-time-admin-token"


class _Request:
    def __init__(self, *, admin_headers: list[str] | None = None) -> None:
        self.headers = SimpleNamespace(
            getlist=lambda name: admin_headers or [] if name == "x-admin-token" else []
        )


def test_admin_token_comparison_uses_hmac_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    original = security.hmac.compare_digest

    def spy(left: str, right: str) -> bool:
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(security.hmac, "compare_digest", spy)
    monkeypatch.setattr(
        security, "get_settings", lambda: SimpleNamespace(admin_token=TOKEN)
    )
    for name in (
        "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN",
        "AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN",
        "AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS",
        "AUTO_ROUTER_ROUTE_DECISION_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    asyncio.run(
        security.require_admin(
            _Request(admin_headers=[TOKEN]),
            x_admin_token=TOKEN,
            bearer=None,
            basic=None,
        )
    )

    assert (TOKEN, TOKEN) in calls


def test_security_module_has_no_secrets_compare_digest() -> None:
    source = (Path(security.__file__)).read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    assert "secrets.compare_digest" not in source
