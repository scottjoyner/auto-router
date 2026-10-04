#!/usr/bin/env python3
"""Report drift between config/providers.yaml and what the fleet actually serves.

The registry is a hand-maintained cache. Nothing used to reconcile it against the
hosts, so it went stale silently: models were renamed upstream
(`refinedtoolcallv5-3b` -> `toolcall-v5-3b-combined-r2`), versioned
(`ornith-1.0-35b` -> `ornith-1.5-35b-a3b-apex-mtp`), or unloaded entirely, and the
registry kept advertising the old name. A route to such an entry cannot succeed.

That drift is also what commit 9493bd5 worked around, by hand-trimming
providers.yaml after `model_not_found` appeared in production. Trimming treats the
symptom; this script reports the cause, and the snapshot contract that consumes it
turns the cause into a gate.

Scope, stated precisely: `auto_router.live_model_routes.refresh_provider_models`
already polls every provider's `/v1/models` and folds the result into a sqlite
registry, so after the first poll the runtime is self-correcting. `providers.yaml`
is the cold-start bootstrap. That makes drift here a cold-start and
disaster-recovery problem - a fresh router, or one restored from config, begins
routing to model names that do not exist - rather than a permanent production
fault. It is still worth gating, because the window it covers is exactly the window
nobody is watching.

A second consequence: LM Studio loads and unloads models on its own, so a
hand-pinned entry for a large model cannot be made permanently true. Observed
during development: `ornith-1.5-35b-a3b-apex-mtp` was loaded on x1-370 at 12:27
and gone by 12:36 with no operator action. Entries that churn this way belong in
the accepted-drift file with a note, or the registry should stop enumerating
runtime-loaded models at all.

Read-only. Probes each provider's OpenAI-compatible `/v1/models` endpoint and
classifies every registry entry:

    ok           the host serves this model
    MISSING       the host is up and does NOT serve it - a guaranteed routing miss
    UNREACHABLE   the host did not answer - drift unknowable, not evidence of absence

UNREACHABLE is deliberately not MISSING. A powered-off node must not be read as
"this model does not exist", or a fleet-wide outage would look like a config bug
and invite deleting live registry entries.

Usage:
    python3 scripts/provider_registry_drift.py            # human report, exit 1 on drift
    python3 scripts/provider_registry_drift.py --json     # machine-readable
    python3 scripts/provider_registry_drift.py --write-snapshot   # refresh the pinned data

Exit codes:
    0  no drift among reachable providers
    1  at least one MISSING entry (the registry is wrong)
    2  a provider could not be parsed, or the registry is unreadable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "providers.yaml"
SNAPSHOT = ROOT / "config" / "provider_registry_snapshot.json"

DEFAULT_TIMEOUT = 6.0

# `lmstudio-x1-370` addresses host.docker.internal, which only resolves inside a
# container. Everywhere else that means "this host", so map it before probing.
_LOCAL_ALIASES = {"host.docker.internal": "127.0.0.1"}
_TAILNET_SUFFIX = ".tailcb8954.ts.net"
_BARE_HOSTNAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Provider:
    name: str
    node_id: str
    base_url: str
    models: list[str]


@dataclass
class Finding:
    provider: str
    model: str
    status: str
    detail: str = ""
    served_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {"provider": self.provider, "model": self.model, "status": self.status}
        if self.detail:
            out["detail"] = self.detail
        if self.served_count is not None:
            out["served_count"] = self.served_count
        return out


@dataclass
class Report:
    providers: list[Provider] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def missing(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "MISSING"]

    @property
    def unreachable(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "UNREACHABLE"]

    @property
    def ok(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "ok"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "providers": [
                {"name": p.name, "node_id": p.node_id, "models": p.models} for p in self.providers
            ],
            "findings": [f.as_dict() for f in self.findings],
            "summary": {
                "ok": len(self.ok),
                "missing": len(self.missing),
                "unreachable": len(self.unreachable),
            },
        }


def _resolve_base_url(url: str) -> str:
    """Resolve the registry's `${VAR:-default}` interpolations.

    Env wins over the literal default, matching how docker compose and the router
    both read these values. A bare `${VAR}` with no default resolves to empty
    rather than being left in place, so a malformed URL surfaces as UNREACHABLE
    instead of being probed literally.
    """
    def with_default(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), match.group(2))

    def bare(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    resolved = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}", with_default, url)
    resolved = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", bare, resolved)
    return resolved.rstrip("/")


def _resolve_host(url: str) -> str:
    """Hostname to probe, fixing up names that only resolve in other contexts.

    `host.docker.internal` exists only inside a container, so from a script it
    means "this host". Bare tailnet names need the FQDN suffix; dotted-quad
    addresses and loopback are already final.
    """
    resolved = _resolve_base_url(url)
    authority = resolved.split("//", 1)[-1]
    hostport = authority.split("/")[0]
    host = hostport.rsplit(":", 1)[0] if ":" in hostport else hostport
    host = _LOCAL_ALIASES.get(host, host)
    if host not in ("127.0.0.1", "localhost") and not host[0].isdigit():
        if _BARE_HOSTNAME.match(host) and not host.endswith(_TAILNET_SUFFIX):
            host = f"{host}{_TAILNET_SUFFIX}"
    return host


# LM Studio serves on 1234 by convention, but it is a convention and not a
# rule: fastflowlm-x1-npu runs on 52625 and destroyer-k2 on 1235. Hard-coding
# 1234 probes the wrong port and reports a live model as MISSING, which is worse
# than not probing at all because the finding reads as authoritative.
DEFAULT_PORT = "1234"


def _endpoint(provider: Provider) -> str:
    """Scheme, host and port, all taken from the registry's own value.

    Resolve interpolations *first*: parsing the raw string splits
    `${VAR:-http://host:1234/v1}` into nonsense authority and port components,
    which is how every provider ended up UNREACHABLE once the port became
    dynamic.
    """
    resolved = _resolve_base_url(provider.base_url)
    scheme = resolved.split("://", 1)[0] if "://" in resolved else "http"
    authority = resolved.split("://", 1)[-1] if "://" in resolved else resolved
    hostport = authority.split("/")[0]
    port = DEFAULT_PORT
    if ":" in hostport:
        candidate = hostport.rsplit(":", 1)[1]
        if candidate.isdigit():
            port = candidate
    return f"{scheme}://{_resolve_host(provider.base_url)}:{port}"


def _models_endpoint(provider: Provider) -> str:
    return f"{_endpoint(provider)}/v1/models"


def fetch_served_models(provider: Provider, timeout: float = DEFAULT_TIMEOUT) -> list[str] | None:
    """Return served model ids, or None when the host does not answer."""
    url = _models_endpoint(provider)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    return [entry["id"] for entry in data if isinstance(entry, dict) and entry.get("id")]


def load_providers(registry: Path = REGISTRY) -> list[Provider]:
    document = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    providers: list[Provider] = []
    for entry in document.get("providers", []) or []:
        if not isinstance(entry, dict):
            continue
        models = [
            str(model["provider_model"])
            for model in entry.get("models", []) or []
            if isinstance(model, dict) and model.get("provider_model")
        ]
        providers.append(
            Provider(
                name=str(entry.get("name", "<unnamed>")),
                node_id=str(entry.get("node_id", "")),
                base_url=str(entry.get("base_url", "")),
                models=models,
            )
        )
    return providers


def build_report(providers: list[Provider] | None = None, timeout: float = DEFAULT_TIMEOUT) -> Report:
    report = Report(providers=providers if providers is not None else load_providers())
    for provider in report.providers:
        served = fetch_served_models(provider, timeout=timeout)
        for model in provider.models:
            if served is None:
                report.findings.append(
                    Finding(provider.name, model, "UNREACHABLE", "host did not answer /v1/models")
                )
            elif model in served:
                report.findings.append(Finding(provider.name, model, "ok", served_count=len(served)))
            else:
                report.findings.append(
                    Finding(
                        provider.name,
                        model,
                        "MISSING",
                        "registry advertises a model this host does not serve",
                        served_count=len(served),
                    )
                )
    return report


def _curl_fallback(url: str, timeout: float) -> list[str] | None:
    """Some fleet nodes resolve only inside the docker network."""
    try:
        completed = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), url],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    return [entry["id"] for entry in data if isinstance(entry, dict) and entry.get("id")]


def fetch_served_models_with_fallback(provider: Provider, timeout: float = DEFAULT_TIMEOUT) -> list[str] | None:
    served = fetch_served_models(provider, timeout=timeout)
    if served is not None:
        return served
    return _curl_fallback(_models_endpoint(provider), timeout)


def render(report: Report) -> str:
    lines = ["provider registry drift", ""]
    by_provider: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_provider.setdefault(finding.provider, []).append(finding)
    for provider in report.providers:
        entries = by_provider.get(provider.name, [])
        if not entries:
            continue
        bad = [f for f in entries if f.status == "MISSING"]
        unknown = [f for f in entries if f.status == "UNREACHABLE"]
        marker = "FAIL" if bad else ("unknown" if unknown else "ok")
        lines.append(f"  [{marker:7}] {provider.name} ({provider.node_id})")
        for finding in entries:
            if finding.status == "ok":
                suffix = f" - host serves {finding.served_count}"
                lines.append(f"      ok          {finding.model}{suffix}")
            elif finding.status == "MISSING":
                lines.append(f"      MISSING    {finding.model} - {finding.detail}")
            else:
                lines.append(f"      UNREACHABLE {finding.model} - {finding.detail}")
    lines += [
        "",
        f"  {len(report.ok)} ok, {len(report.missing)} missing, {len(report.unreachable)} unreachable",
    ]
    if report.missing:
        lines.append("")
        lines.append("  MISSING entries are guaranteed routing failures: the router asks for a")
        lines.append("  model the host does not serve. UNREACHABLE is not evidence of absence.")
    return "\n".join(lines)


def write_snapshot(report: Report, when: str) -> None:
    """Record what the reachable hosts serve, for the CI gate to compare against."""
    snapshot = {
        "recorded_at": when,
        "note": (
            "Generated by scripts/provider_registry_drift.py --write-snapshot. Only hosts that "
            "answered are recorded; a provider absent here was unreachable at recording time and "
            "is not asserted about."
        ),
        "providers": {
            provider.name: sorted(served)
            for provider, served in (
                (p, fetch_served_models_with_fallback(p)) for p in report.providers
            )
            if served is not None
        },
    }
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--write-snapshot",
        metavar="ISO8601",
        help="write config/provider_registry_snapshot.json recording reachable hosts",
    )
    args = parser.parse_args(argv)

    try:
        providers = load_providers(args.registry)
    except (OSError, yaml.YAMLError) as error:
        print(f"cannot read {args.registry}: {error}", file=sys.stderr)
        return 2

    report = Report(providers=providers)
    for provider in providers:
        served = fetch_served_models_with_fallback(provider, timeout=args.timeout)
        for model in provider.models:
            if served is None:
                report.findings.append(Finding(provider.name, model, "UNREACHABLE", "host did not answer /v1/models"))
            elif model in served:
                report.findings.append(Finding(provider.name, model, "ok", served_count=len(served)))
            else:
                report.findings.append(
                    Finding(
                        provider.name,
                        model,
                        "MISSING",
                        "registry advertises a model this host does not serve",
                        served_count=len(served),
                    )
                )

    if args.write_snapshot:
        write_snapshot(report, args.write_snapshot)
        print(f"wrote {SNAPSHOT.relative_to(ROOT)}")

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(render(report))

    return 1 if report.missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
