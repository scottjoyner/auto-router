from __future__ import annotations

import email.utils
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from auto_router.models import ProviderConfig, ProviderHealth, ProviderResponse, RouterRequest


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.headers = headers or {}


@dataclass
class ProviderStreamResponse:
    provider: str
    model: str
    status_code: int
    headers: dict[str, str]
    body: AsyncIterator[bytes]


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible provider adapter."""

    def __init__(self, config: ProviderConfig, timeout_seconds: float = 120.0):
        self.config = config
        self.timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return self.config.name

    def is_configured(self) -> bool:
        if self.config.type == "lmstudio":
            return True
        if not self.config.api_key_env:
            return True
        return bool(os.getenv(self.config.api_key_env))

    async def health(self) -> ProviderHealth:
        if not self.is_configured():
            return ProviderHealth(provider=self.name, ok=False, detail="missing API key")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.config.base_url.rstrip('/')}/models",
                    headers=self._headers(),
                )
            if response.status_code < 500:
                return ProviderHealth(provider=self.name, ok=True, detail=f"HTTP {response.status_code}")
            return ProviderHealth(provider=self.name, ok=False, detail=f"HTTP {response.status_code}")
        except Exception as exc:  # pragma: no cover - network dependent
            return ProviderHealth(provider=self.name, ok=False, detail=str(exc))

    async def list_models(self) -> list[dict[str, Any]]:
        if not self.is_configured():
            raise ProviderError(f"provider {self.name} is not configured", retryable=True)
        url = f"{self.config.base_url.rstrip('/')}/models"
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 30.0)) as client:
                response = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider {self.name} model discovery failed: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"provider {self.name} model discovery returned HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                retryable=response.status_code in {408, 409, 425, 429, 500, 502, 503, 504},
                retry_after_seconds=parse_retry_after(response.headers),
                headers={key.lower(): value for key, value in response.headers.items()},
            )
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        return [normalize_model_record(item) for item in data if isinstance(item, dict)]

    async def chat_completions(self, request: RouterRequest, provider_model: str) -> ProviderResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._post("/chat/completions", payload, provider_model)

    async def responses(self, request: RouterRequest, provider_model: str) -> ProviderResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._post("/responses", payload, provider_model)

    async def embeddings(self, request: RouterRequest, provider_model: str) -> ProviderResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._post("/embeddings", payload, provider_model)

    async def completions(self, request: RouterRequest, provider_model: str) -> ProviderResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._post("/completions", payload, provider_model)

    async def stream_chat_completions(self, request: RouterRequest, provider_model: str) -> ProviderStreamResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._stream_post("/chat/completions", payload, provider_model)

    async def stream_responses(self, request: RouterRequest, provider_model: str) -> ProviderStreamResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._stream_post("/responses", payload, provider_model)

    async def stream_completions(self, request: RouterRequest, provider_model: str) -> ProviderStreamResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._stream_post("/completions", payload, provider_model)

    async def _post(self, path: str, payload: dict[str, Any], provider_model: str) -> ProviderResponse:
        if not self.is_configured():
            raise ProviderError(f"provider {self.name} is not configured", retryable=True)
        url = f"{self.config.base_url.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider {self.name} request failed: {exc}") from exc
        if response.status_code >= 400:
            retryable = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
            raise ProviderError(
                f"provider {self.name} returned HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                retryable=retryable,
                retry_after_seconds=parse_retry_after(response.headers),
                headers={key.lower(): value for key, value in response.headers.items()},
            )
        data = response.json()
        usage = _normalize_usage(data.get("usage") if isinstance(data, dict) else None)
        return ProviderResponse(provider=self.name, model=provider_model, data=data, usage=usage, status_code=response.status_code)

    async def _stream_post(self, path: str, payload: dict[str, Any], provider_model: str) -> ProviderStreamResponse:
        if not self.is_configured():
            raise ProviderError(f"provider {self.name} is not configured", retryable=True)
        url = f"{self.config.base_url.rstrip('/')}{path}"
        client = httpx.AsyncClient(timeout=self.timeout_seconds)
        stream_cm = client.stream("POST", url, headers=self._headers(), json=payload)
        try:
            response = await stream_cm.__aenter__()
        except Exception as exc:
            await client.aclose()
            raise ProviderError(f"provider {self.name} request failed: {exc}") from exc
        if response.status_code >= 400:
            body = await response.aread()
            await stream_cm.__aexit__(None, None, None)
            await client.aclose()
            retryable = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
            raise ProviderError(
                f"provider {self.name} returned HTTP {response.status_code}: {body.decode('utf-8', errors='replace')[:500]}",
                status_code=response.status_code,
                retryable=retryable,
                retry_after_seconds=parse_retry_after(response.headers),
                headers={key.lower(): value for key, value in response.headers.items()},
            )

        async def body_stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await stream_cm.__aexit__(None, None, None)
                await client.aclose()

        headers = {key.lower(): value for key, value in response.headers.items()}
        return ProviderStreamResponse(provider=self.name, model=provider_model, status_code=response.status_code, headers=headers, body=body_stream())

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self.config.headers)
        if self.config.api_key_env:
            api_key = os.getenv(self.config.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        return headers


def normalize_model_record(item: dict[str, Any]) -> dict[str, Any]:
    model_id = item.get("id") or item.get("key") or item.get("name") or item.get("display_name") or item.get("model")
    return {
        "id": model_id,
        "object": item.get("object", "model"),
        "owned_by": item.get("owned_by") or item.get("publisher") or item.get("owner"),
        "created": item.get("created"),
        "raw": item,
    }


def _normalize_usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    normalized: dict[str, int] = {}
    for key in ("prompt_tokens", "input_tokens", "completion_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        try:
            if value is not None:
                normalized[key] = int(value)
        except (TypeError, ValueError):
            continue
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("reasoning_tokens")
        if not isinstance(reasoning, bool):
            try:
                if reasoning is not None:
                    normalized["reasoning_tokens"] = int(reasoning)
            except (TypeError, ValueError):
                pass
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens")
        if not isinstance(cached, bool):
            try:
                if cached is not None:
                    normalized["cached_tokens"] = int(cached)
            except (TypeError, ValueError):
                pass
    return normalized


def parse_retry_after(headers: httpx.Headers | dict[str, str]) -> int | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(int(float(value)), 0)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return max(int(parsed.timestamp() - time.time()), 0)
        except Exception:
            return None


class LMStudioProvider(OpenAICompatibleProvider):
    async def list_models(self) -> list[dict[str, Any]]:
        if not self.is_configured():
            raise ProviderError(f"provider {self.name} is not configured", retryable=True)
        native_root = self._lmstudio_native_root()
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 30.0)) as client:
                response = await client.get(f"{native_root}/api/v1/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider {self.name} model discovery failed: {exc}") from exc
        if response.status_code >= 400:
            return await super().list_models()
        payload = response.json()
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return []
        records: list[dict[str, Any]] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            loaded_instances = item.get("loaded_instances") or []
            loaded = bool(loaded_instances)
            first_loaded = loaded_instances[0] if loaded_instances else {}
            config = first_loaded.get("config") if isinstance(first_loaded, dict) else {}
            context_length = None
            if isinstance(config, dict):
                context_length = config.get("context_length")
            record = normalize_model_record(item)
            record.update(
                {
                    "loaded": loaded,
                    "loaded_instances": loaded_instances,
                    "context_length": context_length,
                    "gpu": any(isinstance(instance, dict) and instance.get("config", {}).get("gpu") for instance in loaded_instances),
                    "source": "lmstudio_native",
                    "endpoint": native_root,
                }
            )
            records.append(record)
        return records

    async def health(self) -> ProviderHealth:
        native_root = self._lmstudio_native_root()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{native_root}/api/v1/models", headers=self._headers())
            if response.status_code == 200:
                data = response.json()
                models = data.get("models") or []
                loaded_count = 0
                total_loaded_ctx = 0
                loaded_models: list[dict[str, Any]] = []
                for model in models:
                    instances = model.get("loaded_instances") or []
                    if not instances:
                        continue
                    loaded_count += 1
                    model_id = model.get("id") or model.get("key")
                    ctx = instances[0].get("config", {}).get("context_length", 0)
                    total_loaded_ctx += ctx
                    loaded_models.append(
                        {
                            "id": model_id,
                            "context_length": ctx,
                            "gpu": any(instance.get("config", {}).get("gpu") for instance in instances),
                            "loaded_instances": instances,
                        }
                    )
                detail = f"online, {loaded_count} models loaded"
                if loaded_count > 0:
                    detail += f" ({total_loaded_ctx // 1024}k total ctx)"
                return ProviderHealth(provider=self.name, ok=True, detail=detail, metadata={"type": "lmstudio", "loaded_models": loaded_models, "raw_model_count": len(models)})
            return await super().health()
        except Exception:
            return await super().health()

    def _lmstudio_native_root(self) -> str:
        base_url = self.config.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return base_url[:-3].rstrip("/")
        if base_url.endswith("/api/v1"):
            return base_url[:-7].rstrip("/")
        return base_url


def build_provider(config: ProviderConfig, timeout_seconds: float = 120.0) -> OpenAICompatibleProvider:
    """Build provider instance with optional gateway sidecar routing.
    
    Args:
        config: Provider configuration.
        timeout_seconds: Request timeout in seconds.
    
    Returns:
        Provider instance (direct or through agentgateway).
    """
    from auto_router.gateway_config import GatewayConfig
    
    # Check if gateway mode is enabled and provider should use it
    gateway_config = GatewayConfig.from_env()
    uses_gateway = (
        gateway_config.enabled 
        and gateway_config.mode == "sidecar"
        and getattr(config, "gateway_managed", False)  # type: ignore[attr-defined]
    )
    
    if config.type == "lmstudio":
        return LMStudioProvider(config, timeout_seconds=timeout_seconds)
    
    if uses_gateway:
        return AgentGatewayProviderAdapter(config, timeout_seconds=timeout_seconds)
    
    return OpenAICompatibleProvider(config, timeout_seconds=timeout_seconds)


class AgentGatewayProviderAdapter(OpenAICompatibleProvider):
    """Provider adapter for agentgateway sidecar.
    
    Extends OpenAICompatibleProvider to add gateway-specific header/body metadata
    and privacy enforcement before routing through the sidecar.
    """
    
    provider_kind = "agentgateway"
    
    async def chat_completions(
        self, 
        request: RouterRequest, 
        provider_model: str,
        gateway_config=None,
        route_plan=None,
    ) -> ProviderResponse:
        """Send chat completion through agentgateway with metadata headers.
        
        Args:
            request: RouterRequest with raw_body payload.
            provider_model: Model identifier (may be auto/* alias).
            gateway_config: GatewayConfig instance from env.
            route_plan: Route plan with privacy/quota info.
        
        Returns:
            ProviderResponse with normalized response data.
        """
        from auto_router.gateway import (
            build_gateway_headers,
            attach_gateway_metadata,
            strip_gateway_metadata,
            generate_request_id,
        )
        
        if gateway_config is None:
            from auto_router.gateway_config import GatewayConfig
            gateway_config = GatewayConfig.from_env()
        
        # Generate request ID for tracing
        request_id = generate_request_id()
        
        # Build headers with route metadata
        privacy = str(getattr(route_plan, "privacy", "cloud_allowed") if route_plan else "cloud_allowed")
        profile = str(getattr(route_plan, "profile", "auto/default") if route_plan else "auto/default")
        stage = str(getattr(route_plan, "stage", "refine") if route_plan else "refine")
        priority = str(getattr(route_plan, "priority", "normal") if route_plan else "normal")
        quota_mode = str(getattr(route_plan, "quota_mode", "balanced") if route_plan else "balanced")
        task_id = str(getattr(route_plan, "task_id", None) or getattr(request, "task_id", None) or "") or None
        agent_run_id = str(getattr(route_plan, "agent_run_id", None) or getattr(request, "agent_run_id", None) or "") or None
        node_id = str(getattr(route_plan, "node_id", None) or getattr(request, "node_id", None) or "") or None
        context_revision = getattr(route_plan, "context_revision", None)
        
        headers = build_gateway_headers(
            request_id=request_id,
            profile=profile,
            stage=stage,
            priority=priority,
            privacy=privacy,
            quota_mode=quota_mode,
            provider_plan=self.config.id or "agentgateway-sidecar",
            model_plan=provider_model,
            context_revision=context_revision,
            task_id=task_id,
            agent_run_id=agent_run_id,
            node_id=node_id,
            fallback_allowed=gateway_config.fail_open_to_direct,
        )
        
        # Normalize payload and add gateway metadata if enabled
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        
        if gateway_config.pass_metadata_in_body:
            payload = attach_gateway_metadata(
                payload,
                request_id=request_id,
                profile=profile,
                stage=stage,
                priority=priority,
                privacy=privacy,
                quota_mode=quota_mode,
                task_id=task_id,
                agent_run_id=agent_run_id,
                node_id=node_id,
                fallback_allowed=gateway_config.fail_open_to_direct,
            )
        
        # Send to gateway
        url = f"{gateway_config.openai_base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=gateway_config.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"agentgateway request failed: {exc}",
                retryable=True,
            ) from exc
        
        if response.status_code >= 400:
            retryable = response.status_code in {408, 429, 500, 502, 503, 504}
            raise ProviderError(
                f"agentgateway returned HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                retryable=retryable,
                headers={key.lower(): value for key, value in response.headers.items()},
            )
        
        data = response.json()
        usage = data.get("usage") if isinstance(data, dict) else {}
        
        # Store gateway metadata in response for ledger
        data["_gateway_metadata"] = {
            "request_id": request_id,
            "provider": self.config.id or "agentgateway-sidecar",
            "profile": profile,
            "stage": stage,
            "privacy": privacy,
            "quota_mode": quota_mode,
            "latency_ms": int(response.elapsed.total_seconds() * 1000),
        }
        usage = _normalize_usage(data.get("usage") if isinstance(data, dict) else None)
        return ProviderResponse(
            provider=self.config.id or self.name or "agentgateway-sidecar",
            model=provider_model,
            data=data,
            usage=usage,
            status_code=response.status_code,
        )

    async def stream_chat_completions(
        self, 
        request: RouterRequest, 
        provider_model: str,
        gateway_config=None,
        route_plan=None,
    ) -> ProviderStreamResponse:
        """Send streaming chat completion through agentgateway.
        
        Args:
            request: RouterRequest with raw_body payload.
            provider_model: Model identifier (may be auto/* alias).
            gateway_config: GatewayConfig instance from env.
            route_plan: Route plan with privacy/quota info.
        
        Returns:
            ProviderStreamResponse with streaming body.
        """
        from auto_router.gateway import build_gateway_headers, generate_request_id
        
        if gateway_config is None:
            from auto_router.gateway_config import GatewayConfig
            gateway_config = GatewayConfig.from_env()
        
        request_id = generate_request_id()
        privacy = str(getattr(route_plan, "privacy", "cloud_allowed") if route_plan else "cloud_allowed")
        profile = str(getattr(route_plan, "profile", "auto/default") if route_plan else "auto/default")
        stage = str(getattr(route_plan, "stage", "refine") if route_plan else "refine")
        priority = str(getattr(route_plan, "priority", "normal") if route_plan else "normal")
        quota_mode = str(getattr(route_plan, "quota_mode", "balanced") if route_plan else "balanced")
        task_id = str(getattr(route_plan, "task_id", None) or getattr(request, "task_id", None) or "") or None
        agent_run_id = str(getattr(route_plan, "agent_run_id", None) or getattr(request, "agent_run_id", None) or "") or None
        node_id = str(getattr(route_plan, "node_id", None) or getattr(request, "node_id", None) or "") or None
        context_revision = getattr(route_plan, "context_revision", None)
        
        headers = build_gateway_headers(
            request_id=request_id,
            profile=profile,
            stage=stage,
            priority=priority,
            privacy=privacy,
            quota_mode=quota_mode,
            provider_plan=self.config.id or "agentgateway-sidecar",
            model_plan=provider_model,
            context_revision=context_revision,
            task_id=task_id,
            agent_run_id=agent_run_id,
            node_id=node_id,
            fallback_allowed=gateway_config.fail_open_to_direct,
        )
        
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        
        # Send streaming request to gateway
        url = f"{gateway_config.openai_base_url}/chat/completions"
        client = httpx.AsyncClient(timeout=gateway_config.timeout_seconds)
        stream_cm = client.stream("POST", url, headers=headers, json=payload)
        
        try:
            response = await stream_cm.__aenter__()
        except Exception as exc:
            await client.aclose()
            raise ProviderError(f"agentgateway streaming request failed: {exc}") from exc
        
        if response.status_code >= 400:
            body = await response.aread()
            await stream_cm.__aexit__(None, None, None)
            await client.aclose()
            retryable = response.status_code in {408, 429, 500, 502, 503, 504}
            raise ProviderError(
                f"agentgateway returned HTTP {response.status_code}: {body.decode('utf-8', errors='replace')[:500]}",
                status_code=response.status_code,
                retryable=retryable,
                headers={key.lower(): value for key, value in response.headers.items()},
            )
        
        async def body_stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await stream_cm.__aexit__(None, None, None)
                await client.aclose()
        
        headers_dict = {key.lower(): value for key, value in response.headers.items()}
        return ProviderStreamResponse(
            provider=self.config.id or "agentgateway-sidecar",
            model=provider_model,
            status_code=response.status_code,
            headers=headers_dict,
            body=body_stream(),
        )
