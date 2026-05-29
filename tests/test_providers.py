from datetime import UTC, datetime, timedelta

from auto_router.providers import ProviderError, parse_retry_after


def test_provider_error_carries_retry_metadata() -> None:
    error = ProviderError("limited", status_code=429, retry_after_seconds=42)

    assert error.status_code == 429
    assert error.retryable is True
    assert error.retry_after_seconds == 42


def test_parse_retry_after_seconds() -> None:
    assert parse_retry_after({"retry-after": "12"}) == 12


def test_parse_retry_after_http_date() -> None:
    future = datetime.now(UTC) + timedelta(seconds=30)
    value = future.strftime("%a, %d %b %Y %H:%M:%S GMT")

    parsed = parse_retry_after({"Retry-After": value})

    assert parsed is not None
    assert 0 <= parsed <= 30
