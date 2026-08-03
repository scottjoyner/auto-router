from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auto_router.request_idempotency import (
    RequestIdempotencyLedger,
    RequestIdempotencyMiddleware,
)


def test_ledger_reserves_once_and_persists_terminal_state(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'router.sqlite3'}"
    ledger = RequestIdempotencyLedger(database_url, ttl_seconds=3600)

    created, first = ledger.reserve("key-1", "fingerprint-1", "request-1")
    assert created is True
    assert first["state"] == "in_progress"

    created, duplicate = ledger.reserve("key-1", "fingerprint-1", "request-1")
    assert created is False
    assert duplicate["request_id"] == "request-1"

    ledger.transition("key-1", "upstream_started")
    ledger.transition(
        "key-1",
        "possibly_accepted",
        detail="transport interrupted after request write",
    )
    ledger.transition("key-1", "failed", detail="must not replace ambiguity")

    record = ledger.get("key-1")
    assert record is not None
    assert record["state"] == "possibly_accepted"
    assert "transport interrupted" in str(record["detail"])

    # The durable record contains only identifiers, hashes, state, and timing.
    with sqlite3.connect(tmp_path / "router.sqlite3") as conn:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(inference_request_idempotency)"
            ).fetchall()
        }
    assert "payload" not in columns
    assert "prompt" not in columns
    assert "response" not in columns


def test_duplicate_request_is_not_forwarded(tmp_path: Path) -> None:
    ledger = RequestIdempotencyLedger(
        f"sqlite:///{tmp_path / 'router.sqlite3'}",
        ttl_seconds=3600,
    )
    app = FastAPI()
    calls: list[dict] = []

    @app.post("/v1/chat/completions")
    async def completion(request: Request) -> dict:
        payload = await request.json()
        calls.append(payload)
        return {"id": "completion-1", "choices": []}

    app.add_middleware(RequestIdempotencyMiddleware, ledger=ledger)
    client = TestClient(app)
    body = {
        "model": "auto/local",
        "messages": [{"role": "user", "content": "bounded test"}],
        "max_tokens": 8,
    }
    headers = {"Idempotency-Key": "operator-request-1"}

    first = client.post("/v1/chat/completions", json=body, headers=headers)
    second = client.post("/v1/chat/completions", json=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["state"] == "completed"
    assert len(calls) == 1
    metadata = calls[0]["metadata"]
    assert metadata["request_id"]
    assert len(metadata["idempotency_key"]) == 64
    assert len(metadata["idempotency_fingerprint"]) == 64


def test_same_key_with_different_request_is_rejected(tmp_path: Path) -> None:
    ledger = RequestIdempotencyLedger(
        f"sqlite:///{tmp_path / 'router.sqlite3'}",
        ttl_seconds=3600,
    )
    app = FastAPI()

    @app.post("/v1/responses")
    async def response_endpoint() -> dict:
        return {"id": "response-1"}

    app.add_middleware(RequestIdempotencyMiddleware, ledger=ledger)
    client = TestClient(app)
    headers = {"Idempotency-Key": "shared-key"}

    first = client.post(
        "/v1/responses",
        json={"model": "auto/local", "input": "first"},
        headers=headers,
    )
    conflict = client.post(
        "/v1/responses",
        json={"model": "auto/local", "input": "different"},
        headers=headers,
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert "different request" in conflict.json()["detail"]


def test_interrupted_response_after_upstream_start_is_ambiguous(tmp_path: Path) -> None:
    ledger = RequestIdempotencyLedger(
        f"sqlite:///{tmp_path / 'router.sqlite3'}",
        ttl_seconds=3600,
    )
    created, _ = ledger.reserve("key-ambiguous", "fingerprint", "request-ambiguous")
    assert created is True
    ledger.transition("key-ambiguous", "upstream_started")
    ledger.transition(
        "key-ambiguous",
        "possibly_accepted",
        status_code=504,
        detail="timeout after upstream dispatch",
    )

    duplicate_created, record = ledger.reserve(
        "key-ambiguous",
        "fingerprint",
        "request-ambiguous",
    )
    assert duplicate_created is False
    assert record["state"] == "possibly_accepted"
    assert record["status_code"] == 504
