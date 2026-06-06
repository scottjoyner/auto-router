import asyncio
import json

from auto_router.context import ContextService, ServiceStatus
from auto_router.service_scanner import discover_tailnet_lmstudio_services, is_private_or_local_service, probe_service


def test_private_or_local_service_detection() -> None:
    assert is_private_or_local_service("http://localhost:8088/health") is True
    assert is_private_or_local_service("http://127.0.0.1:8088/health") is True
    assert is_private_or_local_service("http://deathstar-XPS-8920:7474") is True
    assert is_private_or_local_service("http://x1-370.lan:1234/v1/models") is True
    assert is_private_or_local_service("https://api.cerebras.ai/v1/models") is False


def test_external_service_probe_is_skipped_by_default() -> None:
    service = ContextService(
        service_id="cerebras.api",
        name="Cerebras API",
        url="https://api.cerebras.ai/v1",
        health_url="https://api.cerebras.ai/v1/models",
    )

    result = asyncio.run(probe_service(service, allow_external=False))

    assert result.skipped is True
    assert result.status == ServiceStatus.unknown
    assert result.reason == "external probing disabled"


def test_blocked_service_probe_is_skipped() -> None:
    service = ContextService(
        service_id="openrouter.api",
        name="OpenRouter API",
        url="https://openrouter.ai/api/v1",
        status="blocked",
    )

    result = asyncio.run(probe_service(service, allow_external=True))

    assert result.skipped is True
    assert result.status == ServiceStatus.blocked
    assert result.reason == "service is blocked in context"


def test_discover_tailnet_lmstudio_services_from_tailscale(monkeypatch) -> None:
    payload = {
        "Peer": {
            "peer1": {
                "HostName": "r2d2",
                "DNSName": "r2d2.tailcb8954.ts.net.",
                "TailscaleIPs": ["100.105.87.118"],
            }
        }
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload)

    monkeypatch.setattr("auto_router.service_scanner.subprocess.run", lambda *args, **kwargs: Result())

    services = discover_tailnet_lmstudio_services()

    assert len(services) == 1
    assert services[0].service_id == "tailnet.r2d2.lmstudio"
    assert services[0].provider == "lmstudio-r2d2"
    assert services[0].url == "http://r2d2.tailcb8954.ts.net:1234/v1"
