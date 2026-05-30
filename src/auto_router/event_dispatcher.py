from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from auto_router.event_outbox import EventOutbox


@dataclass
class DispatchResult:
    event_id: str
    status: str
    delivered: bool
    retry: bool
    error: str | None = None
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "status": self.status,
            "delivered": self.delivered,
            "retry": self.retry,
            "error": self.error,
            "status_code": self.status_code,
        }


class AssistXEventDispatcher:
    """Posts pending outbox events to AssistX and updates event state.

    The dispatcher is intentionally explicit and operator-triggered for now.
    A background loop can be added later once the AssistX event sink is stable.
    """

    def __init__(
        self,
        outbox: EventOutbox,
        sink_url: str | None,
        timeout_seconds: float = 10.0,
        max_attempts: int = 5,
    ):
        self.outbox = outbox
        self.sink_url = sink_url
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    @property
    def configured(self) -> bool:
        return bool(self.sink_url)

    async def dispatch_pending(self, limit: int = 25, dry_run: bool = False) -> list[DispatchResult]:
        events = self.outbox.pending(limit=limit)
        if dry_run:
            return [
                DispatchResult(
                    event_id=event["event_id"],
                    status="dry_run",
                    delivered=False,
                    retry=False,
                    error=None,
                )
                for event in events
            ]
        if not self.sink_url:
            return [
                DispatchResult(
                    event_id=event["event_id"],
                    status="not_configured",
                    delivered=False,
                    retry=True,
                    error="AUTO_ROUTER_ASSISTX_EVENT_SINK_URL is not configured",
                )
                for event in events
            ]

        results: list[DispatchResult] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for event in events:
                results.append(await self._dispatch_one(client, event))
        return results

    async def _dispatch_one(self, client: httpx.AsyncClient, event: dict[str, Any]) -> DispatchResult:
        event_id = str(event["event_id"])
        try:
            response = await client.post(
                str(self.sink_url),
                json={
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "source_service": event["source_service"],
                    "idempotency_key": event["idempotency_key"],
                    "payload": event["payload"],
                    "created_at": event["created_at"],
                },
            )
        except Exception as exc:
            self.outbox.mark_failed(event_id, str(exc), retry=True)
            return DispatchResult(
                event_id=event_id,
                status="retry",
                delivered=False,
                retry=True,
                error=str(exc)[:500],
            )

        if 200 <= response.status_code < 300 or response.status_code == 409:
            self.outbox.mark_delivered(event_id)
            return DispatchResult(
                event_id=event_id,
                status="delivered",
                delivered=True,
                retry=False,
                status_code=response.status_code,
            )

        retry = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        if int(event.get("attempts") or 0) + 1 >= self.max_attempts:
            retry = False
        error = f"AssistX returned HTTP {response.status_code}: {response.text[:500]}"
        self.outbox.mark_failed(event_id, error, retry=retry)
        return DispatchResult(
            event_id=event_id,
            status="retry" if retry else "dead_letter",
            delivered=False,
            retry=retry,
            error=error,
            status_code=response.status_code,
        )
