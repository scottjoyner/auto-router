from auto_router.ledger import RuntimeSample, UsageEvent, UsageLedger


def test_usage_ledger_records_runtime_samples(tmp_path):
    db_path = tmp_path / "router.sqlite3"
    ledger = UsageLedger(f"sqlite:///{db_path}")

    ledger.record_runtime_sample(
        RuntimeSample(
            request_id="req-2",
            provider_id="local",
            model_id="local/model",
            route="chat_completions",
            priority="interactive",
            stage="final",
            started_at_ms=1_000,
            ended_at_ms=1_250,
            queue_wait_ms=25,
            load_time_ms=10,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            tokens_per_second=60.0,
            value_units=5,
            value_per_second=20.0,
            status_code=200,
            latency_ms=250,
        )
    )

    summary = ledger.runtime_summary()
    recent = ledger.recent_runtime_samples()

    assert summary["samples"] == 1
    assert summary["successful"] == 1
    assert summary["avg_queue_wait_ms"] == 25
    assert summary["avg_tokens_per_second"] == 60.0
    assert summary["by_provider"][0]["provider"] == "local"
    assert recent[0]["elapsed_ms"] == 250
    assert recent[0]["value_per_second"] == 20.0


def test_runtime_sample_without_status_code_is_success_when_no_error(tmp_path):
    db_path = tmp_path / "router.sqlite3"
    ledger = UsageLedger(f"sqlite:///{db_path}")

    ledger.record_runtime_sample(
        RuntimeSample(
            request_id="req-stream",
            provider_id="local",
            model_id="local/model",
            route="chat_completions",
            priority="interactive",
            stage="final",
            status_code=None,
            latency_ms=100,
            error_type=None,
            error_message=None,
        )
    )

    summary = ledger.runtime_summary()
    assert summary["successful"] == 1
    assert summary["failed"] == 0


def test_runtime_sample_without_status_code_is_failure_when_error_present(tmp_path):
    db_path = tmp_path / "router.sqlite3"
    ledger = UsageLedger(f"sqlite:///{db_path}")

    ledger.record_runtime_sample(
        RuntimeSample(
            request_id="req-error",
            provider_id="local",
            model_id="local/model",
            route="chat_completions",
            priority="interactive",
            stage="final",
            status_code=None,
            latency_ms=100,
            error_type="RuntimeError",
            error_message="stream failed",
        )
    )

    summary = ledger.runtime_summary()
    assert summary["successful"] == 0
    assert summary["failed"] == 1
