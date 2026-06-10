from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from auto_router.context import ContextSignal, ContextSnapshot
from auto_router.live_models import LiveModelSnapshot


def signal_snapshot(signals: list[ContextSignal], revision: str = "swarm-signals", source: str = "signals") -> ContextSnapshot:
    return ContextSnapshot(revision=revision, source=source, signals=signals)


def _provider_scoped_model_id(provider: str, model_id: str) -> str:
    provider_name = str(provider or "").strip().lower()
    model_name = str(model_id or "").strip().lower()
    if provider_name and model_name:
        if model_name.startswith(f"{provider_name}."):
            return model_name
        return f"{provider_name}.{model_name}"
    return model_name or provider_name


def _slug(text: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in str(text))
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "unknown"


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _decision_detail(is_chosen: bool, reason: Any, rejections: list[str] | None) -> str:
    pieces = ["chosen" if is_chosen else "candidate"]
    reason_text = str(reason or "").strip()
    if reason_text:
        pieces.append(reason_text)
    if not is_chosen and rejections:
        pieces.append(f"{len(rejections)} rejections")
    return " · ".join(pieces)


def _execution_detail(success: bool, latency_ms: int, status_code: int | None, error: Exception | None) -> str:
    if success:
        status_text = f"{status_code}" if status_code is not None else "success"
        return f"{status_text} · {latency_ms}ms"
    error_text = str(error or "failed").strip()
    return f"failed · {latency_ms}ms · {error_text[:120]}"


def _rejection_is_blocking(rejections: list[str] | None, provider: str, model: str) -> bool:
    if not rejections:
        return False
    needle = f"{provider}/{model}".lower()
    return any(
        needle in str(reason).lower()
        and any(token in str(reason).lower() for token in ("circuit open", "not allowed", "quota unavailable", "blocked"))
        for reason in rejections
    )


def provider_health_signals(
    provider: str,
    report: dict[str, Any],
    node_id: str | None = None,
) -> list[ContextSignal]:
    source = str(report.get("source") or "provider_health")
    health_score = _int_value(report.get("health_score"), default=0)
    model_count = _int_value(report.get("model_count"), default=0)
    ok = bool(report.get("ok"))
    drift = bool(report.get("drift"))
    latency_ms = report.get("latency_ms")
    age_seconds = _int_value(report.get("age_seconds"), default=0)
    success_rate = float(report.get("success_rate") or 0.0)
    signal_type = "blocked" if not ok else ("avoid" if drift else "preferred")
    strength = 1.0 if not ok else max(min(health_score / 100.0, 1.0), 0.1)
    if drift and ok:
        strength = min(strength, 0.7)
    metadata = {
        "health_score": health_score,
        "ok": ok,
        "drift": drift,
        "model_count": model_count,
        "latency_ms": latency_ms,
        "age_seconds": age_seconds,
        "success_rate": success_rate,
        "signature": report.get("signature"),
        "previous_signature": report.get("previous_signature"),
    }
    detail = str(report.get("error") or report.get("detail") or "").strip()
    signals = [
        ContextSignal(
            signal_id=f"provider.{_slug(provider)}.health",
            target_type="provider",
            target_id=str(provider).strip().lower(),
            signal_type=signal_type,
            source=source,
            strength=strength,
            priority=20,
            detail=detail,
            metadata=metadata,
        )
    ]
    if node_id:
        signals.append(
            ContextSignal(
                signal_id=f"node.{_slug(node_id)}.health.{_slug(provider)}",
                target_type="node",
                target_id=node_id,
                signal_type=signal_type,
                source=source,
                strength=strength,
                priority=20,
                detail=detail,
                metadata=metadata | {"provider": provider},
            )
        )
    return signals


def live_model_signals(snapshot: LiveModelSnapshot, node_id: str | None = None) -> list[ContextSignal]:
    source = "live_models"
    ok = bool(snapshot.ok)
    drift = bool(snapshot.drift)
    signal_type = "blocked" if not ok else ("avoid" if drift else "preferred")
    base_strength = 1.0 if ok else 0.0
    if drift and ok:
        base_strength = 0.6
    metadata_base = {
        "provider": snapshot.provider,
        "ok": ok,
        "drift": drift,
        "model_count": len(snapshot.models),
        "latency_ms": snapshot.latency_ms,
        "signature": snapshot.signature,
        "previous_signature": snapshot.previous_signature,
        "changed_models": list(snapshot.changed_models),
    }
    signals: list[ContextSignal] = []
    for record in snapshot.models:
        model_id = str(record.get("id") or record.get("name") or record.get("model") or "").strip()
        if not model_id:
            continue
        loaded = bool(record.get("loaded", True))
        model_signal_type = signal_type if loaded else ("avoid" if ok else "blocked")
        strength = base_strength if loaded else max(base_strength * 0.5, 0.25)
        details: list[str] = []
        if loaded:
            details.append("loaded")
        if record.get("owned_by"):
            details.append(f"owned by {record.get('owned_by')}")
        if record.get("source"):
            details.append(str(record.get("source")))
        signals.append(
            ContextSignal(
                signal_id=f"model.{_slug(snapshot.provider)}.{_slug(model_id)}.live",
                target_type="model",
                target_id=_provider_scoped_model_id(snapshot.provider, model_id),
                signal_type=model_signal_type,
                source=source,
                strength=strength,
                priority=30,
                detail=" · ".join(details),
                metadata=metadata_base | {"loaded": loaded, "endpoint": record.get("endpoint"), "context_length": record.get("context_length")},
            )
        )
    if node_id:
        signals.append(
            ContextSignal(
                signal_id=f"node.{_slug(node_id)}.models.{_slug(snapshot.provider)}",
                target_type="node",
                target_id=node_id,
                signal_type=signal_type,
                source=source,
                strength=base_strength,
                priority=30,
                detail=f"{len(snapshot.models)} live model(s)",
                metadata=metadata_base | {"node_id": node_id},
            )
        )
    return signals


def route_decision_signals(
    request: Any,
    profile_name: str,
    stage: str,
    chosen: dict[str, Any],
    candidates: list[dict[str, Any]],
    rejections: list[str] | None = None,
    node_id: str | None = None,
) -> list[ContextSignal]:
    route = str(getattr(request, "route", "") or "unknown")
    request_id = str(getattr(request, "request_id", "") or "unknown")
    priority = str(getattr(getattr(request, "priority", None), "value", getattr(request, "priority", "unknown")) or "unknown")
    local_only = bool(getattr(request, "local_only", False))
    allow_cloud = getattr(request, "allow_cloud", None)
    chosen_provider = str(chosen.get("provider") or "").strip()
    chosen_provider_id = str(chosen.get("provider_id") or chosen_provider).strip().lower()
    chosen_model = str(chosen.get("model") or chosen.get("provider_model") or "").strip()
    chosen_model_id = str(chosen.get("model_id") or _provider_scoped_model_id(chosen_provider, chosen_model)).strip().lower()
    chosen_score = _float_value(chosen.get("score"), default=0.0)
    rejection_count = len(rejections or [])
    signals: list[ContextSignal] = []
    for candidate in candidates:
        provider = str(candidate.get("provider") or "").strip()
        model = str(candidate.get("model") or candidate.get("provider_model") or "").strip()
        candidate_provider_id = str(candidate.get("provider_id") or provider).strip().lower()
        candidate_model_id = str(candidate.get("model_id") or _provider_scoped_model_id(provider, model)).strip().lower()
        if not provider and not model:
            continue
        is_chosen = candidate_provider_id == chosen_provider_id and candidate_model_id == chosen_model_id
        signal_type = "preferred" if is_chosen else "avoid"
        if not is_chosen and _rejection_is_blocking(rejections, provider, model):
            signal_type = "blocked"
        signal_id_base = f"route.{_slug(route)}.{_slug(stage)}.{_slug(provider)}.{_slug(model)}"
        metadata = {
            "request_id": request_id,
            "route": route,
            "profile": profile_name,
            "task_id": getattr(request, "task_id", None) or (request.metadata.get("task_id") if isinstance(getattr(request, "metadata", None), dict) else None),
            "agent_run_id": getattr(request, "agent_run_id", None) or (request.metadata.get("agent_run_id") if isinstance(getattr(request, "metadata", None), dict) else None),
            "node_id": node_id,
            "stage": stage,
            "priority": priority,
            "local_only": local_only,
            "allow_cloud": allow_cloud,
            "chosen": is_chosen,
            "score": _float_value(candidate.get("score"), default=0.0),
            "reason": candidate.get("reason"),
            "candidate_count": len(candidates),
            "rejection_count": rejection_count,
            "provider_id": candidate_provider_id,
            "model_id": candidate_model_id,
        }
        if provider:
            signals.append(
                ContextSignal(
                    signal_id=f"{signal_id_base}.provider",
                    target_type="provider",
                    target_id=candidate_provider_id,
                    signal_type=signal_type,
                    source="route_decision",
                    strength=0.85 if is_chosen else 0.35,
                    priority=40,
                    detail=_decision_detail(is_chosen, candidate.get("reason"), rejections),
                    metadata=metadata,
                    expires_at=int(time.time()) + 6 * 60 * 60,
                )
            )
        if model:
            signals.append(
                ContextSignal(
                    signal_id=f"{signal_id_base}.model",
                    target_type="model",
                    target_id=candidate_model_id,
                    signal_type=signal_type,
                    source="route_decision",
                    strength=0.85 if is_chosen else 0.35,
                    priority=40,
                    detail=_decision_detail(is_chosen, candidate.get("reason"), rejections),
                    metadata=metadata,
                    expires_at=int(time.time()) + 6 * 60 * 60,
                )
            )
        if node_id:
            signals.append(
                ContextSignal(
                    signal_id=f"{signal_id_base}.node.{_slug(node_id)}",
                    target_type="node",
                    target_id=node_id,
                    signal_type=signal_type,
                    source="route_decision",
                    strength=0.85 if is_chosen else 0.35,
                    priority=40,
                    detail=_decision_detail(is_chosen, candidate.get("reason"), rejections),
                    metadata=metadata | {"node_id": node_id},
                    expires_at=int(time.time()) + 6 * 60 * 60,
                )
            )
    if chosen_provider or chosen_model:
        chosen_metadata = {
            "request_id": request_id,
            "route": route,
            "profile": profile_name,
            "task_id": getattr(request, "task_id", None) or (request.metadata.get("task_id") if isinstance(getattr(request, "metadata", None), dict) else None),
            "agent_run_id": getattr(request, "agent_run_id", None) or (request.metadata.get("agent_run_id") if isinstance(getattr(request, "metadata", None), dict) else None),
            "node_id": node_id,
            "stage": stage,
            "priority": priority,
            "local_only": local_only,
            "allow_cloud": allow_cloud,
            "chosen": True,
            "score": chosen_score,
            "reason": chosen.get("reason"),
            "candidate_count": len(candidates),
            "rejection_count": rejection_count,
            "provider_id": chosen_provider_id,
            "model_id": chosen_model_id,
        }
        if chosen_provider:
            signals.append(
                ContextSignal(
                    signal_id=f"route.{_slug(route)}.{_slug(stage)}.{_slug(chosen_provider)}.chosen.provider",
                    target_type="provider",
                    target_id=chosen_provider_id,
                    signal_type="preferred",
                    source="route_decision",
                    strength=0.95,
                    priority=35,
                    detail=_decision_detail(True, chosen.get("reason"), rejections),
                    metadata=chosen_metadata,
                    expires_at=int(time.time()) + 6 * 60 * 60,
                )
            )
        if chosen_model:
            signals.append(
                ContextSignal(
                    signal_id=f"route.{_slug(route)}.{_slug(stage)}.{_slug(chosen_provider)}.{_slug(chosen_model)}.chosen.model",
                    target_type="model",
                    target_id=chosen_model_id,
                    signal_type="preferred",
                    source="route_decision",
                    strength=0.95,
                    priority=35,
                    detail=_decision_detail(True, chosen.get("reason"), rejections),
                    metadata=chosen_metadata,
                    expires_at=int(time.time()) + 6 * 60 * 60,
                )
            )
        if node_id:
            signals.append(
                ContextSignal(
                    signal_id=f"route.{_slug(route)}.{_slug(stage)}.{_slug(node_id)}.chosen.node",
                    target_type="node",
                    target_id=node_id,
                    signal_type="preferred",
                    source="route_decision",
                    strength=0.95,
                    priority=35,
                    detail=_decision_detail(True, chosen.get("reason"), rejections),
                    metadata=chosen_metadata | {"node_id": node_id},
                    expires_at=int(time.time()) + 6 * 60 * 60,
                )
            )
    return signals


def route_execution_signals(
    request: Any,
    provider: str,
    model: str,
    stage: str,
    status_code: int | None,
    latency_ms: int,
    error: Exception | None = None,
    usage: dict[str, int] | None = None,
    tokens_per_second: float | None = None,
    value_units: int | None = None,
    value_per_second: float | None = None,
    node_id: str | None = None,
    gateway_metadata: dict[str, Any] | None = None,
) -> list[ContextSignal]:
    route = str(getattr(request, "route", "") or "unknown")
    request_id = str(getattr(request, "request_id", "") or "unknown")
    priority = str(getattr(getattr(request, "priority", None), "value", getattr(request, "priority", "unknown")) or "unknown")
    success = error is None and (status_code is None or 200 <= status_code < 400)
    signal_type = "preferred" if success else _failure_signal_type(status_code, error)
    strength = 0.95 if success else (0.9 if signal_type == "blocked" else 0.35)
    usage = usage or {}
    metadata = {
        "request_id": request_id,
        "route": route,
        "stage": stage,
        "priority": priority,
        "task_id": getattr(request, "task_id", None) or (request.metadata.get("task_id") if isinstance(getattr(request, "metadata", None), dict) else None),
        "agent_run_id": getattr(request, "agent_run_id", None) or (request.metadata.get("agent_run_id") if isinstance(getattr(request, "metadata", None), dict) else None),
        "node_id": node_id,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "tokens_per_second": tokens_per_second,
        "value_units": value_units,
        "value_per_second": value_per_second,
        "error_type": type(error).__name__ if error else None,
        "error_message": str(error)[:1000] if error else None,
        "gateway_provider": gateway_metadata.get("provider") if isinstance(gateway_metadata, dict) else None,
        "gateway_used": bool(gateway_metadata),
        "input_tokens": int((usage or {}).get("prompt_tokens") or 0),
        "output_tokens": int((usage or {}).get("completion_tokens") or 0),
        "total_tokens": int((usage or {}).get("total_tokens") or 0),
    }
    detail = _execution_detail(success, latency_ms, status_code, error)
    signals: list[ContextSignal] = []
    provider_id = str(provider or "").strip().lower()
    model_id = _provider_scoped_model_id(provider, model)
    for target_type, target_value in (("provider", provider_id), ("model", model_id), ("node", node_id)):
        if not target_value:
            continue
        signals.append(
            ContextSignal(
                signal_id=f"route.{_slug(route)}.{_slug(stage)}.{_slug(target_type)}.{_slug(target_value)}.execution",
                target_type=target_type,
                target_id=target_value,
                signal_type=signal_type,
                source="route_execution",
                strength=strength,
                priority=42,
                detail=detail,
                metadata=metadata | {"target_type": target_type, "target_id": str(target_value)},
                expires_at=int(time.time()) + (2 * 60 * 60 if signal_type == "blocked" else 15 * 60),
            )
        )
    return signals


def _failure_signal_type(status_code: int | None, error: Exception | None) -> str:
    hard_statuses = {401, 403}
    if status_code in hard_statuses:
        return "blocked"
    text = str(error or "").lower()
    if any(token in text for token in ("not allowed", "disallowed", "blocked", "authentication", "unauthorized", "forbidden")):
        return "blocked"
    return "avoid"


class ContextSignalStore:
    """Durable store for swarm signals projected from AssistX/Sophia context."""

    def __init__(self, database_url: str):
        self.database_path = self._path_from_url(database_url)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def save_snapshot(self, snapshot: ContextSnapshot) -> None:
        now = int(time.time())
        self.prune(now=now)
        with self._connect() as conn:
            for signal in snapshot.signals:
                conn.execute(
                    """
                    INSERT INTO context_signal_events (
                        signal_id, target_type, target_id, signal_type, source,
                        strength, active, observed_at, expires_at, priority,
                        detail, tags_json, metadata_json, context_revision,
                        context_source, saved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal.signal_id,
                        signal.target_type,
                        signal.target_id,
                        signal.signal_type,
                        signal.source,
                        signal.strength,
                        1 if signal.active else 0,
                        signal.observed_at,
                        signal.expires_at,
                        signal.priority,
                        signal.detail,
                        json.dumps(sorted(signal.tags)),
                        json.dumps(signal.metadata),
                        snapshot.revision,
                        snapshot.source,
                        now,
                    ),
                )

    def latest_signals(self, limit: int | None = None) -> list[ContextSignal]:
        now = int(time.time())
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM context_signal_events
                WHERE active = 1
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY saved_at DESC, observed_at DESC, id DESC
                """,
                (now,),
            ).fetchall()
        latest_rows: dict[str, sqlite3.Row] = {}
        for row in rows:
            signal_id = row["signal_id"]
            if signal_id not in latest_rows:
                latest_rows[signal_id] = row
        signals = [self._row_to_signal(row) for row in latest_rows.values()]
        signals.sort(key=lambda item: (item.priority, item.source, item.target_type, item.target_id, item.signal_type))
        return signals[:limit] if limit is not None else signals

    def prune(self, retention_seconds: int = 7 * 24 * 60 * 60, now: int | None = None) -> int:
        current_time = int(time.time()) if now is None else int(now)
        cutoff = current_time - max(int(retention_seconds), 0)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM context_signal_events
                WHERE (expires_at IS NOT NULL AND expires_at <= ?)
                   OR saved_at < ?
                   OR observed_at < ?
                """,
                (current_time, cutoff, cutoff),
            )
            return int(cursor.rowcount or 0)

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM context_signal_events
                ORDER BY saved_at DESC, observed_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, int]:
        signals = self.latest_signals()
        summary: dict[str, int] = {"signals": len(signals), "active": 0, "provider": 0, "model": 0, "node": 0, "service": 0}
        for signal in signals:
            if signal.is_active:
                summary["active"] += 1
            summary[signal.target_type] = summary.get(signal.target_type, 0) + 1
        return summary

    def hydrate_context(self, snapshot: ContextSnapshot) -> ContextSnapshot:
        signals = _merge_signals(list(snapshot.signals), self.latest_signals())
        return snapshot.model_copy(update={"signals": signals})

    def _row_to_signal(self, row: sqlite3.Row) -> ContextSignal:
        metadata_raw = row["metadata_json"] or "{}"
        tags_raw = row["tags_json"] or "[]"
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            metadata = {}
        try:
            tags = set(json.loads(tags_raw))
        except json.JSONDecodeError:
            tags = set()
        return ContextSignal(
            signal_id=row["signal_id"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            signal_type=row["signal_type"],
            source=row["source"] or "",
            strength=row["strength"],
            active=bool(row["active"]),
            observed_at=row["observed_at"],
            expires_at=row["expires_at"],
            priority=row["priority"],
            detail=row["detail"] or "",
            tags={str(item) for item in tags if item},
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS context_signal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    strength REAL NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    observed_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    priority INTEGER NOT NULL DEFAULT 100,
                    detail TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    context_revision TEXT NOT NULL DEFAULT '',
                    context_source TEXT NOT NULL DEFAULT '',
                    saved_at INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_context_signal_events_signal_id ON context_signal_events(signal_id, saved_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_context_signal_events_target ON context_signal_events(target_type, target_id, saved_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_context_signal_events_saved ON context_signal_events(saved_at DESC)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _path_from_url(self, database_url: str) -> Path:
        if database_url.startswith("sqlite:///./"):
            return Path(database_url.removeprefix("sqlite:///"))
        if database_url.startswith("sqlite:////"):
            parsed = urlparse(database_url)
            return Path(parsed.path)
        if database_url.startswith("sqlite:///"):
            return Path(database_url.removeprefix("sqlite:///"))
        return Path(database_url)


def _merge_signals(*groups: list[ContextSignal]) -> list[ContextSignal]:
    merged: dict[str, ContextSignal] = {}
    for group in groups:
        for signal in group:
            existing = merged.get(signal.signal_id)
            if existing is None or signal.observed_at >= existing.observed_at:
                merged[signal.signal_id] = signal
    return sorted(merged.values(), key=lambda item: (item.priority, item.source, item.target_type, item.target_id, item.signal_type))
