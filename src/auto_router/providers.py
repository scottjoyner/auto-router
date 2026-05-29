from __future__ import annotations

import email.utils
import os
<<<<<<< Updated upstream
import time
from typing import Any
=======
from dataclasses import dataclass
from typing import Any, AsyncIterator
>>>>>>> Stashed changes

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
    """Minimal OpenAI-compatible provider adapter.

    This intentionally supports LM Studio and most hosted providers that expose `/v1` routes.
    Provider-specific adapters can subclass this when custom headers, paths, or response handling
    become necessary.
    """

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
                response = await client.get(f"{self.config.base_url.rstrip('/')}/models", headers=self._headers())
            if response.status_code < 500:
                return ProviderHealth(provider=self.name, ok=True, detail=f"HTTP {response.status_code}")
            return ProviderHealth(provider=self.name, ok=False, detail=f"HTTP {response.status_code}")
        except Exception as exc:  # pragma: no cover - network dependent
            return ProviderHealth(provider=self.name, ok=False, detail=str(exc))

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
        usage = data.get("usage") if isinstance(data, dict) else None
        return ProviderResponse(
            provider=self.name,
            model=provider_model,
            data=data,
            usage=usage or {},
            status_code=response.status_code,
        )

    async def _stream_post(
        self,
        path: str,
        payload: dict[str, Any],
        provider_model: str,
    ) -> ProviderStreamResponse:
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
            )

        async def body_stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await stream_cm.__aexit__(None, None, None)
                await client.aclose()

        headers = {key.lower(): value for key, value in response.headers.items()}
        return ProviderStreamResponse(
            provider=self.name,
            model=provider_model,
            status_code=response.status_code,
            headers=headers,
            body=body_stream(),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self.config.headers)
        if self.config.api_key_env:
            api_key = os.getenv(self.config.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        return headers


<<<<<<< Updated upstream
def parse_retry_after(headers: httpx.Headers | dict[str, str]) -> int | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(int(float(value)), 0)
    except ValueError:
        parsed = email.utils.parsedate_to_datetime(value)
        return max(int(parsed.timestamp() - time.time()), 0)
=======
class LMStudioProvider(OpenAICompatibleProvider):
    """Specialized provider for LM Studio that uses native APIs for richer health info."""

    async def health(self) -> ProviderHealth:
        base_url = self.config.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            native_root = base_url[:-3].rstrip("/")
        else:
            native_root = base_url

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Use native API to get loaded status and capabilities
                response = await client.get(f"{native_root}/api/v1/models", headers=self._headers())
            
            if response.status_code == 200:
                data = response.json()
                models = data.get("models") or []
                loaded_count = 0
                total_loaded_ctx = 0
                loaded_models = []

                for m in models:
                    instances = m.get("loaded_instances") or []
                    if instances:
                        loaded_count += 1
                        model_id = m.get("id") or m.get("key")
                        ctx = instances[0].get("config", {}).get("context_length", 0)
                        total_loaded_ctx += ctx
                        loaded_models.append({
                            "id": model_id,
                            "context_length": ctx,
                            "gpu": any(inst.get("config", {}).get("gpu") for inst in instances)
                        })

                detail = f"online, {loaded_count} models loaded"
                if loaded_count > 0:
                    detail += f" ({total_loaded_ctx//1024}k total ctx)"

                return ProviderHealth(
                    provider=self.name,
                    ok=True,
                    detail=detail,
                    metadata={
                        "type": "lmstudio",
                        "loaded_models": loaded_models,
                        "raw_model_count": len(models),
                    }
                )
            
            # Fallback to standard /v1/models if native API fails
            return await super().health()
        except Exception as exc:
            return ProviderHealth(provider=self.name, ok=False, detail=str(exc))
>>>>>>> Stashed changes


def build_provider(config: ProviderConfig, timeout_seconds: float = 120.0) -> OpenAICompatibleProvider:
    if config.type == "lmstudio":
        return LMStudioProvider(config, timeout_seconds=timeout_seconds)
    return OpenAICompatibleProvider(config, timeout_seconds=timeout_seconds)
