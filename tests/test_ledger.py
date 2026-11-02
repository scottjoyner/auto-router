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


def test_counterfactual_decision_is_completed_by_runtime_sample(tmp_path):
    db_path = tmp_path / "router.sqlite3"
    ledger = UsageLedger(f"sqlite:///{db_path}")
    # Seed historical evidence used to predict candidate value.
    for provider, model, value in (("fast", "model-a", 10.0), ("slow", "model-b", 3.0)):
        ledger.record_runtime_sample(
            RuntimeSample(
                request_id=f"seed-{provider}",
                provider_id=provider,
                model_id=model,
                route="chat_completions",
                priority="interactive",
                stage="final",
                value_per_second=value,
                status_code=200,
                latency_ms=100,
            )
        )
    candidates = [
        {"provider": "fast", "provider_model": "model-a", "score": 1},
        {"provider": "slow", "provider_model": "model-b", "score": 2},
    ]
    ledger.record_counterfactual_decision(
        decision_id="decision-1",
        request_id="req-1",
        stage="final",
        chosen=candidates[1],
        candidates=candidates,
        rejections=[],
    )
    ledger.record_runtime_sample(
        RuntimeSample(
            request_id="req-1",
            provider_id="slow",
            model_id="model-b",
            route="chat_completions",
            priority="interactive",
            stage="final",
            value_per_second=2.0,
            status_code=200,
            latency_ms=500,
        )
    )

    result = ledger.counterfactual_summary()
    item = result["items"][0]
    assert item["status"] == "completed"
    assert item["realized_value_per_hour"] == 7200.0
    assert item["realized_regret"] == 28800.0
    assert result["summary"]["avg_realized_regret"] == 28800.0


def test_failed_counterfactual_is_not_completed_or_valued(tmp_path):
    ledger = UsageLedger(f"sqlite:///{tmp_path / 'ledger.sqlite3'}")
    ledger.record_runtime_sample(RuntimeSample(
        request_id="seed-valid", provider_id="healthy", model_id="model-v",
        route="chat_completions", priority="interactive", stage="final",
        status_code=200, latency_ms=500, value_per_second=3.0,
    ))
    ledger.record_runtime_sample(RuntimeSample(
        request_id="seed-failed", provider_id="broken", model_id="model-b",
        route="chat_completions", priority="interactive", stage="final",
        status_code=503, latency_ms=0, value_per_second=999999.0,
        error_type="RuntimeError", error_message="down",
    ))
    good = ledger._candidate_prediction({"provider": "healthy", "provider_model": "model-v"})
    bad = ledger._candidate_prediction({"provider": "broken", "provider_model": "model-b"})
    assert good["predicted_value_per_hour"] == 10800.0
    assert good["qualified_value_samples"] == 1
    assert bad["predicted_value_per_hour"] is None
    assert bad["qualified_value_samples"] == 0
    assert bad["predicted_success_rate"] == 0
    ledger.record_counterfactual_decision(
        decision_id="failed-1", request_id="attempt-1", stage="final",
        chosen={"provider": "broken", "provider_model": "model-b"},
        candidates=[{"provider": "healthy", "provider_model": "model-v"}],
        rejections=[],
    )
    ledger.record_runtime_sample(RuntimeSample(
        request_id="attempt-1", provider_id="broken", model_id="model-b",
        route="chat_completions", priority="interactive", stage="final",
        status_code=503, latency_ms=0, value_per_second=999999.0,
        error_type="RuntimeError", error_message="connection aborted",
    ))
    summary = ledger.counterfactual_summary()
    assert summary["summary"]["decisions"] == 1
    assert summary["summary"]["completed"] == 0
    assert summary["summary"]["failed"] == 1
    assert summary["summary"]["terminal"] == 1
    assert summary["summary"]["pending"] == 0
    assert summary["summary"]["avg_realized_regret"] is None
    assert summary["items"][0]["realized_value_per_hour"] is None
    assert summary["items"][0]["realized_regret"] is None


def test_selected_decisions_are_pending_not_completed(tmp_path):
    ledger = UsageLedger(f"sqlite:///{tmp_path / 'ledger.sqlite3'}")
    candidate = {"provider": "local", "provider_model": "model"}
    ledger.record_counterfactual_decision(
        decision_id="selected-1", request_id="pending-1", stage="final",
        chosen=candidate, candidates=[candidate], rejections=[],
    )
    result = ledger.counterfactual_summary()["summary"]
    assert (result["decisions"], result["completed"], result["failed"], result["pending"]) == (1, 0, 0, 1)


def test_successful_zero_rate_is_counted_in_model_value_prediction(tmp_path):
    ledger = UsageLedger(f"sqlite:///{tmp_path / 'ledger.sqlite3'}")
    ledger.record_runtime_sample(RuntimeSample(
        request_id="seed-zero", provider_id="zero", model_id="model-0",
        route="chat_completions", priority="background", stage="final",
        status_code=200, latency_ms=50, value_per_second=0.0,
    ))
    candidate = {"provider": "zero", "provider_model": "model-0"}
    prediction = ledger._candidate_prediction(candidate)
    assert prediction["prediction_samples"] == 1
    assert prediction["qualified_value_samples"] == 1
    assert prediction["predicted_value_per_hour"] == 0.0
    # Transport completion does not prove task quality. A zero-output
    # attempt must still depress predicted model value rather than vanish.
    ledger.record_counterfactual_decision(
        decision_id="decision-zero", request_id="zero-run", stage="final",
        chosen=candidate, candidates=[candidate], rejections=[],
    )
    ledger.record_runtime_sample(RuntimeSample(
        request_id="zero-run", provider_id="zero", model_id="model-0",
        route="chat_completions", priority="background", stage="final",
        status_code=200, latency_ms=50, value_per_second=0.0,
    ))
    report = ledger.counterfactual_summary()
    assert report["summary"]["completed"] == 1
    assert report["items"][0]["realized_value_per_hour"] == 0.0
    assert report["items"][0]["realized_regret"] == 0.0
