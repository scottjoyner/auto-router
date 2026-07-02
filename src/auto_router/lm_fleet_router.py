#!/usr/bin/env python3
"""
lm_fleet_router.py

Single-file LM Studio fleet heartbeat + OpenAI-compatible router for a Tailscale homelab.

What it does:
  - Probes configured nodes on :1234.
  - Reads LM Studio native /api/v1/models for available + loaded model instances.
  - Falls back to OpenAI-compatible /v1/models when native discovery is unavailable.
  - Exposes a single OpenAI-compatible /v1 endpoint surface.
  - Scores candidate node/model pairs by loaded-in-memory status, health, latency, context fit,
    historical speed, historical quality, and current in-flight requests through this router.
  - Persists heartbeats and run telemetry to SQLite so routing improves over time.
  - Runs a background task queue that dispatches small tasks (<4096 tokens) to idle nodes
    and writes responses as markdown docs into the knowledge vault workspace.
  - Tracks cost per token using observed latency, power estimates, and output quality.

Install:
  python3 -m venv .venv
  . .venv/bin/activate
  pip install fastapi 'uvicorn[standard]' httpx pydantic

Run:
  export LM_FLEET_BIND=0.0.0.0
  export LM_FLEET_PORT=8091
  # Optional, if LM Studio authentication is enabled on your nodes:
  export LM_API_TOKEN='your-lmstudio-token'
  python lm_fleet_router.py

Use as OpenAI-compatible base URL:
  export OPENAI_BASE_URL=http://<router-node-tailscale-ip>:8091/v1
  export OPENAI_API_KEY=local-router
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

DEFAULT_PORT = int(os.getenv("LM_FLEET_LMSTUDIO_PORT", "1234"))
DB_PATH = Path(os.getenv("LM_FLEET_DB", "./lm_fleet_router.sqlite3"))
PROBE_TIMEOUT = float(os.getenv("LM_FLEET_PROBE_TIMEOUT", "2.5"))
REQUEST_TIMEOUT = float(os.getenv("LM_FLEET_REQUEST_TIMEOUT", "900"))
HEARTBEAT_SECONDS = float(os.getenv("LM_FLEET_HEARTBEAT_SECONDS", "15"))
STALE_AFTER_SECONDS = float(os.getenv("LM_FLEET_STALE_AFTER_SECONDS", "45"))
ALLOW_AUTO_LOAD = os.getenv("LM_FLEET_ALLOW_AUTO_LOAD", "false").lower() in {"1", "true", "yes"}
DEFAULT_LM_API_TOKEN = os.getenv("LM_API_TOKEN") or os.getenv("LMSTUDIO_API_TOKEN")

# Knowledge vault workspace for task responses
KNOWLEDGE_VAULT_WORKSPACE = Path(os.getenv("LM_FLEET_VAULT_WORKSPACE", "/home/scott/knowledge/vault-workspace"))
TASK_QUEUE_INTERVAL = float(os.getenv("LM_FLEET_TASK_INTERVAL", "5"))
TASK_MAX_INPUT_TOKENS = int(os.getenv("LM_FLEET_TASK_MAX_INPUT_TOKENS", "4096"))
TASK_POWER_ESTIMATE_WATTS = float(os.getenv("LM_FLEET_TASK_POWER_WATTS", "65"))  # rough per-node power draw

# Inventory derived from the table provided in the prompt.
DEFAULT_NODES: list[dict[str, Any]] = [
    {
        "name": "deathstar-xps-8920",
        "device_id": "nbhTPZ6Zi121CNTRL",
        "os": "linux",
        "os_version": "6.17.0-35-generic",
        "domain": "deathstar-xps-8920.tailcb8954.ts.net",
        "tailscale_ip": "100.78.106.121",
        "role": "gpu-small-vram",
        "lmlink_expected": True,
        "notes": "RX480-class 8 GB VRAM; prefer VRAM-fit quantized models; avoid RAM spill.",
    },
    {
        "name": "destroyer",
        "device_id": "ndeQfPitL511CNTRL",
        "os": "linux",
        "os_version": "6.17.0-23-generic",
        "domain": "destroyer.tailcb8954.ts.net",
        "tailscale_ip": "100.81.57.77",
        "role": "linux-worker",
        "lmlink_expected": True,
        "notes": "General Linux worker; score from live telemetry.",
    },
    {
        "name": "iphone-12-pro-max",
        "device_id": "nkDLn1fpn611CNTRL",
        "os": "iOS",
        "os_version": "26.5.0",
        "domain": "iphone-12-pro-max.tailcb8954.ts.net",
        "tailscale_ip": "100.96.196.106",
        "role": "mobile-small",
        "lmlink_expected": False,
        "notes": "Mobile/on-device source; useful only when an OpenAI-compatible endpoint is actually reachable.",
    },
    {
        "name": "scott-lenovo-ideapad-330s-15ikb",
        "device_id": "n4DqFVeUEg11CNTRL",
        "os": "linux",
        "os_version": "6.8.0-41-generic",
        "domain": "scott-lenovo-ideapad-330s-15ikb.tailcb8954.ts.net",
        "tailscale_ip": "100.105.137.98",
        "role": "linux-small",
        "lmlink_expected": True,
        "notes": "Small Linux endpoint; route quick/low-risk work only after telemetry proves stable.",
    },
    {
        "name": "scott-optiplex-9030-aio",
        "device_id": "nNTqmifCaB11CNTRL",
        "os": "linux",
        "os_version": "6.17.0-29-generic",
        "domain": "scott-optiplex-9030-aio.tailcb8954.ts.net",
        "tailscale_ip": "100.69.158.114",
        "role": "linux-aio",
        "lmlink_expected": True,
        "notes": "General endpoint; score from live telemetry.",
    },
    {
        "name": "beelink-ryzen-7-mini-pc",
        "device_id": "nAPU6WKCWN11CNTRL",
        "os": "linux",
        "os_version": "6.8.0-41-generic",
        "domain": "beelink-ryzen-7-mini-pc.tailcb8954.ts.net",
        "tailscale_ip": "100.85.72.121",
        "role": "mini-pc-mid",
        "lmlink_expected": True,
        "notes": "Fresh Linux install on the Ryzen 7 Beelink; local endpoint is still being benchmarked.",
    },
    {
        "name": "scotts-macbook-air",
        "device_id": "nmMGwLCeMU11CNTRL",
        "os": "macOS",
        "os_version": "14.5.0",
        "domain": "scotts-macbook-air.tailcb8954.ts.net",
        "tailscale_ip": "100.85.64.117",
        "role": "macbook-air-fast-small",
        "lmlink_expected": True,
        "notes": "Only Mac node to probe. Use for short, low-latency drafts; avoid long context/heavy jobs.",
    },
    {
        "name": "x1-370",
        "device_id": "nMUcwkoaJq11CNTRL",
        "os": "linux",
        "os_version": "6.17.0-35-generic",
        "domain": "x1-370.tailcb8954.ts.net",
        "tailscale_ip": "100.64.43.123",
        "role": "heavy-local-reasoning",
        "lmlink_expected": True,
        "notes": "Strong local reasoning node; protect shared AssistX/Neo4j services from overload.",
    },
    {
        "name": "xwing",
        "device_id": "nGY7Xfmr4V11CNTRL",
        "os": "linux",
        "os_version": "6.17.0-1025-oem",
        "domain": "xwing.tailcb8954.ts.net",
        "tailscale_ip": "100.108.99.47",
        "role": "default-dev-worker",
        "lmlink_expected": True,
        "notes": "Default repo/dev worker; known LM Studio candidate on 100.108.99.47:1234.",
    },
]


def now_ms() -> int:
    return int(time.time() * 1000)


def rough_token_count(value: Any) -> int:
    """Cheap, dependency-free estimator. Good enough for routing guardrails."""
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return max(1, int(len(text) / 3.7))


def normalize_model_id(model: str | None) -> str:
    return (model or "auto/fast").strip()


def model_family_score(model_key: str) -> float:
    """Static prior only. Telemetry overrides this as runs accumulate."""
    m = model_key.lower()
    score = 0.55
    if any(x in m for x in ["qwen", "deepseek", "r1", "coder", "codestral"]):
        score += 0.18
    if any(x in m for x in ["gemma", "llama", "mistral", "mixtral"]):
        score += 0.10
    if any(x in m for x in ["7b", "8b", "9b", "12b", "14b"]):
        score += 0.04
    if any(x in m for x in ["20b", "26b", "27b", "32b", "70b", "72b"]):
        score += 0.09
    if "embedding" in m or "embed" in m:
        score -= 0.25
    return max(0.05, min(score, 1.0))


@dataclass
class NodeConfig:
    name: str
    device_id: str
    os: str
    os_version: str
    domain: str
    tailscale_ip: str
    role: str
    lmlink_expected: bool = False
    notes: str = ""
    enabled: bool = True
    port: int = DEFAULT_PORT
    api_token: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.tailscale_ip}:{self.port}"


@dataclass
class ModelInstance:
    id: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class FleetModel:
    node: str
    key: str
    display_name: str
    type: str = "llm"
    publisher: str = ""
    architecture: str | None = None
    quantization: dict[str, Any] | None = None
    size_bytes: int | None = None
    params_string: str | None = None
    format: str | None = None
    max_context_length: int | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    loaded_instances: list[ModelInstance] = field(default_factory=list)
    source: Literal["native", "openai"] = "native"

    @property
    def loaded(self) -> bool:
        return bool(self.loaded_instances)

    @property
    def best_context_length(self) -> int | None:
        loaded_contexts = [
            int(i.config.get("context_length") or 0)
            for i in self.loaded_instances
            if isinstance(i.config, dict) and str(i.config.get("context_length", "")).isdigit()
        ]
        if loaded_contexts:
            return max(loaded_contexts)
        return self.max_context_length


@dataclass
class NodeState:
    config: NodeConfig
    online: bool = False
    native_ok: bool = False
    openai_ok: bool = False
    status: str = "unknown"
    last_seen_ms: int = 0
    latency_ms: float | None = None
    error: str | None = None
    models: list[FleetModel] = field(default_factory=list)
    in_flight: int = 0
    last_request_ms: int = 0

    @property
    def stale(self) -> bool:
        return not self.last_seen_ms or (now_ms() - self.last_seen_ms) > STALE_AFTER_SECONDS * 1000

    @property
    def busy(self) -> bool:
        if self.in_flight > 0:
            return True
        if self.last_request_ms and (now_ms() - self.last_request_ms) < 1500:
            return True
        return False

    @property
    def idle(self) -> bool:
        return self.online and not self.busy


class RouteRequestMeta(BaseModel):
    privacy: str | None = None
    data_class: str | None = None
    local_only: bool | None = None
    allow_cloud: bool | None = None
    task_type: str | None = None
    source_node: str | None = None
    quality_min: float | None = None
    max_context_tokens: int | None = None


class ScoreBreakdown(BaseModel):
    node: str
    model: str
    score: float
    loaded: bool
    latency_ms: float | None
    in_flight: int
    context_length: int | None
    estimated_input_tokens: int
    speed_tps_ewma: float | None
    quality_ewma: float | None
    reasons: list[str]


class FleetRouter:
    def __init__(self, nodes: list[NodeConfig]) -> None:
        self.nodes = {n.name: n for n in nodes if n.enabled}
        self.states: dict[str, NodeState] = {n.name: NodeState(config=n) for n in nodes if n.enabled}
        self._lock = asyncio.Lock()
        self._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cur = self._db.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS node_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                node TEXT NOT NULL,
                online INTEGER NOT NULL,
                native_ok INTEGER NOT NULL,
                openai_ok INTEGER NOT NULL,
                latency_ms REAL,
                model_count INTEGER NOT NULL,
                loaded_model_count INTEGER NOT NULL,
                error TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS run_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                request_id TEXT NOT NULL,
                node TEXT NOT NULL,
                model TEXT NOT NULL,
                route TEXT NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                estimated_tps REAL,
                quality_score REAL,
                error TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                node TEXT NOT NULL,
                model TEXT NOT NULL,
                output_tokens INTEGER,
                latency_ms REAL,
                power_joules REAL,
                cost_per_million REAL,
                quality_score REAL,
                effective_cost_per_token REAL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_run_node_model ON run_telemetry(node, model, ts_ms)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_node_ts ON node_snapshots(node, ts_ms)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cost_node_model ON cost_tracking(node, model)")
        self._db.commit()

    @staticmethod
    def _headers(node: NodeConfig) -> dict[str, str]:
        token = node.api_token or DEFAULT_LM_API_TOKEN
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def probe_all(self) -> dict[str, Any]:
        async with self._lock:
            results = await asyncio.gather(
                *(self._probe_node(config) for config in self.nodes.values()),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, NodeState):
                    result.in_flight = self.states[result.config.name].in_flight
                    result.last_request_ms = self.states[result.config.name].last_request_ms
                    self.states[result.config.name] = result
                    self._persist_snapshot(result)
            return self.snapshot()

    async def _probe_node(self, node: NodeConfig) -> NodeState:
        state = NodeState(config=node)
        started = time.perf_counter()
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            native_data: dict[str, Any] | None = None
            try:
                resp = await client.get(f"{node.base_url}/api/v1/models", headers=self._headers(node))
                state.latency_ms = (time.perf_counter() - started) * 1000
                if resp.status_code < 500:
                    resp.raise_for_status()
                    native_data = resp.json()
                    state.native_ok = True
                    state.online = True
                    state.models.extend(self._parse_native_models(node.name, native_data))
            except Exception as exc:
                errors.append(f"native:/api/v1/models:{type(exc).__name__}:{exc}")

            # OpenAI compatibility is checked separately because it is the surface client tools will use.
            try:
                started2 = time.perf_counter()
                resp = await client.get(f"{node.base_url}/v1/models", headers=self._headers(node))
                state.latency_ms = state.latency_ms or ((time.perf_counter() - started2) * 1000)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    openai_data = resp.json()
                    state.openai_ok = True
                    state.online = True
                    if not native_data:
                        state.models.extend(self._parse_openai_models(node.name, openai_data))
            except Exception as exc:
                errors.append(f"openai:/v1/models:{type(exc).__name__}:{exc}")

        state.last_seen_ms = now_ms() if state.online else 0
        if state.online:
            loaded_count = sum(1 for m in state.models if m.loaded)
            state.status = "idle" if loaded_count else "online_no_loaded_models"
        else:
            state.status = "offline"
            state.error = "; ".join(errors[-2:]) if errors else "unreachable"
        return state

    @staticmethod
    def _parse_native_models(node_name: str, payload: dict[str, Any] | None) -> list[FleetModel]:
        if not payload:
            return []
        models: list[FleetModel] = []
        for item in payload.get("models", []) or []:
            loaded_instances = []
            for inst in item.get("loaded_instances", []) or []:
                loaded_instances.append(ModelInstance(id=str(inst.get("id") or item.get("key")), config=inst.get("config") or {}))
            models.append(
                FleetModel(
                    node=node_name,
                    key=str(item.get("key") or item.get("id") or item.get("display_name")),
                    display_name=str(item.get("display_name") or item.get("key") or item.get("id")),
                    type=str(item.get("type") or "llm"),
                    publisher=str(item.get("publisher") or ""),
                    architecture=item.get("architecture"),
                    quantization=item.get("quantization"),
                    size_bytes=item.get("size_bytes"),
                    params_string=item.get("params_string"),
                    format=item.get("format"),
                    max_context_length=item.get("max_context_length"),
                    capabilities=item.get("capabilities") or {},
                    loaded_instances=loaded_instances,
                    source="native",
                )
            )
        return models

    @staticmethod
    def _parse_openai_models(node_name: str, payload: dict[str, Any]) -> list[FleetModel]:
        models: list[FleetModel] = []
        for item in payload.get("data", []) or []:
            model_id = str(item.get("id") or "")
            if not model_id:
                continue
            models.append(
                FleetModel(
                    node=node_name,
                    key=model_id,
                    display_name=model_id,
                    publisher=str(item.get("owned_by") or ""),
                    loaded_instances=[],
                    source="openai",
                )
            )
        return models

    def _persist_snapshot(self, state: NodeState) -> None:
        payload = self._node_payload(state)
        cur = self._db.cursor()
        cur.execute(
            """
            INSERT INTO node_snapshots
            (ts_ms, node, online, native_ok, openai_ok, latency_ms, model_count, loaded_model_count, error, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_ms(),
                state.config.name,
                1 if state.online else 0,
                1 if state.native_ok else 0,
                1 if state.openai_ok else 0,
                state.latency_ms,
                len(state.models),
                sum(1 for m in state.models if m.loaded),
                state.error,
                json.dumps(payload, sort_keys=True),
            ),
        )
        self._db.commit()

    def _record_run(
        self,
        *,
        request_id: str,
        node: str,
        model: str,
        route: str,
        status_code: int | None,
        latency_ms: float | None,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        estimated_tps: float | None,
        quality_score: float | None = None,
        error: str | None = None,
    ) -> None:
        self._db.execute(
            """
            INSERT INTO run_telemetry
            (ts_ms, request_id, node, model, route, status_code, latency_ms, input_tokens, output_tokens,
             total_tokens, estimated_tps, quality_score, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_ms(),
                request_id,
                node,
                model,
                route,
                status_code,
                latency_ms,
                input_tokens,
                output_tokens,
                total_tokens,
                estimated_tps,
                quality_score,
                error,
            ),
        )

        # Also record cost tracking data
        if output_tokens and latency_ms:
            power_joules = TASK_POWER_ESTIMATE_WATTS * (latency_ms / 1000)
            # Cost per million tokens based on observed metrics
            cost_per_million = (power_joules / 3600) * 1000000 / output_tokens if output_tokens > 0 else 0
            self._db.execute(
                """
                INSERT INTO cost_tracking
                (ts_ms, node, model, output_tokens, latency_ms, power_joules, cost_per_million, quality_score, effective_cost_per_token)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (now_ms(), node, model, output_tokens, latency_ms, power_joules, cost_per_million, quality_score, cost_per_million / output_tokens if output_tokens > 0 else cost_per_million),
            )

        self._db.commit()

    def _record_cost(self, node: str, model: str, output_tokens: int, latency_ms: float, quality_score: float) -> None:
        """Record cost data for a completed run."""
        power_joules = TASK_POWER_ESTIMATE_WATTS * (latency_ms / 1000)
        cost_per_million = (power_joules / 3600) * 1000000 / output_tokens if output_tokens > 0 else 0
        self._db.execute(
            """
            INSERT INTO cost_tracking
            (ts_ms, node, model, output_tokens, latency_ms, power_joules, cost_per_million, quality_score, effective_cost_per_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now_ms(), node, model, output_tokens, latency_ms, power_joules, cost_per_million, quality_score, cost_per_million / output_tokens if output_tokens > 0 else cost_per_million),
        )
        self._db.commit()

    def telemetry_for(self, node: str, model: str) -> dict[str, float | None]:
        rows = self._db.execute(
            """
            SELECT estimated_tps, quality_score, latency_ms
            FROM run_telemetry
            WHERE node = ? AND model = ?
            ORDER BY ts_ms DESC LIMIT 50
            """,
            (node, model),
        ).fetchall()
        tps = [float(r["estimated_tps"]) for r in rows if r["estimated_tps"] is not None and r["estimated_tps"] > 0]
        quality = [float(r["quality_score"]) for r in rows if r["quality_score"] is not None]
        latency = [float(r["latency_ms"]) for r in rows if r["latency_ms"] is not None]
        return {
            "speed_tps_ewma": self._weighted_average(tps),
            "quality_ewma": self._weighted_average(quality),
            "latency_ms_median": statistics.median(latency) if latency else None,
        }

    @staticmethod
    def _weighted_average(values: list[float]) -> float | None:
        if not values:
            return None
        weights = [0.92**i for i in range(len(values))]
        return sum(v * w for v, w in zip(values, weights, strict=False)) / sum(weights)

    def model_alias_matches(self, requested: str, model: FleetModel, route: str) -> bool:
        req = requested.lower()
        key = model.key.lower()
        if model.type == "embedding" and route != "embeddings":
            return False
        if route == "embeddings" and model.type != "embedding" and "embed" not in key:
            return False
        if req.startswith("auto/"):
            if req in {"auto/local", "auto/private", "auto/fast", "auto/sophia", "auto/high-quality", "auto/code"}:
                return True
            if req == "auto/embedding" and (model.type == "embedding" or "embed" in key):
                return True
        if requested == model.key:
            return True
        return req in key or key.endswith(req)

    def choose_candidate(
        self,
        *,
        requested_model: str,
        route: str,
        body: dict[str, Any],
        client_host: str | None,
    ) -> tuple[NodeConfig, FleetModel, list[ScoreBreakdown]]:
        estimated_input_tokens = rough_token_count(body.get("messages") or body.get("input") or body.get("prompt") or body)
        requested_context = self._requested_context(body, estimated_input_tokens)
        metadata = body.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        source_node = metadata.get("source_node") or self._node_name_for_ip(client_host)
        privacy = str(metadata.get("privacy") or metadata.get("data_class") or "").lower()
        local_only = bool(metadata.get("local_only")) or privacy in {"private", "personal", "internal", "sensitive", "secret"}

        scored: list[ScoreBreakdown] = []
        candidates: list[tuple[float, NodeConfig, FleetModel]] = []
        for state in self.states.values():
            if not state.online or state.stale:
                continue
            for model in state.models:
                if not self.model_alias_matches(requested_model, model, route):
                    continue
                reasons: list[str] = []
                context_len = model.best_context_length
                if context_len is not None and requested_context > context_len:
                    scored.append(
                        ScoreBreakdown(
                            node=state.config.name,
                            model=model.key,
                            score=-999,
                            loaded=model.loaded,
                            latency_ms=state.latency_ms,
                            in_flight=state.in_flight,
                            context_length=context_len,
                            estimated_input_tokens=estimated_input_tokens,
                            speed_tps_ewma=None,
                            quality_ewma=None,
                            reasons=[f"rejected: requested context {requested_context} > available {context_len}"],
                        )
                    )
                    continue

                telemetry = self.telemetry_for(state.config.name, model.key)
                speed_tps = telemetry["speed_tps_ewma"]
                quality = telemetry["quality_ewma"]

                score = 0.0
                if model.loaded:
                    score += 100.0
                    reasons.append("loaded-in-memory +100")
                else:
                    score -= 25.0
                    reasons.append("not-loaded -25")

                if state.idle:
                    score += 20.0
                    reasons.append("node-idle +20")
                else:
                    penalty = 25.0 + 18.0 * state.in_flight
                    score -= penalty
                    reasons.append(f"busy/in-flight -{penalty:.1f}")

                if state.latency_ms is not None:
                    latency_bonus = max(0.0, 20.0 - min(state.latency_ms, 2000.0) / 100.0)
                    score += latency_bonus
                    reasons.append(f"latency-bonus +{latency_bonus:.1f}")

                if speed_tps is not None:
                    speed_bonus = min(40.0, max(0.0, speed_tps))
                    score += speed_bonus
                    reasons.append(f"observed-tps +{speed_bonus:.1f}")
                else:
                    prior = model_family_score(model.key) * 12.0
                    score += prior
                    reasons.append(f"model-prior +{prior:.1f}")

                if quality is not None:
                    q_bonus = max(-20.0, min(35.0, (quality - 0.5) * 70.0))
                    score += q_bonus
                    reasons.append(f"quality-ewma {quality:.2f} -> {q_bonus:+.1f}")

                if source_node and source_node == state.config.name:
                    score += 15.0
                    reasons.append("source-local +15")

                if state.config.role in {"mobile-small", "macbook-air-fast-small", "linux-small"} and requested_context > 6000:
                    score -= 35.0
                    reasons.append("small-node-long-context -35")

                if local_only:
                    reasons.append("local-only satisfied by Tailscale node")

                breakdown = ScoreBreakdown(
                    node=state.config.name,
                    model=model.key,
                    score=round(score, 3),
                    loaded=model.loaded,
                    latency_ms=state.latency_ms,
                    in_flight=state.in_flight,
                    context_length=context_len,
                    estimated_input_tokens=estimated_input_tokens,
                    speed_tps_ewma=speed_tps,
                    quality_ewma=quality,
                    reasons=reasons,
                )
                scored.append(breakdown)
                candidates.append((score, state.config, model))

        if not candidates:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "no healthy LM Studio candidate matched request",
                    "requested_model": requested_model,
                    "route": route,
                    "estimated_input_tokens": estimated_input_tokens,
                    "candidate_debug": [s.model_dump() for s in scored[:25]],
                },
            )
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1], candidates[0][2], sorted(scored, key=lambda x: x.score, reverse=True)

    @staticmethod
    def _requested_context(body: dict[str, Any], input_tokens: int) -> int:
        max_out = body.get("max_tokens") or body.get("max_output_tokens") or body.get("max_completion_tokens") or 1024
        try:
            max_out_i = int(max_out)
        except Exception:
            max_out_i = 1024
        explicit_context = body.get("context_length") or (body.get("metadata") or {}).get("max_context_tokens") if isinstance(body.get("metadata"), dict) else None
        if explicit_context:
            try:
                return max(int(explicit_context), input_tokens + max_out_i)
            except Exception:
                pass
        return input_tokens + max_out_i

    def _node_name_for_ip(self, ip: str | None) -> str | None:
        if not ip:
            return None
        for state in self.states.values():
            if ip == state.config.tailscale_ip:
                return state.config.name
        return None

    def _node_payload(self, state: NodeState) -> dict[str, Any]:
        return {
            "name": state.config.name,
            "device_id": state.config.device_id,
            "role": state.config.role,
            "os": state.config.os,
            "os_version": state.config.os_version,
            "domain": state.config.domain,
            "tailscale_ip": state.config.tailscale_ip,
            "base_url": state.config.base_url,
            "lmlink_expected": state.config.lmlink_expected,
            "notes": state.config.notes,
            "online": state.online,
            "native_ok": state.native_ok,
            "openai_ok": state.openai_ok,
            "status": "busy" if state.busy else state.status,
            "idle": state.idle,
            "busy": state.busy,
            "stale": state.stale,
            "last_seen_ms": state.last_seen_ms,
            "latency_ms": state.latency_ms,
            "in_flight": state.in_flight,
            "error": state.error,
            "model_count": len(state.models),
            "loaded_model_count": sum(1 for m in state.models if m.loaded),
            "loaded_models": [m.key for m in state.models if m.loaded],
            "available_models": [m.key for m in state.models],
        }

    def snapshot(self) -> dict[str, Any]:
        nodes = [self._node_payload(s) for s in self.states.values()]
        model_rows: list[dict[str, Any]] = []
        for s in self.states.values():
            for m in s.models:
                telemetry = self.telemetry_for(s.config.name, m.key)
                model_rows.append(
                    {
                        "node": s.config.name,
                        "model": m.key,
                        "display_name": m.display_name,
                        "type": m.type,
                        "loaded": m.loaded,
                        "loaded_instances": [{"id": i.id, "config": i.config} for i in m.loaded_instances],
                        "context_length": m.best_context_length,
                        "max_context_length": m.max_context_length,
                        "quantization": m.quantization,
                        "params_string": m.params_string,
                        "format": m.format,
                        "capabilities": m.capabilities,
                        "source": m.source,
                        "speed_tps_ewma": telemetry["speed_tps_ewma"],
                        "quality_ewma": telemetry["quality_ewma"],
                    }
                )
        return {
            "service": "lm-fleet-router",
            "time_ms": now_ms(),
            "db": str(DB_PATH),
            "heartbeat_seconds": HEARTBEAT_SECONDS,
            "stale_after_seconds": STALE_AFTER_SECONDS,
            "allow_auto_load": ALLOW_AUTO_LOAD,
            "summary": {
                "nodes_total": len(nodes),
                "nodes_online": sum(1 for n in nodes if n["online"]),
                "nodes_idle": sum(1 for n in nodes if n["idle"]),
                "nodes_busy": sum(1 for n in nodes if n["busy"]),
                "models_available": len(model_rows),
                "models_loaded": sum(1 for m in model_rows if m["loaded"]),
            },
            "nodes": nodes,
            "models": model_rows,
        }

    async def maybe_load_model(self, node: NodeConfig, model: str) -> None:
        if not ALLOW_AUTO_LOAD:
            return
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            await client.post(
                f"{node.base_url}/api/v1/models/load",
                headers={**self._headers(node), "Content-Type": "application/json"},
                json={"model": model, "echo_load_config": True},
            )

    async def proxy_openai(self, route: str, request: Request, body: dict[str, Any]) -> JSONResponse | StreamingResponse:
        requested_model = normalize_model_id(body.get("model"))
        node, model, scored = self.choose_candidate(
            requested_model=requested_model,
            route=route,
            body=body,
            client_host=request.client.host if request.client else None,
        )
        request_id = str(uuid.uuid4())
        state = self.states[node.name]
        state.in_flight += 1
        state.last_request_ms = now_ms()
        body = dict(body)
        body["model"] = model.key
        headers = {**self._headers(node), "Content-Type": "application/json"}
        endpoint = {
            "chat_completions": "/v1/chat/completions",
            "responses": "/v1/responses",
            "embeddings": "/v1/embeddings",
            "completions": "/v1/completions",
        }[route]
        started = time.perf_counter()
        input_tokens_est = rough_token_count(body.get("messages") or body.get("input") or body.get("prompt") or body)

        try:
            if body.get("stream") is True and route in {"chat_completions", "responses", "completions"}:
                return await self._proxy_stream(
                    node=node,
                    model=model,
                    route=route,
                    endpoint=endpoint,
                    headers=headers,
                    body=body,
                    request_id=request_id,
                    started=started,
                    input_tokens_est=input_tokens_est,
                    scored=scored,
                )

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(f"{node.base_url}{endpoint}", headers=headers, json=body)
                latency_ms = (time.perf_counter() - started) * 1000
                payload = self._safe_json(resp)
                usage = payload.get("usage") if isinstance(payload, dict) else None
                input_tokens, output_tokens, total_tokens = self._usage_tokens(usage, input_tokens_est)
                tps = (output_tokens / (latency_ms / 1000)) if output_tokens and latency_ms > 0 else None
                self._record_run(
                    request_id=request_id,
                    node=node.name,
                    model=model.key,
                    route=route,
                    status_code=resp.status_code,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_tps=tps,
                )
                if isinstance(payload, dict):
                    payload.setdefault("auto_router", {})
                    payload["auto_router"].update(
                        {
                            "request_id": request_id,
                            "selected_node": node.name,
                            "selected_endpoint": node.base_url,
                            "selected_model": model.key,
                            "original_model": requested_model,
                            "latency_ms": round(latency_ms, 3),
                            "route_debug_top": [s.model_dump() for s in scored[:5]],
                        }
                    )
                return JSONResponse(payload, status_code=resp.status_code)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            self._record_run(
                request_id=request_id,
                node=node.name,
                model=model.key,
                route=route,
                status_code=None,
                latency_ms=latency_ms,
                input_tokens=input_tokens_est,
                output_tokens=None,
                total_tokens=None,
                estimated_tps=None,
                error=str(exc),
            )
            raise HTTPException(status_code=502, detail={"error": str(exc), "node": node.name, "model": model.key}) from exc
        finally:
            state.in_flight = max(0, state.in_flight - 1)

    async def _proxy_stream(
        self,
        *,
        node: NodeConfig,
        model: FleetModel,
        route: str,
        endpoint: str,
        headers: dict[str, str],
        body: dict[str, Any],
        request_id: str,
        started: float,
        input_tokens_est: int,
        scored: list[ScoreBreakdown],
    ) -> StreamingResponse:
        state = self.states[node.name]

        async def iterator() -> AsyncIterator[bytes]:
            output_chars = 0
            status_code: int | None = None
            error: str | None = None
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    async with client.stream("POST", f"{node.base_url}{endpoint}", headers=headers, json=body) as resp:
                        status_code = resp.status_code
                        async for chunk in resp.aiter_bytes():
                            output_chars += len(chunk)
                            yield chunk
            except Exception as exc:
                error = str(exc)
                yield f"event: error\ndata: {json.dumps({'error': error, 'node': node.name, 'model': model.key})}\n\n".encode()
            finally:
                latency_ms = (time.perf_counter() - started) * 1000
                out_tokens = max(1, int(output_chars / 4)) if output_chars else None
                tps = (out_tokens / (latency_ms / 1000)) if out_tokens and latency_ms > 0 else None
                self._record_run(
                    request_id=request_id,
                    node=node.name,
                    model=model.key,
                    route=route,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens_est,
                    output_tokens=out_tokens,
                    total_tokens=(input_tokens_est + out_tokens) if out_tokens else None,
                    estimated_tps=tps,
                    error=error,
                )
                state.in_flight = max(0, state.in_flight - 1)

        return StreamingResponse(
            iterator(),
            media_type="text/event-stream",
            headers={
                "x-lm-fleet-request-id": request_id,
                "x-lm-fleet-selected-node": node.name,
                "x-lm-fleet-selected-model": model.key,
                "x-lm-fleet-selected-endpoint": node.base_url,
                "x-lm-fleet-route-score": str(scored[0].score if scored else ""),
            },
        )

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text, "status_code": resp.status_code}

    @staticmethod
    def _usage_tokens(usage: Any, input_estimate: int) -> tuple[int | None, int | None, int | None]:
        if not isinstance(usage, dict):
            return input_estimate, None, None
        input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or input_estimate
        output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
        total_tokens = usage.get("total_tokens") or (
            (int(input_tokens) + int(output_tokens)) if input_tokens is not None and output_tokens is not None else None
        )
        return int(input_tokens) if input_tokens is not None else None, int(output_tokens) if output_tokens is not None else None, int(total_tokens) if total_tokens is not None else None

    def openai_models(self) -> dict[str, Any]:
        data: list[dict[str, Any]] = []
        seen: set[str] = set()
        for state in self.states.values():
            if not state.online or state.stale:
                continue
            for model in state.models:
                ids = [model.key, f"{state.config.name}/{model.key}"]
                for mid in ids:
                    if mid in seen:
                        continue
                    seen.add(mid)
                    data.append(
                        {
                            "id": mid,
                            "object": "model",
                            "created": 0,
                            "owned_by": f"lmstudio:{state.config.name}",
                            "loaded": model.loaded,
                            "node": state.config.name,
                            "context_length": model.best_context_length,
                        }
                    )
        for alias in ["auto/fast", "auto/local", "auto/private", "auto/sophia", "auto/high-quality", "auto/code", "auto/embedding"]:
            data.append({"id": alias, "object": "model", "created": 0, "owned_by": "lm-fleet-router"})
        return {"object": "list", "data": data}

    def update_quality(self, request_id: str, quality_score: float) -> int:
        cur = self._db.execute(
            "UPDATE run_telemetry SET quality_score = ? WHERE request_id = ?",
            (quality_score, request_id),
        )
        self._db.commit()
        return int(cur.rowcount)

    def idle_nodes(self) -> list[dict[str, Any]]:
        return [self._node_payload(s) for s in self.states.values() if s.idle]


def load_nodes() -> list[NodeConfig]:
    raw = os.getenv("LM_FLEET_NODES_JSON")
    data = json.loads(raw) if raw else DEFAULT_NODES
    nodes = []
    for item in data:
        item = dict(item)
        env_name = "LM_API_TOKEN_" + re.sub(r"[^A-Z0-9]+", "_", item["name"].upper()).strip("_")
        item["api_token"] = item.get("api_token") or os.getenv(env_name)
        nodes.append(NodeConfig(**item))
    return nodes


router = FleetRouter(load_nodes())
app = FastAPI(title="LM Fleet Router", version="0.1.0")


@app.on_event("startup")
async def startup() -> None:
    await router.probe_all()
    asyncio.create_task(_heartbeat_loop())


async def _heartbeat_loop() -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            await router.probe_all()
        except Exception as exc:
            print(f"heartbeat failed: {exc}")


@app.get("/health")
async def health() -> dict[str, Any]:
    snap = router.snapshot()
    return {
        "ok": snap["summary"]["nodes_online"] > 0,
        "service": "lm-fleet-router",
        "summary": snap["summary"],
        "time_ms": snap["time_ms"],
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    snap = router.snapshot()
    lines = [
        "# HELP lm_fleet_nodes_online Online nodes.",
        "# TYPE lm_fleet_nodes_online gauge",
        f"lm_fleet_nodes_online {snap['summary']['nodes_online']}",
        "# HELP lm_fleet_nodes_idle Idle nodes.",
        "# TYPE lm_fleet_nodes_idle gauge",
        f"lm_fleet_nodes_idle {snap['summary']['nodes_idle']}",
        "# HELP lm_fleet_models_loaded Loaded models.",
        "# TYPE lm_fleet_models_loaded gauge",
        f"lm_fleet_models_loaded {snap['summary']['models_loaded']}",
    ]
    for node in snap["nodes"]:
        labels = f'node="{node["name"]}",role="{node["role"]}"'
        lines.append(f"lm_fleet_node_online{{{labels}}} {1 if node['online'] else 0}")
        lines.append(f"lm_fleet_node_idle{{{labels}}} {1 if node['idle'] else 0}")
        lines.append(f"lm_fleet_node_in_flight{{{labels}}} {node['in_flight']}")
        if node["latency_ms"] is not None:
            lines.append(f"lm_fleet_node_probe_latency_ms{{{labels}}} {float(node['latency_ms']):.3f}")
    return "\n".join(lines) + "\n"


@app.get("/fleet/snapshot")
async def fleet_snapshot() -> dict[str, Any]:
    return router.snapshot()


@app.post("/fleet/heartbeat")
async def fleet_heartbeat() -> dict[str, Any]:
    return await router.probe_all()


@app.get("/fleet/idle")
async def fleet_idle() -> dict[str, Any]:
    return {
        "idle_nodes": router.idle_nodes(),
        "advisory": "Idle state is exact only for traffic routed through this service. Direct LM Studio traffic requires an optional node-agent/exporter to measure OS/GPU/process load.",
    }


@app.post("/fleet/quality")
async def fleet_quality(body: dict[str, Any]) -> dict[str, Any]:
    request_id = str(body.get("request_id") or "")
    score_raw = body.get("quality_score")
    score = float(score_raw) if score_raw is not None else 0.0
    if not request_id or score < 0 or score > 1:
        raise HTTPException(status_code=400, detail="request_id and quality_score in [0,1] are required")
    updated = router.update_quality(request_id, score)
    return {"updated": updated, "request_id": request_id, "quality_score": score}


@app.post("/fleet/route/score")
async def route_score(request: Request) -> dict[str, Any]:
    body = await request.json()
    requested_model = normalize_model_id(body.get("model"))
    route = str(body.get("route") or "chat_completions")
    node, model, scored = router.choose_candidate(
        requested_model=requested_model,
        route=route,
        body=body,
        client_host=request.client.host if request.client else None,
    )
    return {
        "selected": {"node": node.name, "endpoint": node.base_url, "model": model.key},
        "candidates": [s.model_dump() for s in scored[:25]],
    }


@app.get("/v1/models")
async def v1_models() -> dict[str, Any]:
    return router.openai_models()


@app.post("/v1/chat/completions", response_model=None)
async def v1_chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    return await router.proxy_openai("chat_completions", request, await request.json())


@app.post("/v1/responses", response_model=None)
async def v1_responses(request: Request) -> JSONResponse | StreamingResponse:
    return await router.proxy_openai("responses", request, await request.json())


@app.post("/v1/completions", response_model=None)
async def v1_completions(request: Request) -> JSONResponse | StreamingResponse:
    return await router.proxy_openai("completions", request, await request.json())


@app.post("/v1/embeddings", response_model=None)
async def v1_embeddings(request: Request) -> JSONResponse | StreamingResponse:
    return await router.proxy_openai("embeddings", request, await request.json())


if __name__ == "__main__":
    host = os.getenv("LM_FLEET_BIND", "0.0.0.0")
    port = int(os.getenv("LM_FLEET_PORT", "8091"))
    uvicorn.run(app, host=host, port=port, reload=False)
