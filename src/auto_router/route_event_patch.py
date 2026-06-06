from __future__ import annotations

from typing import Any, Callable

from auto_router.route_events import enqueue_route_execution_event


def install_route_event_patch(main_module: Any) -> None:
    """Patch the base app's usage recorder to also enqueue route events.

    The base `auto_router.main` app stays minimal. The enhanced
    `auto_router.main_live` wrapper installs this patch so production runs get
    route provenance outbox events without needing a large rewrite of the core
    request execution loop.
    """

    if getattr(main_module, "_route_event_patch_installed", False):
        return

    original: Callable[..., None] = main_module._record_usage

    def patched_record_usage(
        request: Any,
        provider: str,
        model: str,
        stage: str,
        estimate: Any,
        status_code: int | None,
        latency_ms: int,
        usage: dict[str, int] | None = None,
        error: Exception | None = None,
        gateway_metadata: dict[str, Any] | None = None,
    ) -> None:
        original(
            request,
            provider,
            model,
            stage,
            estimate,
            status_code,
            latency_ms,
            usage,
            error,
            gateway_metadata,
        )
        enqueue_route_execution_event(
            main_module.state,
            request=request,
            provider=provider,
            model=model,
            stage=stage,
            estimate=estimate,
            status_code=status_code,
            latency_ms=latency_ms,
            usage=usage,
            error=error,
            gateway_metadata=gateway_metadata,
        )

    main_module._record_usage = patched_record_usage
    main_module._route_event_patch_installed = True
