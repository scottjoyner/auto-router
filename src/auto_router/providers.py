from __future__ import annotations

import os
from typing import Any

import httpx

from auto_router.models import ProviderConfig, ProviderHealth, ProviderResponse, RouterRequest


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


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
        return await self._post("/chat/completions", payload, request, provider_model)

    async def responses(self, request: RouterRequest, provider_model: str) -> ProviderResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._post("/responses", payload, request, provider_model)

    async def embeddings(self, request: RouterRequest, provider_model: str) -> ProviderResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._post("/embeddings", payload, request, provider_model)

    async def completions(self, request: RouterRequest, provider_model: str) -> ProviderResponse:
        payload = dict(request.raw_body)
        payload["model"] = provider_model
        return await self._post("/completions", payload, request, provider_model)

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        request: RouterRequest,
        provider_model: str,
    ) -> ProviderResponse:
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

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self.config.headers)
        if self.config.api_key_env:
            api_key = os.getenv(self.config.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        return headers


def build_provider(config: ProviderConfig, timeout_seconds: float = 120.0) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(config, timeout_seconds=timeout_seconds)
