from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from auto_router.settings import Settings


@dataclass
class PreflightCheck:
    name: str
    status: str
    message: str
    severity: str = "info"
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "details": self.details or {},
        }


def build_preflight_report(state: Any, settings: Settings) -> dict[str, Any]:
    checks = [
        _check_prompt_logging(settings),
        _check_provider_registry(state),
        _check_policy_registry(state),
        _check_context_projection(state, settings),
        _check_service_registry(state),
        _check_model_registry(state),
        _check_outbox(state),
        _check_memory(state, settings),
        _check_assistx(settings),
        _check_cli_discovery(state),
        _check_dry_run_posture(state),
    ]
    summary = _summarize(checks)
    return {
        "status": "ready" if summary["failed"] == 0 else "not_ready",
        "summary": summary,
        "checks": [check.to_dict() for check in checks],
    }


def _summarize(checks: list[PreflightCheck]) -> dict[str, int]:
    summary = {"total": len(checks), "passed": 0, "warned": 0, "failed": 0, "info": 0}
    for check in checks:
        if check.status == "pass":
            summary["passed"] += 1
        elif check.status == "warn":
            summary["warned"] += 1
        elif check.status == "fail":
            summary["failed"] += 1
        else:
            summary["info"] += 1
    return summary


def _check_prompt_logging(settings: Settings) -> PreflightCheck:
    if settings.log_prompts:
        return PreflightCheck(
            "prompt_logging",
            "fail",
            "Prompt logging is enabled; disable AUTO_ROUTER_LOG_PROMPTS for production.",
            "critical",
        )
    return PreflightCheck("prompt_logging", "pass", "Prompt logging is disabled.")


def _check_provider_registry(state: Any) -> PreflightCheck:
    providers = getattr(state, "providers", None)
    enabled = providers.enabled() if providers and hasattr(providers, "enabled") else []
    if not enabled:
        return PreflightCheck("providers", "fail", "No enabled providers are configured.", "critical")
    local = [provider.name for provider in enabled if provider.type == "lmstudio" or provider.quota_class == "local"]
    hosted = [provider.name for provider in enabled if provider.name not in local]
    status = "pass" if local else "warn"
    message = "Enabled providers found."
    if not local:
        message = "Enabled providers found, but no local/LM Studio fallback provider is enabled."
    return PreflightCheck(
        "providers",
        status,
        message,
        "warning" if status == "warn" else "info",
        {"enabled": [provider.name for provider in enabled], "local": local, "hosted": hosted},
    )


def _check_policy_registry(state: Any) -> PreflightCheck:
    policies = getattr(state, "policies", None)
    profiles = getattr(policies, "profiles", {}) if policies else {}
    required = {"backlog_burn", "flash_start_planner", "sophia_realtime"}
    missing = sorted(required - set(profiles.keys()))
    if missing:
        return PreflightCheck(
            "policies",
            "warn",
            "Some recommended policy profiles are missing.",
            "warning",
            {"missing": missing, "profiles": sorted(profiles.keys())},
        )
    return PreflightCheck("policies", "pass", "Recommended policy profiles are present.", details={"profiles": sorted(profiles.keys())})


def _check_context_projection(state: Any, settings: Settings) -> PreflightCheck:
    context = getattr(state, "context", None)
    if context is None:
        return PreflightCheck("context", "fail", "No context snapshot is loaded.", "critical")
    provider_count = len(getattr(context, "providers", []) or [])
    node_count = len(getattr(context, "nodes", []) or [])
    is_http = str(settings.context_config).startswith(("http://", "https://"))
    projection_status = getattr(context, "projection_status", lambda: "bootstrap")()
    projection_error = getattr(context, "projection_error", lambda: "")()
    projection_degraded = getattr(context, "is_projection_degraded", lambda: False)()
    if is_http and projection_status != "active":
        message = "AssistX projection URL is configured, but the router fell back to bootstrap."
        severity = "critical" if projection_status == "bootstrap_fallback" else "warning"
        status = "warn"
    elif is_http:
        message = "Context projection is loaded from AssistX/HTTP."
        severity = "info"
        status = "pass"
    else:
        message = "Context is loaded from local/bootstrap config, not AssistX HTTP projection."
        severity = "warning"
        status = "warn"
    details = {
        "source": getattr(context, "source", "unknown"),
        "revision": getattr(context, "revision", "unknown"),
        "providers": provider_count,
        "nodes": node_count,
        "context_config": settings.context_config,
        "projection_status": projection_status,
        "projection_error": projection_error,
        "projection_degraded": projection_degraded,
    }
    return PreflightCheck("context", status, message, severity, details)


def _check_service_registry(state: Any) -> PreflightCheck:
    context = getattr(state, "context", None)
    services = context.all_services() if context and hasattr(context, "all_services") else []
    if not services:
        return PreflightCheck("services", "warn", "No services are registered for the launchpad/scanner.", "warning")
    statuses: dict[str, int] = {}
    for service in services:
        status = getattr(service.status, "value", str(service.status))
        statuses[status] = statuses.get(status, 0) + 1
    return PreflightCheck("services", "pass", "Service registry is populated.", details={"total": len(services), "statuses": statuses})


def _check_model_registry(state: Any) -> PreflightCheck:
    registry = getattr(state, "model_registry", None)
    if registry is None:
        return PreflightCheck("model_registry", "warn", "Model registry store is not initialized; use main_live app.", "warning")
    summary = registry.summary()
    if not summary.get("providers"):
        return PreflightCheck("model_registry", "warn", "No durable model registry snapshots yet; refresh hosted models.", "warning", summary)
    return PreflightCheck("model_registry", "pass", "Model registry has durable snapshots.", details=summary)


def _check_outbox(state: Any) -> PreflightCheck:
    outbox = getattr(state, "event_outbox", None)
    if outbox is None:
        return PreflightCheck("outbox", "warn", "Event outbox is not initialized; use main_live app.", "warning")
    summary = outbox.summary()
    status = "warn" if summary.get("dead_letter", 0) else "pass"
    message = "Outbox is available."
    if status == "warn":
        message = "Outbox has dead-letter events that need operator review."
    return PreflightCheck("outbox", status, message, "warning" if status == "warn" else "info", summary)


def _check_memory(state: Any, settings: Settings) -> PreflightCheck:
    store = getattr(state, "memory_store", None)
    if not settings.memory_enabled:
        return PreflightCheck(
            "fleet_memory",
            "info",
            "Fleet experience memory is disabled.",
            details={"enabled": False},
        )
    if store is None:
        return PreflightCheck(
            "fleet_memory",
            "warn",
            "Fleet memory is enabled but the local degraded-mode store is unavailable.",
            "warning",
        )
    summary = store.summary()
    summary["remote_url_configured"] = bool(settings.memory_service_url)
    if not settings.memory_service_url:
        return PreflightCheck(
            "fleet_memory",
            "warn",
            "Fleet memory is using the local cache; configure AUTO_ROUTER_MEMORY_SERVICE_URL "
            "for canonical AssistX/Neo4j retrieval.",
            "warning",
            summary,
        )
    return PreflightCheck(
        "fleet_memory",
        "pass",
        "Fleet memory remote backend and degraded-mode cache are configured.",
        details=summary,
    )


def _check_assistx(settings: Settings) -> PreflightCheck:
    missing = []
    if not settings.assistx_tasks_url:
        missing.append("AUTO_ROUTER_ASSISTX_TASKS_URL")
    if not settings.assistx_event_sink_url:
        missing.append("AUTO_ROUTER_ASSISTX_EVENT_SINK_URL")
    if missing:
        return PreflightCheck(
            "assistx",
            "warn",
            "AssistX task intake and/or event sink are not fully configured.",
            "warning",
            {"missing": missing},
        )
    return PreflightCheck("assistx", "pass", "AssistX task intake and event sink are configured.")


def _check_cli_discovery(state: Any) -> PreflightCheck:
    results = getattr(state, "cli_discovery", []) or []
    if not results:
        return PreflightCheck("agent_clis", "warn", "Agent CLI discovery has not been run yet.", "warning")
    runnable = [item.get("name") for item in results if item.get("runnable")]
    status = "pass" if runnable else "warn"
    return PreflightCheck(
        "agent_clis",
        status,
        "Agent CLI discovery has runnable tools." if runnable else "Agent CLI discovery ran, but no tools are runnable.",
        "warning" if status == "warn" else "info",
        {"runnable": runnable, "total": len(results)},
    )


def _check_dry_run_posture(state: Any) -> PreflightCheck:
    agents = getattr(state, "agents", None)
    workers = getattr(agents, "workers", []) if agents else []
    enabled_workers = [worker.name for worker in workers if getattr(worker, "enabled", False)]
    if enabled_workers:
        return PreflightCheck(
            "agent_execution_posture",
            "warn",
            "Some agent workers are enabled; confirm write/commit/push policy before production use.",
            "warning",
            {"enabled_workers": enabled_workers},
        )
    return PreflightCheck("agent_execution_posture", "pass", "Agent workers are disabled by default; dry-run posture preserved.")
