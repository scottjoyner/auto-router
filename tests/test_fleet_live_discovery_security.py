from __future__ import annotations

import httpx
import pytest

from auto_router import fleet_task_dispatcher as discovery


def _provider() -> dict[str, object]:
    return {
        "name": "test-provider",
        "node_id": "node-a",
        "base_url": "http://node-a:1234/v1",
        "configured_models": [],
        "model_aliases": {},
    }


def test_discovery_rejects_public_provider_hosts() -> None:
    with pytest.raises(discovery.DiscoveryConfigError, match="public or unresolved"):
        discovery._validate_base_url(
            "https://api.openai.com/v1",
            provider_name="public-provider",
        )


def test_discovery_does_not_follow_redirects(monkeypatch) -> None:
    real_client = httpx.Client
    observed_follow_redirects: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/models"})

    def client_factory(*args, **kwargs):
        observed_follow_redirects.append(bool(kwargs.get("follow_redirects")))
        return real_client(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr(discovery.httpx, "Client", client_factory)

    node = discovery._probe_provider(_provider())

    assert observed_follow_redirects == [False]
    assert node.online is False
    assert node.loaded_models == []
    assert all("http-302" in status for status in node.endpoint_status.values())
