"""Strict read-only llama.cpp runtime evidence, never route/admission authority.

llama.cpp /props gives a claimed running model path and model alias, while
/v1/models provides an independent API label from the same server. Correlation
is stronger than a catalog listing but not proof of weight hash or runtime
identity. No inference, dynamic loading, service control or Task writes.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from urllib.parse import urlsplit

import httpx

MAX_JSON_BYTES = 256 * 1024


def _private_base(url: str) -> str:
    if not isinstance(url, str) or not url or url != url.strip():
        raise ValueError("private_runtime_url_required")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid_runtime_url") from None
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ("", "/", "/v1")
            or port is None or port <= 0):
        raise ValueError("invalid_runtime_url")
    if parsed.hostname != "localhost":
        try:
            host = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            raise ValueError("numeric_private_or_localhost_url_required") from None
        if isinstance(host, ipaddress.IPv4Address):
            permitted = (
                "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
                "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10",
            )
        else:
            permitted = ("::1/128", "fc00::/7", "fe80::/10")
        if not any(host in ipaddress.ip_network(network) for network in permitted):
            raise ValueError("numeric_private_or_localhost_url_required")
    return f"{parsed.scheme}://{parsed.netloc}"


def _read_json(client: httpx.Client, url: str) -> tuple[int, object | None]:
    try:
        with client.stream("GET", url, follow_redirects=False) as response:
            code = response.status_code
            if code != 200:
                return code, None
            raw = bytearray()
            for chunk in response.iter_bytes():
                if len(raw) + len(chunk) > MAX_JSON_BYTES:
                    return 0, None
                raw.extend(chunk)
            try:
                return 200, json.loads(bytes(raw))
            except (ValueError, UnicodeDecodeError):
                return 200, None
    except (httpx.RequestError, ValueError):
        return 0, None


def probe(base_url: str, *, expected_model: str = "", client: httpx.Client | None = None) -> dict:
    """Return sanitized evidence; no path, aliases or credentials in output."""
    root = _private_base(base_url)
    evidence = {
        "schema": "llamacpp-native-runtime-evidence-v1",
        "source": "runtime-props-and-compatible-model-id",
        "props_http": 0,
        "catalog_http": 0,
        "props_model_path_present": False,
        "props_alias_matches_catalog": False,
        "runtime_reported_not_sleeping": False,
        "runtime_reported_slots": 0,
        "reported_alias_sha256": None,
        "native_runtime_residency_candidate": False,
        "expected_model_exact_match": None if not expected_model else False,
        "chat_capability_attested": False,
        "weight_artifact_attested": False,
        "signed_runtime_admission": "NOT_CHECKED",
        "authorizes_dispatch": False,
    }
    owned = client is None
    if owned:
        client = httpx.Client(timeout=3, follow_redirects=False, trust_env=False)
    try:
        props_code, props = _read_json(client, root + "/props")
        catalog_code, catalog = _read_json(client, root + "/v1/models")
    finally:
        if owned:
            client.close()
    evidence["props_http"] = props_code
    evidence["catalog_http"] = catalog_code
    if props_code != 200 or catalog_code != 200:
        return evidence
    if not isinstance(props, dict) or not isinstance(catalog, dict):
        return evidence
    alias = props.get("model_alias")
    path = props.get("model_path")
    slots = props.get("total_slots")
    sleeping = props.get("is_sleeping")
    rows = catalog.get("data")
    if isinstance(path, str) and path.strip():
        evidence["props_model_path_present"] = True
    if type(slots) is int and 1 <= slots <= 1024:
        evidence["runtime_reported_slots"] = slots
    if sleeping is False:
        evidence["runtime_reported_not_sleeping"] = True
    if not (isinstance(alias, str) and alias.strip() and len(alias) <= 1024
            and isinstance(rows, list) and len(rows) == 1
            and isinstance(rows[0], dict)
            and isinstance(rows[0].get("id"), str)):
        return evidence
    evidence["reported_alias_sha256"] = hashlib.sha256(alias.encode()).hexdigest()
    evidence["props_alias_matches_catalog"] = alias == rows[0]["id"]
    if not (evidence["props_alias_matches_catalog"]
            and evidence["props_model_path_present"]
            and evidence["runtime_reported_not_sleeping"]
            and evidence["runtime_reported_slots"] > 0):
        return evidence
    evidence["native_runtime_residency_candidate"] = True
    if expected_model:
        evidence["expected_model_exact_match"] = expected_model == alias
    return evidence
