from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_runs_as_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    users = [
        line.split(maxsplit=1)[1].strip()
        for line in dockerfile.splitlines()
        if line.startswith("USER ")
    ]
    assert users, "Dockerfile must declare an unprivileged USER"
    assert users[-1] not in {"root", "0"}


def test_committed_context_excludes_hosted_inference_lanes() -> None:
    document = yaml.safe_load((ROOT / "config" / "context.yaml").read_text(encoding="utf-8"))
    provider_ids = {
        str(item.get("provider") or "").strip().lower()
        for item in document.get("providers", [])
    }
    node_ids = {
        str(item.get("node_id") or "").strip().lower()
        for item in document.get("nodes", [])
    }
    assert provider_ids.isdisjoint({"groq", "cerebras", "openrouter"})
    assert "cerebras-wse3" not in node_ids

    urls = [
        str(service.get("url") or "")
        for section in ("services", "nodes", "providers")
        for item in document.get(section, [])
        for service in item.get("services", []) or [item]
    ]
    assert not any(url.startswith("https://") for url in urls)


def test_readme_matches_compose_dispatcher_and_documents_npu_env() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["llm-router"]["environment"]
    assert environment["AUTO_ROUTER_FLEET_DISPATCHER_ENABLED"] == "true"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "enables the in-process fleet dispatcher" in readme
    assert "has autoload/unload and the in-process fleet dispatcher disabled" not in readme
    assert "XWING_NPU_BASE_URL" in readme
