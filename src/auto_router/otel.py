from __future__ import annotations

"""Minimal OpenTelemetry tracer bootstrap for auto-router.

The SDK is engaged only when ``AUTO_ROUTER_OTEL_ENABLED=true`` (LLD §3.5 W-60).
When disabled (the default) or when the ``opentelemetry-api`` package is not
installed, this is a no-op so imports and startup never break.
"""

from typing import Any

from auto_router.settings import get_settings

_tracer: Any | None = None
_enabled: bool = False


def get_tracer(name: str = "auto-router") -> Any:
    """Return the active tracer, or a no-op shim when OTEL is disabled/unavailable."""
    if _tracer is not None:
        return _tracer
    return _NoopTracer()


def init_otel() -> bool:
    """Initialize the global OTEL tracer if enabled.

    Returns True if a real tracer was wired up, False otherwise.
    """
    global _tracer, _enabled
    settings = get_settings()
    if not getattr(settings, "otel_enabled", False):
        _enabled = False
        return False
    try:  # pragma: no cover - depends on optional SDK
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )

        provider = TracerProvider(resource=Resource.create({"service.name": "auto-router"}))
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("auto-router")
        _enabled = True
        return True
    except Exception as exc:  # pragma: no cover - defensive
        print(f"WARNING: OTEL enabled but init failed ({exc}); tracing disabled.")
        _enabled = False
        return False


class _NoopTracer:
    """Fallback tracer used when OTEL is disabled or the SDK is missing."""

    def start_as_current_span(self, *args: Any, **kwargs: Any) -> Any:
        from contextlib import nullcontext

        return nullcontext()
