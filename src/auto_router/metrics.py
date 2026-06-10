from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, REGISTRY, generate_latest

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


def _safe_counter(name: str, doc: str, labels: list | None = None) -> Counter:
    try:
        return Counter(name, doc, labels or [])
    except ValueError:
        for c in REGISTRY._collector_to_names:
            if hasattr(c, "_name") and c._name == name:
                return c
        return Counter(name, doc, labels or [])


def _safe_gauge(name: str, doc: str, labels: list | None = None) -> Gauge:
    try:
        return Gauge(name, doc, labels or [])
    except ValueError:
        for c in REGISTRY._collector_to_names:
            if hasattr(c, "_name") and c._name == name:
                return c
        return Gauge(name, doc, labels or [])


def _safe_histogram(name: str, doc: str, labels: list | None = None) -> Histogram:
    try:
        return Histogram(name, doc, labels or [])
    except ValueError:
        for c in REGISTRY._collector_to_names:
            if hasattr(c, "_name") and c._name == name:
                return c
        return Histogram(name, doc, labels or [])


REQUESTS = _safe_counter("auto_router_http_requests_total", "HTTP requests", ["path", "method", "status"])
REQUEST_LATENCY = _safe_histogram("auto_router_http_request_duration_seconds", "HTTP request duration", ["path", "method"])
ROUTE_DECISIONS = _safe_counter("auto_router_route_decisions_total", "Route decisions", ["provider", "model", "status"])
OPEN_CIRCUITS = _safe_gauge("auto_router_open_circuits", "Open circuit breakers", ["provider"])
PROVIDERS_ENABLED = _safe_gauge("auto_router_providers_enabled", "Enabled providers")
QUEUE_DEPTH = _safe_gauge("auto_router_queue_depth", "Pending outbox events")
