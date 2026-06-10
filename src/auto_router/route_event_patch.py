from __future__ import annotations

from typing import Any, Callable

from auto_router.route_events import _provider_node_id, enqueue_route_execution_event
from auto_router.signal_registry import route_execution_signals, signal_snapshot


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
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
        queue_wait_ms: int | None = None,
        load_time_ms: int | None = None,
        tokens_per_second: float | None = None,
        value_units: int | None = None,
        value_per_second: float | None = None,
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
            started_at_ms,
            ended_at_ms,
        )
        runtime = None
        if hasattr(main_module, "_build_runtime_sample"):
            runtime = main_module._build_runtime_sample(
                request=request,
                provider=provider,
                model=model,
                stage=stage,
                estimate=estimate,
                status_code=status_code,
                latency_ms=latency_ms,
                usage=usage or {},
                error=error,
                gateway_metadata=gateway_metadata,
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
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
            started_at_ms=runtime.started_at_ms if runtime is not None else started_at_ms,
            ended_at_ms=runtime.ended_at_ms if runtime is not None else ended_at_ms,
            queue_wait_ms=runtime.queue_wait_ms if runtime is not None else queue_wait_ms,
            load_time_ms=runtime.load_time_ms if runtime is not None else load_time_ms,
            tokens_per_second=runtime.tokens_per_second if runtime is not None else tokens_per_second,
            value_units=runtime.value_units if runtime is not None else value_units,
            value_per_second=runtime.value_per_second if runtime is not None else value_per_second,
        )
        if hasattr(main_module, "state") and hasattr(main_module.state, "signal_registry"):
            node_id = _provider_node_id(main_module.state, provider)
            signals = route_execution_signals(
                request=request,
                provider=provider,
                model=model,
                stage=stage,
                status_code=status_code,
                latency_ms=latency_ms,
                error=error,
                usage=usage,
                tokens_per_second=runtime.tokens_per_second if runtime is not None else tokens_per_second,
                value_units=runtime.value_units if runtime is not None else value_units,
                value_per_second=runtime.value_per_second if runtime is not None else value_per_second,
                node_id=node_id,
                gateway_metadata=gateway_metadata,
            )
            if signals:
                main_module.state.signal_registry.save_snapshot(
                    signal_snapshot(signals, revision=f"route-execution:{request.request_id}:{stage}", source="route_execution")
                )
                main_module.state.context = main_module.state.signal_registry.hydrate_context(main_module.state.context)
                if hasattr(main_module.state, "policy_engine"):
                    main_module.state.policy_engine.context = main_module.state.context

    main_module._record_usage = patched_record_usage
    main_module._route_event_patch_installed = True



