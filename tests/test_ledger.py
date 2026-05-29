from auto_router.ledger import UsageEvent, UsageLedger


def test_usage_ledger_records_and_summarizes(tmp_path):
    db_path = tmp_path / "router.sqlite3"
    ledger = UsageLedger(f"sqlite:///{db_path}")

    ledger.record(
        UsageEvent(
            request_id="req-1",
            provider_id="local",
            model_id="local/model",
            route="chat_completions",
            priority="interactive",
            stage="final",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            status_code=200,
            latency_ms=25,
        )
    )

    summary = ledger.summary()
    recent = ledger.recent_events()

    assert summary["totals"]["requests"] == 1
    assert summary["totals"]["total_tokens"] == 15
    assert summary["by_provider"][0]["provider_id"] == "local"
    assert recent[0]["request_id"] == "req-1"
    assert recent[0]["error_type"] is None
