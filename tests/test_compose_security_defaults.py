from pathlib import Path


def test_default_router_host_bind_is_loopback_only() -> None:
    compose = (Path(__file__).parent.parent / "docker-compose.yml").read_text()
    assert '${AUTO_ROUTER_BIND:-127.0.0.1}:${AUTO_ROUTER_HOST_PORT:-8088}:8088' in compose
    assert '${AUTO_ROUTER_BIND:-0.0.0.0}:${AUTO_ROUTER_HOST_PORT:-8088}:8088' not in compose
