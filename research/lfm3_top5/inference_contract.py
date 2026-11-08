"""Fail-closed, single-attempt LFM3 loopback stage audit (no network I/O)."""
from dataclasses import dataclass
import math
import time
from urllib.parse import urlsplit

@dataclass(frozen=True)
class Sample:
    decision: str
    reason: str
    attempts: int
    latency_ms: float | None

def require_loopback(url: str) -> None:
    try:
        p = urlsplit(url)
        if p.scheme != "http" or p.hostname not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("not_loopback_http")
        if p.username is not None or p.password is not None or p.fragment or p.query:
            raise ValueError("ambiguous_url")
        if p.port is None or p.port < 1 or p.port > 65535:
            raise ValueError("invalid_port")
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_loopback_url") from exc

def sample_once(url: str, caller, *, previous_attempts=0, max_latency_ms=10000,
                clock=time.monotonic, expected_model: str | None = None) -> Sample:
    """One injected call at most. Never retry; never interpret as an SLO sample set."""
    require_loopback(url)
    if type(previous_attempts) is not int or previous_attempts != 0:
        return Sample("HOLD", "already_attempted", 0, None)
    if not math.isfinite(max_latency_ms) or max_latency_ms <= 0:
        raise ValueError("invalid_latency_cap")
    start = clock()
    try:
        response = caller()
    except Exception as exc:
        elapsed = 1000 * (clock() - start)
        return Sample("HOLD", "request_error:" + type(exc).__name__, 1,
                      round(elapsed, 3) if math.isfinite(elapsed) and elapsed >= 0 else None)
    elapsed = 1000 * (clock() - start)
    if not math.isfinite(elapsed) or elapsed < 0:
        return Sample("HOLD", "invalid_clock", 1, None)
    if not isinstance(response, dict) or not isinstance(response.get("choices"), list) or not response["choices"]:
        return Sample("HOLD", "invalid_response", 1, round(elapsed, 3))
    choice = response["choices"][0]
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("content"), str) or not message["content"].strip():
        return Sample("HOLD", "empty_response_content", 1, round(elapsed, 3))
    usage = response.get("usage")
    if (not isinstance(usage, dict)
            or any(type(usage.get(k)) is not int or usage[k] <= 0
                   for k in ("prompt_tokens", "completion_tokens"))):
        return Sample("HOLD", "missing_or_invalid_token_usage", 1, round(elapsed, 3))
    if expected_model is not None and response.get("model") != expected_model:
        return Sample("HOLD", "model_identity_mismatch", 1, round(elapsed, 3))
    if "total_tokens" in usage:
        reported_total_tokens = usage.get("total_tokens")
        if (type(reported_total_tokens) is not int
                or reported_total_tokens != usage.get("prompt_tokens") + usage.get("completion_tokens")):
            return Sample("HOLD", "inconsistent_total_tokens", 1, round(elapsed, 3))
    if elapsed > max_latency_ms:
        return Sample("HOLD", "latency_cap_exceeded", 1, round(elapsed, 3))
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    if finish_reason is None:
        if expected_model is not None:
            return Sample("HOLD", "missing_finish_reason", 1, round(elapsed, 3))
    elif finish_reason == "length":
        return Sample("HOLD", "truncated_response", 1, round(elapsed, 3))
    elif finish_reason != "stop":
        return Sample("HOLD", "nonstop_finish_reason", 1, round(elapsed, 3))
    return Sample("PASS_SAMPLE_ONLY", "one_generation_observed", 1, round(elapsed, 3))
