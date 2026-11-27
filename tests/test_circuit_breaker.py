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


def test_explicit_retry_window_blocks_first_failure(monkeypatch):
    monkeypatch.setattr('auto_router.circuit_breaker.time.time', lambda: 100)
    circuits = CircuitBreakerManager(failure_threshold=3, cooldown_seconds=60)
    circuits.record_failure('p/m', 'rate limited', retry_after=120)
    assert not circuits.allowed('p/m')
    assert circuits.snapshot()[0]['opened_until'] == 220


def test_later_failure_cannot_shorten_open_window(monkeypatch):
    now = [100]
    monkeypatch.setattr('auto_router.circuit_breaker.time.time', lambda: now[0])
    circuits = CircuitBreakerManager(failure_threshold=1, cooldown_seconds=60)
    circuits.record_failure('p/m', 'rate limited', retry_after=120)
    now[0] = 105
    circuits.record_failure('p/m', 'late error', retry_after=1)
    assert circuits.snapshot()[0]['opened_until'] == 220


def test_late_success_cannot_clear_retry_window(monkeypatch):
    now = [100]
    monkeypatch.setattr('auto_router.circuit_breaker.time.time', lambda: now[0])
    circuits = CircuitBreakerManager(failure_threshold=1)
    circuits.record_failure('p/m', 'rate limited', retry_after=120)
    circuits.record_success('p/m')
    assert not circuits.allowed('p/m')
    now[0] = 220
    assert circuits.allowed('p/m')
    circuits.record_success('p/m')
    assert circuits.snapshot()[0]['failures'] == 0


def test_invalid_retry_interval_cannot_create_negative_window(monkeypatch):
    monkeypatch.setattr('auto_router.circuit_breaker.time.time', lambda: 100)
    circuits = CircuitBreakerManager(failure_threshold=1, cooldown_seconds=60)
    circuits.record_failure('p/m', 'bad retry hint', retry_after=-5)
    assert circuits.snapshot()[0]['opened_until'] == 160
