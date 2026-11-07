"""Free-only synthetic routing admission. No live provider calls or billing changes."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Choice:
    decision: str
    reason: str
    provider: str | None = None
    model: str | None = None

def recommend(routes: list[dict], *, data_class: str, in_flight: bool = False) -> Choice:
    if data_class not in ("public_synthetic", "private"):
        return Choice("HOLD", "unknown_data_class")
    if in_flight:
        return Choice("HOLD", "concurrency_occupied")
    # Duplicate identities with conflicting public catalog records fail closed,
    # regardless of ordering; never use the first/last record as implied authority.
    by_identity = {}
    for row in routes:
        if not isinstance(row, dict):
            continue
        identity = (row.get("provider"), row.get("model"))
        if not all(isinstance(v, str) and v.strip() for v in identity):
            continue
        if identity in by_identity and by_identity[identity] != row:
            return Choice("HOLD", "conflicting_duplicate_route_identity")
        by_identity[identity] = row
    candidates = []
    seen = set()
    for row in routes:
        if not isinstance(row, dict):
            continue
        provider, model = row.get("provider"), row.get("model")
        if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
            continue
        if (provider, model) in seen:
            continue
        seen.add((provider, model))
        if row.get("verified_zero_cost") is not True or row.get("last_probe_status") != "PASS":
            continue
        retry_after = row.get("retry_after_seconds")
        if type(retry_after) is not int or retry_after != 0:
            continue
        if row.get("dynamic_pool") is not False or row.get("explicit_id") is not True:
            continue
        if data_class == "private" and provider != "local":
            continue
        if data_class == "public_synthetic" and row.get("data_class") != "public_synthetic":
            continue
        candidates.append((provider, model))
    if not candidates:
        return Choice("HOLD", "no_verified_free_eligible_route")
    provider, model = sorted(candidates)[0]
    return Choice("PASS_TO_SINGLE_PROBE", "eligible_free_only", provider, model)

def classify_http(status_code: int) -> Choice:
    if status_code == 429:
        return Choice("HOLD", "quota_or_rate_limit_no_retry")
    if status_code in (401, 403):
        return Choice("HOLD", "auth_or_policy_no_retry")
    if status_code >= 500:
        return Choice("HOLD", "upstream_failure_no_paid_fallback")
    return Choice("HOLD", "unclassified_response") if status_code != 200 else Choice("PASS_SAMPLE_ONLY", "http_200_not_quality_proof")

def verify_free_receipt(requested_provider, requested_model, receipt) -> Choice:
    if not isinstance(receipt, dict):
        return Choice("HOLD", "invalid_receipt_not_object")
    provider = receipt.get("provider")
    model = receipt.get("model")
    status = receipt.get("status")
    cost_usd = receipt.get("cost_usd")
    completion_tokens = receipt.get("completion_tokens")
    if provider != requested_provider or model != requested_model:
        return Choice("HOLD", "receipt_identity_mismatch")
    if type(status) is not int or status != 200:
        return Choice("HOLD", "receipt_status_not_success")
    if type(cost_usd) not in (int, float) or cost_usd != 0:
        return Choice("HOLD", "receipt_cost_not_zero")
    if type(completion_tokens) is not int or completion_tokens <= 0:
        return Choice("HOLD", "receipt_completion_missing_or_invalid")
    return Choice("PASS_SAMPLE_ONLY", "sample_receipt_verified", requested_provider, requested_model)

def verify_delegation(expected_model, child_sessions, parent, child) -> Choice:
    def _hold(reason: str) -> Choice:
        return Choice("HOLD", reason, None, expected_model)

    # Exactly one child session is allowed for a research-only delegation.
    if type(child_sessions) is not int or child_sessions != 1:
        return _hold("invalid_child_session_count")

    if not isinstance(parent, dict) or not isinstance(child, dict):
        return _hold("invalid_delegation_summary_not_object")

    # Both summaries must carry the exact expected model identity.
    parent_models = parent.get("models")
    child_models = child.get("models")
    if not isinstance(parent_models, list) or not isinstance(child_models, list):
        return _hold("invalid_model_identity_summary")
    if (type(expected_model) is not str or not expected_model.strip()
            or parent_models != [expected_model] or child_models != [expected_model]):
        return _hold("model_identity_mismatch")

    # Reported total cost must be numeric zero (never boolean).
    parent_cost = parent.get("session_reported_cost")
    child_cost = child.get("session_reported_cost")
    if type(parent_cost) not in (int, float) or parent_cost != 0:
        return _hold("parent_cost_not_zero")
    if type(child_cost) not in (int, float) or child_cost != 0:
        return _hold("child_cost_not_zero")

    # No missing cost receipts allowed.
    parent_missing = parent.get("cost_receipts_missing")
    child_missing = child.get("cost_receipts_missing")
    if type(parent_missing) is not int or parent_missing != 0:
        return _hold("parent_cost_receipts_missing")
    if type(child_missing) is not int or child_missing != 0:
        return _hold("child_cost_receipts_missing")

    # Reported token usage must be a positive integer (never boolean).
    parent_tokens = parent.get("token_usage_reported")
    child_tokens = child.get("token_usage_reported")
    if type(parent_tokens) is not int or parent_tokens <= 0:
        return _hold("parent_token_usage_invalid")
    if type(child_tokens) is not int or child_tokens <= 0:
        return _hold("child_token_usage_invalid")

    # Child must have reached a clean stop with no error.
    for label, item in (("parent", parent), ("child", child)):
        finishes=item.get("finish_reasons")
        if not isinstance(finishes, list) or "stop" not in finishes:
            return _hold(label+"_no_stop")
        if any(x not in ("stop", "tool-calls") for x in finishes):
            return _hold(label+"_invalid_finish_reason")
    finish_reasons = child.get("finish_reasons")
    if any(isinstance(r, str) and r.strip().lower().startswith("error") for r in finish_reasons):
        return _hold("child_stopped_with_error")

    return Choice("PASS_RESEARCH_ONLY", "research_only_delegation_verified", None, expected_model)
