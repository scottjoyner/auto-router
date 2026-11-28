"""Deterministic no-model-call tests for read-only LFM3 probe receipts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[1] / "scripts/joyner_lfm3_probe_diagnostics.py"
spec = importlib.util.spec_from_file_location("joyner_lfm3_probe_diagnostics", FILE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample(case="a", *, ok=True, exact=True, text="12", tokens=4):
    return {
        "case_id": case,
        "ok": ok,
        "strict_exact_match": exact,
        "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "completion_tokens": tokens,
    }


def receipt(*samples, model="LFM3-1.7B-QAD-Q4_0"):
    return {"schema": module.SCHEMA, "node": "joyner",
            "model": model, "probe_count": len(samples), "samples": list(samples)}


def test_categorizes_exact_and_nonempty_mismatch():
    r = module.summarize([receipt(sample(), sample("b", exact=False, text="11"))], 64)
    assert r["totals"]["exact"] == 1
    assert r["totals"]["nonempty_mismatch"] == 1
    assert r["deployment_gate"] == "HOLD"
    assert r["model_requests_made"] == 0


def test_empty_at_cap_only_suggests_exhaustion():
    r = module.summarize([receipt(sample(exact=False, text="", tokens=64))], 64)
    assert r["totals"]["possible_budget_exhaustion"] == 1
    assert "only a truncation candidate" in r["interpretation"]


def test_empty_under_cap_separately_identified():
    r = module.summarize([receipt(sample(exact=False, text="", tokens=30))], 64)
    assert r["totals"]["empty_under_budget"] == 1


def test_failed_http_request_not_counted_as_answer():
    r = module.summarize([receipt(sample(ok=False, exact=False))], 64)
    assert r["totals"]["http_failure"] == 1


@pytest.mark.parametrize("bad", [0, -1, 8193])
def test_cap_bounded(bad):
    with pytest.raises(ValueError):
        module.summarize([receipt(sample())], bad)


def test_fails_closed_on_duplicate_case_ids():
    with pytest.raises(ValueError, match="duplicated"):
        module.summarize([receipt(sample(), sample())], 64)


def test_fails_closed_on_inconsistent_receipt_count():
    r = receipt(sample())
    r["probe_count"] = 2
    with pytest.raises(ValueError, match="probe count"):
        module.summarize([r], 64)


def test_fails_closed_on_mixed_models():
    with pytest.raises(ValueError, match="mixed model"):
        module.summarize([receipt(sample()), receipt(sample(), model="other")], 64)


def test_fails_closed_on_invalid_digest():
    s = sample()
    s["response_sha256"] = "nonhex" * 10 + "xxxx"
    with pytest.raises(ValueError, match="digest"):
        module.summarize([receipt(s)], 64)


def test_fails_closed_on_nonboolean_success():
    s = sample()
    s["ok"] = 1
    with pytest.raises(ValueError, match="boolean"):
        module.summarize([receipt(s)], 64)


def test_load_uses_last_batches_and_rejects_empty(tmp_path):
    p = tmp_path / "receipts.jsonl"
    with pytest.raises(ValueError, match="missing"):
        module.load_receipts(p, 2)
    rs = [receipt(sample(str(i))) for i in range(3)]
    p.write_text("\n".join(json.dumps(r) for r in rs) + "\n")
    assert module.load_receipts(p, 2) == rs[-2:]
    assert module.load_receipts(p, 3) == rs
    p.write_text("")
    with pytest.raises(ValueError, match="empty"):
        module.load_receipts(p, 2)


def test_rejects_unexpected_schema_or_node():
    r = receipt(sample())
    r["node"] = "different"
    with pytest.raises(ValueError, match="node"):
        module.summarize([r], 64)
    r["node"] = "joyner"
    r["schema"] = "unknown"
    with pytest.raises(ValueError, match="schema"):
        module.summarize([r], 64)
