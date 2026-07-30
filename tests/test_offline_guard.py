from pathlib import Path

import pytest

from auto_router.offline_guard import (
    enforce_strict_offline_provider_config,
    host_is_offline_allowed,
    validate_offline_provider_config,
)


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "providers.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_rejects_enabled_public_provider(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: cerebras
    type: openai_compatible
    enabled: true
    base_url: https://api.cerebras.ai/v1
    quota_class: fast_free
""",
    )

    errors = validate_offline_provider_config(path, env={"AUTO_ROUTER_STRICT_OFFLINE": "true"})

    assert any("quota_class" in error for error in errors)
    assert any("forbidden" in error for error in errors)
    with pytest.raises(RuntimeError, match="strict offline provider validation failed"):
        enforce_strict_offline_provider_config(
            path,
            env={"AUTO_ROUTER_STRICT_OFFLINE": "true"},
        )


def test_allows_loopback_lan_and_tailscale_hosts(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: local
    type: lmstudio
    enabled: true
    base_url: http://127.0.0.1:1234/v1
    access_urls:
      - http://192.168.1.20:1234/v1
      - http://100.85.72.121:1234/v1
      - http://xwing.example.ts.net:1234/v1
      - ""
    quota_class: local
""",
    )

    assert validate_offline_provider_config(path, env={}) == []
    assert host_is_offline_allowed("192.168.1.20")
    assert host_is_offline_allowed("100.64.43.123")
    assert host_is_offline_allowed("xwing.example.ts.net")


def test_rejects_public_fallback_access_url(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: local-with-public-fallback
    type: lmstudio
    enabled: true
    base_url: http://192.168.1.20:1234/v1
    access_urls:
      - http://192.168.1.20:1234/v1
      - https://example.com/v1
    quota_class: local
""",
    )

    errors = validate_offline_provider_config(path, env={})

    assert any("example.com" in error and "forbidden" in error for error in errors)


def test_requires_runtime_identity_for_positive_capacity(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: unresolved-runtime
    type: lmstudio
    enabled: true
    base_url: http://192.168.1.20:1234/v1
    parallel_slots: 1
    quota_class: local
""",
    )

    errors = validate_offline_provider_config(path, env={})

    assert any("runtime_instance_id" in error for error in errors)


def test_ignores_disabled_public_provider(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: local
    type: lmstudio
    enabled: true
    base_url: http://localhost:1234/v1
    quota_class: local
  - name: disabled-cloud
    type: openai_compatible
    enabled: false
    base_url: https://example.com/v1
    quota_class: brokered_free
""",
    )

    assert validate_offline_provider_config(path, env={}) == []


def test_guard_can_only_be_disabled_explicitly(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
providers:
  - name: cloud
    type: openai_compatible
    enabled: true
    base_url: https://example.com/v1
    quota_class: brokered_free
""",
    )

    enforce_strict_offline_provider_config(
        path,
        env={"AUTO_ROUTER_STRICT_OFFLINE": "false"},
    )
