#!/usr/bin/env python3
"""Read-only cross-control-plane recovery gate for sanitized operator observations.

Consumes ONLY a bounded, unsigned aggregate produced outside the scorer. It does
not authenticate its input or query Neo4j, containers, routers or secrets. It
cannot approve a runtime, renew leases, or emit a dispatch-capable decision.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "fleet-authority-recovery-observation-v1"
TOP = {"schema", "kind", "observed_at_utc", "router", "assistx", "neo4j"}
ROUTER = {"health_http", "anonymous_legacy_providers_http",
          "effective_providers_http", "runtime_projection_fresh",
          "runtime_projection_last_error_present", "cached_runtime_count"}
ASSISTX = {"health_http", "runtime_projection_http", "operations_ready",
           "operations_safe_to_dispatch", "mobile_runtime_count",
           "fleet_nodes_count", "inference_models_count"}
GRAPH = {"canonical_status", "canonical_generation", "approval_expires_in_minutes",
         "has_approved_by", "has_approval_id", "has_manifest_checksum",
         "admitted_runtimes", "current_admitted_runtimes",
         "admitted_loaded_models", "current_loaded_models",
         "approved_paths", "current_approved_paths",
         "approved_capacity", "current_approved_capacity"}


def exact(obj: object, fields: set[str], name: str) -> dict:
    if not isinstance(obj, dict) or set(obj) != fields:
        raise ValueError(name + " contains unrecognized or missing fields")
    return obj


def integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value < 2**32:
        raise ValueError("invalid " + name)
    return value


def boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError("invalid boolean " + name)
    return value


def validate(doc: object) -> dict:
    d = exact(doc, TOP, "root")
    if d["schema"] != SCHEMA or d["kind"] != "unsigned_operator_aggregate":
        raise ValueError("unsupported observation/authority kind")
    stamp = d["observed_at_utc"]
    if not isinstance(stamp, str) or len(stamp) != 20 or not stamp.endswith("Z"):
        raise ValueError("require UTC ISO timestamp")
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("bad UTC timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError("wrong timezone")
    router = exact(d["router"], ROUTER, "router")
    assistx = exact(d["assistx"], ASSISTX, "assistx")
    graph = exact(d["neo4j"], GRAPH, "graph")
    for source, fields in ((router, ROUTER), (assistx, ASSISTX)):
        for key in fields:
            if key.endswith("_http"):
                val = integer(source[key], key)
                if not 100 <= val <= 599:
                    raise ValueError("invalid HTTP status")
            elif key.endswith("_count"):
                integer(source[key], key)
            else:
                boolean(source[key], key)
    if graph["canonical_status"] not in ("approved", "draft", "revoked", "missing"):
        raise ValueError("unknown canonical projection status")
    integer(graph["canonical_generation"], "generation")
    expiry = graph["approval_expires_in_minutes"]
    if type(expiry) not in (float, int) or not math.isfinite(expiry):
        raise ValueError("unreliable canonical approval lifetime")
    for key in ("has_approved_by", "has_approval_id", "has_manifest_checksum"):
        boolean(graph[key], key)
    for key in GRAPH - {"canonical_status", "canonical_generation",
                        "approval_expires_in_minutes", "has_approved_by",
                        "has_approval_id", "has_manifest_checksum"}:
        integer(graph[key], key)
    for prefix in ("runtimes", "loaded_models", "paths", "capacity"):
        prefix_total = ("admitted_" if prefix in ("runtimes", "loaded_models")
                        else "approved_") + prefix
        current = ("current_admitted_" if prefix in ("runtimes", "loaded_models")
                   else "current_approved_") + prefix
        if graph[current] > graph[prefix_total]:
            raise ValueError("more current evidence than total approved evidence")
    return d


def decide(doc: object) -> dict:
    d = validate(doc)
    a, r, n = d["assistx"], d["router"], d["neo4j"]
    blockers: list[str] = []
    if n["canonical_status"] != "approved":
        blockers.append("CANONICAL_NOT_APPROVED")
    if n["approval_expires_in_minutes"] <= 0:
        blockers.append("CANONICAL_APPROVAL_EXPIRED")
    if not all(n[k] for k in ("has_approved_by", "has_approval_id", "has_manifest_checksum")):
        blockers.append("CANONICAL_PROVENANCE_INCOMPLETE")
    for field, code in (
        ("current_admitted_runtimes", "NO_FRESH_ADMITTED_RUNTIME"),
        ("current_loaded_models", "NO_FRESH_LOADED_MODEL"),
        ("current_approved_paths", "NO_FRESH_ACCESS_PATH"),
        ("current_approved_capacity", "NO_FRESH_CAPACITY_OBSERVATION"),
    ):
        if n[field] == 0:
            blockers.append(code)
    if r["health_http"] != 200 or a["health_http"] != 200:
        blockers.append("API_HEALTH_NOT_CONFIRMED")
    if r["anonymous_legacy_providers_http"] == 200:
        blockers.append("LEGACY_PROVIDER_ADMIN_ANONYMOUS_200")
    if r["effective_providers_http"] != 200:
        blockers.append("EFFECTIVE_PROVIDER_CATALOG_NOT_DEPLOYED_OR_INACCESSIBLE")
    if not r["runtime_projection_fresh"] or r["runtime_projection_last_error_present"]:
        blockers.append("ROUTER_PROJECTION_STALE_OR_ERROR")
    if a["runtime_projection_http"] != 200:
        blockers.append("ASSISTX_RUNTIME_PROJECTION_NOT_SERVING")
    if a["mobile_runtime_count"] == 0:
        blockers.append("EMPTY_MOBILE_RUNTIME_CATALOG")
    if a["fleet_nodes_count"] == 0 or a["inference_models_count"] == 0:
        blockers.append("EMPTY_ASSISTX_FLEET_OR_INFERENCE_VIEW")
    if (a["operations_safe_to_dispatch"] or a["operations_ready"]) and (
        a["runtime_projection_http"] != 200 or
        not r["runtime_projection_fresh"] or
        not n["current_admitted_runtimes"]):
        blockers.append("GENERAL_READINESS_NOT_EQUIVALENT_TO_FRESH_RUNTIME_AUTHORITY")
    return {
        "schema": "fleet-authority-recovery-decision-v1",
        "observed_at_utc": d["observed_at_utc"],
        "status": "BLOCKED" if blockers else "NO_KNOWN_BLOCKERS_BUT_UNATTESTED",
        "blockers": sorted(set(blockers)),
        "source_kind": "unsigned_operator_aggregate",
        "complete_trusted_authority": False,
        "execution_attested": False,
        "can_dispatch": False,
        "can_promote": False,
        "safe_next_step": (
            "Obtain fresh independently observed runtime/model/path/capacity evidence "
            "and operator-authorized canonical projection approval, then separately "
            "verify authenticated owner binding and atomic admission."
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("observation", type=Path)
    args = p.parse_args()
    try:
        path = args.observation
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 20_000:
            raise ValueError("observation must be a small non-symlink regular file")
        result = decide(json.loads(path.read_text()))
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print("recovery gate refused input: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
