from auto_router.circuit_breaker import CircuitBreakerManager


def test_circuit_opens_after_threshold() -> None:
    circuits = CircuitBreakerManager(failure_threshold=2, cooldown_seconds=30)

    assert circuits.allowed("provider/model") is True
    circuits.record_failure("provider/model", "first")
    assert circuits.allowed("provider/model") is True
    circuits.record_failure("provider/model", "second")

    assert circuits.allowed("provider/model") is False
    snapshot = circuits.snapshot()[0]
    assert snapshot["open"] is True
    assert snapshot["failures"] == 2


def test_circuit_resets_on_success() -> None:
    circuits = CircuitBreakerManager(failure_threshold=1, cooldown_seconds=30)

    circuits.record_failure("provider/model", "boom")
    assert circuits.allowed("provider/model") is False
    circuits.record_success("provider/model")

    assert circuits.allowed("provider/model") is True
    assert circuits.snapshot()[0]["failures"] == 0
