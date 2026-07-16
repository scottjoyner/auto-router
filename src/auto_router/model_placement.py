"""Model placement / load controller for the local LM Studio fleet.

Purpose
-------
Decide *which model is loaded on which node, when*, based on usecase and
per-machine properties (load, priority, speed, correctness). It treats the
router's live-model cache as the source of truth for what is currently loaded,
computes a desired state, then reconciles the gap.

Autoload is gated. By default the controller only *plans and audits* (reports
drift between desired and actual). Once ``AUTO_ROUTER_AUTOLOAD_ENABLED=true`` it
will call the LM Studio native load API to realize the plan. This matters
because LM Studio's autoload is currently OFF on the endpoints, and loaded
models auto-unload after a TTL (observed ``remaining_ttl_seconds: 3600``), so
something must re-pin them.

Environment
-----------
AUTO_ROUTER_AUTOLOAD_ENABLED        default false  - actually issue load calls
AUTO_ROUTER_PLACEMENT_RECONCILE_S   default 0      - background reconcile period (0=off)
AUTO_ROUTER_LOAD_TTL_SECONDS        default 0      - ttl passed on load (0 = pin / no expiry)
AUTO_ROUTER_PLACEMENT_UNLOAD        default false  - allow unloads (see UNLOAD_ALLOWED_KEYS)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from auto_router.model_registry import LiveModelSnapshot

log = logging.getLogger("auto_router.model_placement")

AUTOLOAD_ENABLED = os.getenv("AUTO_ROUTER_AUTOLOAD_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
RECONCILE_SECONDS = int(os.getenv("AUTO_ROUTER_PLACEMENT_RECONCILE_SECONDS", "0") or "0")
DEFAULT_TTL = int(os.getenv("AUTO_ROUTER_LOAD_TTL_SECONDS", "0") or "0")
UNLOAD_ENABLED = os.getenv("AUTO_ROUTER_PLACEMENT_UNLOAD", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Models we are permitted to unload when UNLOAD_ENABLED is true. Empty by
# default so the controller never evicts a model it was not told to manage.
UNLOAD_ALLOWED_KEYS = {
    k for k in os.getenv("AUTO_ROUTER_UNLOAD_KEYS", "").split(",") if k
}

# Nodes the controller may dynamically load/unload (swap models on). Others are
# "static": their models are pre-loaded / managed manually, and the background
# reconcile loop must not touch them. Expand at runtime via AUTO_ROUTER_AUTOLOAD_NODES
# (comma-separated provider names) as you reach more machines.
AUTOLOAD_NODES = {n.strip() for n in os.getenv("AUTO_ROUTER_AUTOLOAD_NODES", "").split(",") if n.strip()}


def is_swappable(provider: str) -> bool:
    if provider in AUTOLOAD_NODES:
        return True
    return bool(NODE_PROFILE.get(provider, {}).get("autoload", False))

# Per-machine profile used as a tie-breaker when the same usecase could land on
# multiple nodes. Higher = more headroom / preferred for that class.
# speed: inference speed; correctness: node reliability; cost: VRAM/RAM price of
# a load here; tier: 3=GPU large, 2=mid, 1=CPU small. 0-100 unless noted.
# `autoload` marks whether the controller may dynamically swap models on this
# node. True = swappable (managed nodes: macbook-air, beelink). False = static
# (models pre-loaded / managed manually; the background reconcile loop must not
# touch them). Override/expand at runtime via AUTO_ROUTER_AUTOLOAD_NODES.
NODE_PROFILE: dict[str, dict[str, int | bool]] = {
    "lmstudio-x1-370": {"speed": 80, "correctness": 95, "cost": 90, "tier": 3, "autoload": False},
    "lmstudio-deathstar": {"speed": 70, "correctness": 85, "cost": 70, "tier": 3, "autoload": False},
    "lmstudio-xwing": {"speed": 55, "correctness": 80, "cost": 60, "tier": 2, "autoload": False},
    "lmstudio-joyner": {"speed": 50, "correctness": 75, "cost": 50, "tier": 2, "autoload": False},
    "lmstudio-beelink-ryzen-7-mini-pc": {"speed": 35, "correctness": 60, "cost": 30, "tier": 1, "autoload": True},
    "lmstudio-scotts-macbook-air": {"speed": 25, "correctness": 60, "cost": 20, "tier": 1, "autoload": False},
    "lmstudio-macbook-air": {"speed": 25, "correctness": 60, "cost": 20, "tier": 1, "autoload": True},
    "lmstudio-lenovo-ideapad-330s-15ikb": {"speed": 20, "correctness": 55, "cost": 15, "tier": 1, "autoload": False},
    "lmstudio-optiplex-9030-aio": {"speed": 18, "correctness": 55, "cost": 15, "tier": 1, "autoload": False},
}


@dataclass
class DesiredLoad:
    """A model that should be resident (loaded) on a specific provider node."""

    provider: str
    model_key: str
    context_length: int
    usecase: str
    priority: int = 50
    ttl: int | None = None  # None -> DEFAULT_TTL


@dataclass
class LoadAction:
    provider: str
    endpoint: str
    model_key: str
    context_length: int
    ttl: int
    usecase: str
    reason: str = ""
    swappable: bool = True


@dataclass
class UnloadAction:
    provider: str
    endpoint: str
    model_key: str
    reason: str = ""


# Desired fleet state. Model keys must match each node's LM Studio catalog key.
# All nodes standardize on the "ornith" spelling for the 9B worker model.
# Context lengths are the effective KV cache size to load with.
DESIRED_PLACEMENTS: list[DesiredLoad] = [
    # --- hermes exec / heavy reasoning: x1-370 is the primary worker ---
    DesiredLoad("lmstudio-x1-370", "ornith-1.0-35b", 262144, "hermes_exec", priority=100),
    DesiredLoad("lmstudio-x1-370", "refinedtoolcallv5-3b", 131072, "tool_call", priority=90),
    # NOTE: qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled is intentionally NOT
    # placed on x1-370 -- its llama-server engine crashes (exitCode=1) on this
    # host, so loading it only trips the circuit breaker and degrades health.
    # --- deathstar: quick hermes sessions (distinct RX 480 node) ---
    DesiredLoad("lmstudio-deathstar", "refinedtoolcallv5-3b", 131072, "quick_session", priority=85),
    DesiredLoad("lmstudio-deathstar", "ornith-1.0-9b", 131072, "hermes_worker", priority=75),
    # --- xwing: secondary hermes worker (9B) ---
    DesiredLoad("lmstudio-xwing", "ornith-1.0-9b", 131072, "hermes_worker_secondary", priority=85),
    DesiredLoad("lmstudio-xwing", "refinedtoolcallv5-3b-ablated-i1", 131072, "tool_call", priority=70),
    # --- macbook air (scotts): review / summarization ---
    DesiredLoad("lmstudio-scotts-macbook-air", "liquid/lfm2.5-1.2b", 128000, "review_summarize", priority=60),
    DesiredLoad("lmstudio-scotts-macbook-air", "refinedtoolcallv5-3b", 131072, "tool_call", priority=55),
    # --- macbook air (other): review / summarization ---
    DesiredLoad("lmstudio-macbook-air", "liquid/lfm2.5-1.2b", 128000, "review_summarize", priority=55),
    # --- lenovo (intel i5/i3 CPU): background tasks + file cat ---
    DesiredLoad("lmstudio-lenovo-ideapad-330s-15ikb", "liquid/lfm2.5-1.2b", 128000, "background_filecat", priority=50),
    DesiredLoad("lmstudio-lenovo-ideapad-330s-15ikb", "vibethinker-3b-heretic-i1", 131072, "tool_call", priority=45),
    # --- beelink (ryzen, CPU-ish): background tasks ---
    DesiredLoad("lmstudio-beelink-ryzen-7-mini-pc", "liquid/lfm2.5-1.2b", 128000, "background_filecat", priority=45),
    DesiredLoad("lmstudio-beelink-ryzen-7-mini-pc", "refinedtoolcallv5-3b", 131072, "tool_call", priority=40),
    # --- optiplex (intel CPU): background tasks ---
    DesiredLoad("lmstudio-optiplex-9030-aio", "liquid/lfm2.5-1.2b", 128000, "background_filecat", priority=40),
]


def _strip_v1(url: str) -> str:
    url = (url or "").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _model_id(m: dict) -> str:
    """Canonical model identity. LM Studio's OpenAI ``/v1/models`` endpoint only
    returns *currently loaded* models, each with an ``id`` (e.g. ``liquid/lfm2.5-1.2b``)
    that matches the keys used in DESIRED_PLACEMENTS. Fall back to native fields
    (key/path) when present."""
    if not isinstance(m, dict):
        return ""
    return str(
        m.get("id")
        or (m.get("raw", {}) or {}).get("key")
        or m.get("model_key")
        or m.get("path")
        or ""
    ).strip()


def _is_loaded(m: dict) -> bool:
    """A model is resident iff LM Studio reports loaded instances. The top-level
    ``loaded`` boolean is unreliable; the authoritative signal is the
    ``raw.loaded_instances`` list (non-empty = actually running on the endpoint)."""
    if not isinstance(m, dict):
        return False
    raw = m.get("raw", {}) or {}
    instances = raw.get("loaded_instances") or m.get("loaded_instances") or []
    if isinstance(instances, list) and instances:
        return True
    return bool(m.get("loaded"))


def _loaded_keys(snapshot: LiveModelSnapshot) -> set[str]:
    keys: set[str] = set()
    for m in snapshot.models:
        if _is_loaded(m):
            mid = _model_id(m)
            if mid:
                keys.add(mid)
    return keys


def gather_live(state: Any) -> dict[str, dict[str, Any]]:
    """Return {provider_name: {endpoint, loaded:set, ok, latency_ms}} for lmstudio providers."""
    out: dict[str, dict[str, Any]] = {}
    inventory = state.model_registry.latest_inventory()
    inv_by_provider = {s.provider: s for s in inventory}
    for prov in state.providers.providers:
        if getattr(prov, "type", None) != "lmstudio":
            continue
        name = prov.name
        snap = inv_by_provider.get(name)
        endpoint = None
        loaded: set[str] = set()
        ok = bool(snap and snap.ok)
        latency = snap.latency_ms if snap else None
        if snap and snap.models:
            endpoint = snap.models[0].get("endpoint")
            loaded = _loaded_keys(snap)
        if not endpoint:
            endpoint = _strip_v1(prov.base_url)
        available: set[str] = set()
        if snap and snap.models:
            for m in snap.models:
                mid = _model_id(m)
                if mid:
                    available.add(mid)
        out[name] = {
            "endpoint": _strip_v1(endpoint),
            "loaded": loaded,
            "available": available,
            "ok": ok,
            "latency_ms": latency,
        }
    return out


def compute_plan(state: Any) -> dict[str, Any]:
    live = gather_live(state)
    loads: list[LoadAction] = []
    unloads: list[UnloadAction] = []
    for d in DESIRED_PLACEMENTS:
        node = live.get(d.provider)
        if not node:
            loads.append(
                LoadAction(
                    d.provider,
                    "",
                    d.model_key,
                    d.context_length,
                    d.ttl if d.ttl is not None else DEFAULT_TTL,
                    d.usecase,
                    swappable=is_swappable(d.provider),
                    reason="provider not present in live state",
                )
            )
            continue
        if not node["ok"]:
            loads.append(
                LoadAction(
                    d.provider,
                    node["endpoint"],
                    d.model_key,
                    d.context_length,
                    d.ttl if d.ttl is not None else DEFAULT_TTL,
                    d.usecase,
                    swappable=is_swappable(d.provider),
                    reason="node unreachable (ok=false)",
                )
            )
            continue
        if d.model_key in node["loaded"]:
            continue
        available = node.get("available") or set()
        if available and d.model_key not in available:
            # Desired key is not in this node's catalog (e.g. spelling
            # divergence between nodes). Skip rather than firing a doomed load
            # that would 400 and trip the circuit breaker.
            loads.append(
                LoadAction(
                    d.provider,
                    node["endpoint"],
                    d.model_key,
                    d.context_length,
                    d.ttl if d.ttl is not None else DEFAULT_TTL,
                    d.usecase,
                    swappable=is_swappable(d.provider),
                    reason="model key not in node catalog (skip)",
                )
            )
            continue
        loads.append(
            LoadAction(
                d.provider,
                node["endpoint"],
                d.model_key,
                d.context_length,
                d.ttl if d.ttl is not None else DEFAULT_TTL,
                d.usecase,
                swappable=is_swappable(d.provider),
                reason="desired model not loaded",
            )
        )
    if UNLOAD_ENABLED:
        desired_keys_by_provider: dict[str, set[str]] = {}
        for d in DESIRED_PLACEMENTS:
            desired_keys_by_provider.setdefault(d.provider, set()).add(d.model_key)
        for prov, node in live.items():
            for key in node["loaded"]:
                if key in desired_keys_by_provider.get(prov, set()):
                    continue
                if UNLOAD_ALLOWED_KEYS and key not in UNLOAD_ALLOWED_KEYS:
                    continue
                unloads.append(
                    UnloadAction(prov, node["endpoint"], key, reason="loaded but not in desired set")
                )
    return {
        "autoload_enabled": AUTOLOAD_ENABLED,
        "reconcile_seconds": RECONCILE_SECONDS,
        "default_ttl": DEFAULT_TTL,
        "live": {
            p: {
                "ok": n["ok"],
                "loaded": sorted(n["loaded"]),
                "latency_ms": n["latency_ms"],
                "swappable": is_swappable(p),
            }
            for p, n in live.items()
        },
        "desired_count": len(DESIRED_PLACEMENTS),
        "loads_needed": [vars(a) for a in loads],
        "load_backoff": {
            k: len([t for t in v if time.time() - t < LOAD_FAILURE_BACKOFF_SECONDS])
            for k, v in _load_failures.items()
        },
        "unloads_needed": [vars(a) for a in unloads],
    }


async def _load_model(endpoint: str, model_key: str, context_length: int, ttl: int = 0) -> dict[str, Any]:
    # LM Studio native load: POST /api/v1/models/load  body {model, context_length}
    # (ttl is not accepted by this server build; the reconcile loop re-pins
    # models before LM Studio's autoload TTL expires.)
    url = f"{endpoint}/api/v1/models/load"
    body = {"model": model_key, "context_length": context_length}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body)
            text = resp.text
            return {"ok": resp.status_code == 200, "status": resp.status_code, "detail": text[:300], "url": url}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "status": None, "detail": str(exc)[:300], "url": url}


async def _unload_model(endpoint: str, model_key: str) -> dict[str, Any]:
    # LM Studio native unload: POST /api/v1/models/unload  body {instance_id}
    url = f"{endpoint}/api/v1/models/unload"
    body = {"instance_id": model_key}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body)
            return {"ok": resp.status_code == 200, "status": resp.status_code, "detail": resp.text[:300], "url": url}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "status": None, "detail": str(exc)[:300], "url": url}


# Load-failure backoff: a placement that fails to load this many consecutive
# times is skipped for LOAD_FAILURE_BACKOFF_SECONDS so a model that genuinely
# cannot load on a node (engine crash, missing file) does not churn every
# reconcile cycle or keep its circuit breaker permanently tripped.
LOAD_FAILURE_BACKOFF_COUNT = int(os.getenv("AUTO_ROUTER_LOAD_FAILURE_BACKOFF_COUNT", "2"))
LOAD_FAILURE_BACKOFF_SECONDS = int(os.getenv("AUTO_ROUTER_LOAD_FAILURE_BACKOFF_SECONDS", "600"))
_load_failures: dict[str, list[float]] = {}


def _load_backoff_key(provider: str, model_key: str) -> str:
    return f"{provider}|{model_key}"


def _in_backoff(provider: str, model_key: str) -> bool:
    key = _load_backoff_key(provider, model_key)
    stamps = _load_failures.get(key)
    if not stamps:
        return False
    now = time.time()
    recent = [t for t in stamps if now - t < LOAD_FAILURE_BACKOFF_SECONDS]
    _load_failures[key] = recent
    return len(recent) >= LOAD_FAILURE_BACKOFF_COUNT


def _record_load_result(provider: str, model_key: str, ok: bool) -> None:
    key = _load_backoff_key(provider, model_key)
    if ok:
        _load_failures.pop(key, None)
    else:
        _load_failures.setdefault(key, []).append(time.time())


async def reconcile_once(state: Any, apply: bool | None = None, swappable_only: bool = False) -> dict[str, Any]:
    """Compute the plan and, if autoload is enabled (or apply=True), realize it.

    ``swappable_only=True`` restricts auto loads/unloads to nodes flagged
    swappable (see NODE_PROFILE / AUTO_ROUTER_AUTOLOAD_NODES). It is used by the
    background reconcile loop so "static" nodes are never touched. One-shot
    ``/admin/load`` calls pass ``swappable_only=False`` to allow a manual load
    anywhere.
    """
    plan = compute_plan(state)
    do_apply = apply if apply is not None else AUTOLOAD_ENABLED
    results: list[dict[str, Any]] = []
    if do_apply:
        for a in plan["loads_needed"]:
            if a["reason"].startswith("model key not in node catalog"):
                results.append({
                    **a,
                    "result": {"ok": False, "detail": "model key absent from node catalog — will not load", "status": None},
                    "skipped": True,
                })
                log.info("skip (absent key) load %s on %s", a["model_key"], a["provider"])
                continue
            if swappable_only and not a["swappable"]:
                results.append({
                    **a,
                    "result": {"ok": False, "detail": "node is static (not swappable) — manual load required", "status": None},
                    "skipped": True,
                })
                log.info("skip (static) load %s on %s", a["model_key"], a["provider"])
                continue
            if not a["endpoint"]:
                results.append({**a, "result": {"ok": False, "detail": "no endpoint", "status": None}})
                continue
            if _in_backoff(a["provider"], a["model_key"]):
                # Clear this model's circuit breaker so a persistently-unloadable
                # model (engine crash, missing file) does not keep fleet health
                # degraded. The backoff already prevents further load churn.
                cb = getattr(state, "circuits", None)
                if cb is not None:
                    try:
                        cb.reset(f"{a['provider']}/{a['model_key']}")
                    except Exception:
                        pass
                results.append({
                    **a,
                    "result": {"ok": False, "detail": "load failing repeatedly; backing off to avoid churn", "status": None},
                    "skipped": True,
                })
                log.info("skip (backoff) load %s on %s", a["model_key"], a["provider"])
                continue
            res = await _load_model(a["endpoint"], a["model_key"], a["context_length"], a["ttl"])
            _record_load_result(a["provider"], a["model_key"], bool(res.get("ok")))
            results.append({**a, "result": res})
            log.info("load %s on %s -> %s", a["model_key"], a["provider"], res)
        for a in plan["unloads_needed"]:
            if swappable_only and not is_swappable(a["provider"]):
                results.append({
                    **a,
                    "result": {"ok": False, "detail": "node is static (not swappable) — manual unload required", "status": None},
                    "skipped": True,
                })
                continue
            res = await _unload_model(a["endpoint"], a["model_key"])
            results.append({**a, "result": res})
            log.info("unload %s on %s -> %s", a["model_key"], a["provider"], res)
    plan["applied"] = do_apply
    plan["actions_taken"] = results
    return plan


async def placement_reconcile_task() -> None:
    if RECONCILE_SECONDS <= 0:
        return
    from auto_router.main import state  # local import to avoid cycle

    while True:
        await asyncio.sleep(RECONCILE_SECONDS)
        try:
            if AUTOLOAD_ENABLED:
                await reconcile_once(state, apply=True, swappable_only=True)
        except Exception as exc:  # pragma: no cover
            log.warning("placement reconcile error: %s", exc)
