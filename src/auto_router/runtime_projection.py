from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from auto_router.access_paths import RuntimeAccessPathSelector
from auto_router.admission import RuntimeAdmissionController
from auto_router.config import (
    ProviderRegistry,
    _project_live_models,
    load_context_snapshot_async,
)
from auto_router.models import ProviderConfig
from auto_router.offline_guard import host_is_offline_allowed, strict_offline_enabled
from auto_router.policy import PolicyEngine
from auto_router.settings import get_settings


_ALLOWED_PROVIDER_TYPES = {
    "lmstudio",
    "llama_cpp",
    "llamacpp",
    "openai_compatible",
    "sglang",
    "vllm",
}
_UNKNOWN = {"", "unknown", "unresolved", "none", "null"}


class RuntimeProjectionDocument(BaseModel):
    schema_version: Literal["1"] = "1"
    source: Literal["assistx"] = "assistx"
    generation: int = Field(ge=1)
    revision: str = Field(min_length=1, max_length=300)
    generated_at_ms: int = Field(gt=0)
    expires_at_ms: int = Field(gt=0)
    providers: list[ProviderConfig] = Field(min_length=1)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass
class RuntimeGeneration:
    generation: int
    revision: str
    checksum: str
    generated_at_ms: int
    expires_at_ms: int
    applied_at_ms: int
    providers: ProviderRegistry
    admission: RuntimeAdmissionController
    access_paths: RuntimeAccessPathSelector

    def is_fresh(self, now_ms: int | None = None) -> bool:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        return self.expires_at_ms > now

    def status(self, now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        return {
            "generation": self.generation,
            "revision": self.revision,
            "checksum": self.checksum,
            "generated_at_ms": self.generated_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "expires_in_ms": max(0, self.expires_at_ms - now),
            "fresh": self.expires_at_ms > now,
            "applied_at_ms": self.applied_at_ms,
            "providers": [provider.name for provider in self.providers.enabled()],
            "admission": self.admission.snapshot(),
            "access_paths": self.access_paths.snapshot(),
        }


def _canonical_configuration(document: dict[str, Any]) -> bytes:
    payload = {
        key: value
        for key, value in document.items()
        if key
        not in {
            "checksum",
            "signature",
            "generated_at_ms",
            "expires_at_ms",
        }
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def projection_checksum(document: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_configuration(document)).hexdigest()


def projection_signature(
    generation: int,
    checksum: str,
    generated_at_ms: int,
    expires_at_ms: int,
    secret: str,
) -> str:
    message = (
        f"{generation}:{checksum}:{generated_at_ms}:{expires_at_ms}"
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _known(value: Any) -> bool:
    return str(value or "").strip().lower() not in _UNKNOWN


def _validate_private_url(label: str, value: str) -> list[str]:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return [f"{label} must be a valid http(s) URL"]
    if not host_is_offline_allowed(parsed.hostname):
        return [
            f"{label} host {parsed.hostname!r} is not allowed in strict offline mode"
        ]
    return []


def validate_projection_document(
    payload: dict[str, Any],
    *,
    secret: str,
    now_ms: int | None = None,
) -> RuntimeProjectionDocument:
    if not strict_offline_enabled():
        raise ValueError("runtime projection is supported only in strict offline mode")
    if not secret.strip():
        raise ValueError("runtime projection HMAC secret is required")
    if not isinstance(payload, dict):
        raise ValueError("runtime projection must be a JSON object")

    received_checksum = str(payload.get("checksum") or "")
    received_signature = str(payload.get("signature") or "")
    expected_checksum = projection_checksum(payload)
    if not hmac.compare_digest(received_checksum, expected_checksum):
        raise ValueError("runtime projection checksum mismatch")
    try:
        raw_generation = int(payload.get("generation"))
        raw_generated_at_ms = int(payload.get("generated_at_ms"))
        raw_expires_at_ms = int(payload.get("expires_at_ms"))
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime projection generation or timestamps are invalid") from exc
    expected_signature = projection_signature(
        raw_generation,
        received_checksum,
        raw_generated_at_ms,
        raw_expires_at_ms,
        secret,
    )
    if not hmac.compare_digest(received_signature, expected_signature):
        raise ValueError("runtime projection signature mismatch")

    document = RuntimeProjectionDocument.model_validate(payload)
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    max_future_ms = int(
        os.getenv("AUTO_ROUTER_PROJECTION_CLOCK_SKEW_MS", "300000")
    )
    max_ttl_ms = int(os.getenv("AUTO_ROUTER_PROJECTION_MAX_TTL_MS", "900000"))
    if document.generated_at_ms > now + max_future_ms:
        raise ValueError("runtime projection generated_at_ms is too far in the future")
    if document.expires_at_ms <= now:
        raise ValueError("runtime projection is expired")
    if document.expires_at_ms <= document.generated_at_ms:
        raise ValueError("runtime projection expiry must be after issuance")
    if document.expires_at_ms - document.generated_at_ms > max_ttl_ms:
        raise ValueError("runtime projection TTL exceeds the configured maximum")

    errors: list[str] = []
    runtime_capacity: dict[str, tuple[int, int, float]] = {}
    provider_names: set[str] = set()
    model_instances: set[str] = set()
    enabled_count = 0

    for provider in document.providers:
        if provider.name in provider_names:
            errors.append(f"duplicate provider name {provider.name!r}")
        provider_names.add(provider.name)
        if not provider.enabled:
            continue
        enabled_count += 1
        provider_type = provider.type.strip().lower()
        if provider_type not in _ALLOWED_PROVIDER_TYPES:
            errors.append(
                f"{provider.name}: provider type {provider.type!r} is not allowed"
            )
        if str(provider.quota_class).strip().lower() != "local":
            errors.append(f"{provider.name}: quota_class must be local")
        if provider.gateway_managed:
            errors.append(f"{provider.name}: gateway_managed is forbidden")
        for field_name, value in (
            ("node_id", provider.node_id),
            ("runtime_instance_id", provider.runtime_instance_id),
            ("runtime_kind", provider.runtime_kind),
            ("runtime_version", provider.runtime_version),
        ):
            if not _known(value):
                errors.append(f"{provider.name}: {field_name} must be resolved")
        if provider.parallel_slots <= 0:
            errors.append(f"{provider.name}: parallel_slots must be positive")
        errors.extend(
            _validate_private_url(f"{provider.name}.base_url", provider.base_url)
        )
        access_urls = [url for url in provider.access_urls if str(url).strip()]
        if not access_urls:
            errors.append(
                f"{provider.name}: at least one approved access URL is required"
            )
        for index, url in enumerate(access_urls):
            errors.extend(
                _validate_private_url(
                    f"{provider.name}.access_urls[{index}]",
                    str(url),
                )
            )
        runtime_id = str(provider.runtime_instance_id or "")
        capacity = (
            int(provider.parallel_slots),
            int(provider.queue_limit),
            float(provider.queue_timeout_seconds),
        )
        previous_capacity = runtime_capacity.get(runtime_id)
        if previous_capacity is not None and previous_capacity != capacity:
            errors.append(
                f"{provider.name}: conflicting capacity for runtime {runtime_id}"
            )
        runtime_capacity[runtime_id] = capacity
        if not provider.models:
            errors.append(f"{provider.name}: at least one loaded model is required")
        for model in provider.models:
            for field_name, value in (
                ("alias", model.alias),
                ("provider_model", model.provider_model),
                ("model_instance_id", model.model_instance_id),
                ("artifact_fingerprint", model.artifact_fingerprint),
                ("quantization", model.quantization),
            ):
                if not _known(value):
                    errors.append(
                        f"{provider.name}/{model.alias}: {field_name} must be resolved"
                    )
            if not model.context_window or model.context_window <= 0:
                errors.append(
                    f"{provider.name}/{model.alias}: context_window must be positive"
                )
            model_instance_id = str(model.model_instance_id or "")
            if model_instance_id in model_instances:
                errors.append(
                    f"duplicate model_instance_id {model_instance_id!r}"
                )
            model_instances.add(model_instance_id)

    if enabled_count == 0:
        errors.append("runtime projection must contain at least one enabled provider")
    if errors:
        raise ValueError("; ".join(errors))
    return document


class RuntimeProjectionManager:
    """Validate and atomically replace the router's AssistX-approved runtime state."""

    def __init__(self, state: Any) -> None:
        self.state = state
        self._lock = asyncio.Lock()
        self.current: RuntimeGeneration | None = None
        self.retired: list[RuntimeGeneration] = []
        self.last_error = ""
        self.last_attempt_at_ms = 0

    def _secret(self) -> str:
        return os.getenv(
            "AUTO_ROUTER_RUNTIME_PROJECTION_HMAC_SECRET",
            "",
        ).strip()

    def assert_current_fresh(self, now_ms: int | None = None) -> RuntimeGeneration:
        current = self.current
        if current is None:
            message = "signed AssistX runtime projection is not configured"
            self.last_error = message
            raise RuntimeError(message)
        if not current.is_fresh(now_ms):
            message = (
                f"signed AssistX runtime projection generation "
                f"{current.generation} is expired"
            )
            self.last_error = message
            raise RuntimeError(message)
        return current

    def _refresh_same_generation(
        self,
        document: RuntimeProjectionDocument,
    ) -> dict[str, Any]:
        assert self.current is not None
        if document.checksum != self.current.checksum:
            raise ValueError("runtime projection generation checksum conflict")
        self.current.generated_at_ms = document.generated_at_ms
        self.current.expires_at_ms = document.expires_at_ms
        self.last_error = ""
        return {
            "applied": False,
            "idempotent": True,
            "lease_refreshed": True,
            **self.status(),
        }

    async def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.last_attempt_at_ms = int(time.time() * 1000)
        document = validate_projection_document(payload, secret=self._secret())

        async with self._lock:
            if self.current is not None:
                if document.generation < self.current.generation:
                    raise ValueError(
                        "runtime projection generation rollback is forbidden"
                    )
                if document.generation == self.current.generation:
                    return self._refresh_same_generation(document)
                if document.generation != self.current.generation + 1:
                    raise ValueError(
                        "runtime projection generation must advance exactly by one"
                    )

        registry = ProviderRegistry(providers=document.providers)
        admission = RuntimeAdmissionController(registry.enabled())
        access_paths = RuntimeAccessPathSelector(
            registry.enabled(),
            cache_ttl_seconds=float(
                os.getenv("AUTO_ROUTER_ACCESS_PATH_TTL_SECONDS", "15")
            ),
            probe_timeout_seconds=float(
                os.getenv("AUTO_ROUTER_ACCESS_PATH_PROBE_TIMEOUT_SECONDS", "2")
            ),
        )
        settings = get_settings()
        context = await load_context_snapshot_async(
            settings.context_config,
            registry,
            self.state.agents,
        )
        if hasattr(self.state, "model_registry"):
            context = _project_live_models(
                context,
                registry,
                self.state.model_registry.latest_inventory(),
            )
        policy_engine = PolicyEngine(
            registry,
            self.state.policies,
            settings.default_profile,
            context,
        )
        prepared = RuntimeGeneration(
            generation=document.generation,
            revision=document.revision,
            checksum=document.checksum,
            generated_at_ms=document.generated_at_ms,
            expires_at_ms=document.expires_at_ms,
            applied_at_ms=int(time.time() * 1000),
            providers=registry,
            admission=admission,
            access_paths=access_paths,
        )

        async with self._lock:
            if self.current is not None:
                if document.generation < self.current.generation:
                    raise ValueError(
                        "runtime projection generation rollback is forbidden"
                    )
                if document.generation == self.current.generation:
                    return self._refresh_same_generation(document)
                if document.generation != self.current.generation + 1:
                    raise ValueError(
                        "runtime projection generation must advance exactly by one"
                    )
                self.retired.append(self.current)

            # Existing leases retain their exact old gate object. New requests see
            # this generation atomically after every replacement component is ready.
            self.state.providers = registry
            self.state.context = context
            self.state.policy_engine = policy_engine
            self.state.admission = admission
            self.state.access_paths = access_paths
            self.current = prepared
            self.last_error = ""
            self._prune_retired()

        try:
            if hasattr(self.state, "signal_registry"):
                await asyncio.to_thread(
                    self.state.signal_registry.save_snapshot,
                    context,
                )
        except Exception:
            pass
        return {
            "applied": True,
            "idempotent": False,
            "lease_refreshed": False,
            **self.status(),
        }

    def _prune_retired(self) -> None:
        retained: list[RuntimeGeneration] = []
        for generation in self.retired[-16:]:
            snapshots = generation.admission.snapshot()
            if any(
                int(item.get("active") or 0) > 0
                or int(item.get("queued") or 0) > 0
                for item in snapshots
            ):
                retained.append(generation)
        self.retired = retained

    def status(self) -> dict[str, Any]:
        self._prune_retired()
        now = int(time.time() * 1000)
        return {
            "configured": self.current is not None,
            "fresh": bool(self.current and self.current.is_fresh(now)),
            "current": self.current.status(now) if self.current else None,
            "retired_generations": [item.status(now) for item in self.retired],
            "last_attempt_at_ms": self.last_attempt_at_ms,
            "last_error": self.last_error,
        }


async def projection_poll_task(
    state: Any,
    manager: RuntimeProjectionManager,
) -> None:
    del state
    url = os.getenv("AUTO_ROUTER_RUNTIME_PROJECTION_URL", "").strip()
    if not url:
        return
    interval = max(
        1.0,
        float(os.getenv("AUTO_ROUTER_RUNTIME_PROJECTION_INTERVAL_SECONDS", "5")),
    )
    timeout = max(
        0.5,
        float(os.getenv("AUTO_ROUTER_RUNTIME_PROJECTION_TIMEOUT_SECONDS", "10")),
    )
    username = os.getenv("AUTO_ROUTER_ASSISTX_BASIC_AUTH_USER", "").strip()
    password = os.getenv("AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS", "").strip()
    auth = httpx.BasicAuth(username, password) if username or password else None
    while True:
        try:
            async with httpx.AsyncClient(timeout=timeout, auth=auth) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
            await manager.apply(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            manager.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            manager.last_attempt_at_ms = int(time.time() * 1000)
        await asyncio.sleep(interval)
