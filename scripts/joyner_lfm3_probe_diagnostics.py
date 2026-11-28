"""Offline, privacy-safe diagnosis of already-recorded Joyner LFM3 probe receipts.

No model calls, credential access, configuration changes, or gate promotion.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

SCHEMA = "sensorgraft.lfm3.online-probe.v1"
EMPTY_HASH = hashlib.sha256(b"").hexdigest()
MAX_INPUT_BYTES = 16_000_000
MAX_LINE_BYTES = 1_000_000


def load_receipts(path: Path, last_batches: int) -> list[dict]:
    if not 1 <= last_batches <= 4096:
        raise ValueError("last_batches outside bounded range")
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("missing or oversized receipt file")
    parsed = []
    with path.open("rb") as stream:
        for raw in stream:
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError("oversized receipt line")
            if raw.strip():
                parsed.append(json.loads(raw))
    if not parsed:
        raise ValueError("empty receipt file")
    return parsed[-last_batches:]


def classify(sample: dict, cap: int) -> str:
    if type(sample.get("ok")) is not bool:
        raise ValueError("missing boolean HTTP outcome")
    if not sample["ok"]:
        return "http_failure"
    if type(sample.get("strict_exact_match")) is not bool:
        raise ValueError("missing boolean exact-match outcome")
    tokens = sample.get("completion_tokens")
    sha = sample.get("response_sha256")
    if type(tokens) is not int or tokens < 0:
        raise ValueError("missing completion token count")
    if not isinstance(sha, str) or len(sha) != 64:
        raise ValueError("missing output digest")
    if any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("invalid output digest")
    if sample["strict_exact_match"]:
        return "exact"
    if sha == EMPTY_HASH and tokens >= cap:
        return "possible_budget_exhaustion"
    if sha == EMPTY_HASH:
        return "empty_under_budget"
    return "nonempty_mismatch"


def summarize(records: list[dict], completion_cap: int) -> dict:
    if not 1 <= completion_cap <= 8192:
        raise ValueError("invalid externally supplied completion cap")
    totals: Counter = Counter()
    per_case: dict[str, Counter] = defaultdict(Counter)
    models: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or record.get("schema") != SCHEMA:
            raise ValueError("unexpected receipt schema")
        if record.get("node") != "joyner":
            raise ValueError("unexpected node identity")
        model = record.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError("model identity absent")
        models.add(model)
        samples = record.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError("samples unavailable")
        if record.get("probe_count") != len(samples):
            raise ValueError("inconsistent probe count")
        seen: set[str] = set()
        for sample in samples:
            if not isinstance(sample, dict):
                raise ValueError("invalid sample")
            case = sample.get("case_id")
            if not isinstance(case, str) or not case or case in seen:
                raise ValueError("missing or duplicated case identifier")
            seen.add(case)
            category = classify(sample, completion_cap)
            totals[category] += 1
            per_case[case][category] += 1
    if len(models) != 1:
        raise ValueError("mixed model identities")
    names = ("exact", "possible_budget_exhaustion", "empty_under_budget",
             "nonempty_mismatch", "http_failure")
    return {
        "schema": "lfm3.joyner.offline-probe-diagnostics.v1",
        "node": "joyner", "model": next(iter(models)),
        "research_only": True, "deployment_gate": "HOLD",
        "model_requests_made": 0, "raw_text_persisted": False,
        "receipt_batches": len(records), "requests": sum(totals.values()),
        "completion_cap_supplied": completion_cap,
        "totals": {k: totals[k] for k in names},
        "by_case": {case: {k: stats[k] for k in names}
                    for case, stats in sorted(per_case.items())},
        "interpretation": ("Empty answer digest plus tokens at supplied cap is "
                           "only a truncation candidate; finish_reason and "
                           "reasoning tokens were not recorded."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("receipts", type=Path)
    ap.add_argument("--last-batches", type=int, default=48)
    ap.add_argument("--completion-cap", type=int, required=True)
    args = ap.parse_args()
    try:
        result = summarize(load_receipts(args.receipts, args.last_batches),
                           args.completion_cap)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        ap.exit(2, "HOLD: unable to validate receipts: " + str(exc) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
