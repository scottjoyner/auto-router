#!/usr/bin/env python3
"""Read-only typed-decision shadow scorer. Python stdlib; NO model/network/router calls."""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import statistics
import sys

FIELDS = frozenset({"case_id", "model_id", "model_revision", "task_type", "variant",
                    "state_sha256", "gold", "prediction", "confidence", "eligible_options",
                    "evidence_complete", "latency_ms", "status", "source_kind"})
KINDS = {"noul", "choice", "score"}
VARIANTS = {"original", "permuted", "missing_evidence", "contradiction"}
STATUSES = {"ok", "error", "unavailable"}
SOURCES = {"synthetic", "replayed_trace", "model_observation"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")


def check(row: object) -> dict:
    if not isinstance(row, dict) or set(row) != FIELDS:
        raise ValueError("observation must have exactly the required allowlisted fields")
    for key in ("case_id", "model_id", "model_revision"):
        if not isinstance(row[key], str) or not ID.fullmatch(row[key]):
            raise ValueError("invalid opaque identity: " + key)
    if row["task_type"] not in KINDS or row["variant"] not in VARIANTS:
        raise ValueError("invalid task_type or variant")
    if row["status"] not in STATUSES or row["source_kind"] not in SOURCES:
        raise ValueError("invalid status or source_kind")
    if not isinstance(row["state_sha256"], str) or not DIGEST.fullmatch(row["state_sha256"]):
        raise ValueError("invalid sanitized-state digest")
    if type(row["evidence_complete"]) is not bool:
        raise ValueError("evidence_complete must be bool")
    if row["variant"] == "missing_evidence" and row["evidence_complete"]:
        raise ValueError("missing-evidence variant must be marked incomplete")
    options = row["eligible_options"]
    if not isinstance(options, list) or len(options) > 255:
        raise ValueError("eligible_options must be a list of at most 255")
    if any(not isinstance(x, str) or not ID.fullmatch(x) for x in options):
        raise ValueError("only opaque option labels allowed")
    if len(set(options)) != len(options):
        raise ValueError("duplicate options")
    if row["task_type"] == "noul" and options != ["false", "true"]:
        raise ValueError("noul must use false/true options")
    for field in ("gold", "prediction"):
        value = row[field]
        if value is not None and (not isinstance(value, str) or not ID.fullmatch(value)):
            raise ValueError(field + " must be an opaque ID or null")
    if row["gold"] is not None and row["evidence_complete"] and row["gold"] not in options:
        raise ValueError("gold label not eligible")
    if row["status"] != "ok" and row["prediction"] is not None:
        raise ValueError("unavailable or error must not have a prediction")
    for field in ("confidence", "latency_ms"):
        value = row[field]
        if value is not None and (type(value) not in (float, int) or not math.isfinite(value)):
            raise ValueError("invalid " + field)
    if row["confidence"] is not None and not 0 <= row["confidence"] <= 1:
        raise ValueError("confidence out of range")
    if row["prediction"] is None and row["confidence"] is not None:
        raise ValueError("cannot attach confidence to an abstention")
    if row["latency_ms"] is not None and row["latency_ms"] < 0:
        raise ValueError("latency must be nonnegative")
    return row


def load(path: Path) -> tuple[list[dict], str]:
    if not path.is_file() or path.stat().st_size > 16_000_000:
        raise ValueError("input must be a regular file no larger than 16 MB")
    rows, seen, contracts, originals, revisions, sources = [], set(), {}, {}, {}, {}
    h = sha256()
    with path.open("rb") as source:
        for number, raw in enumerate(source, 1):
            if len(raw) > 32_768:
                raise ValueError(f"line {number} exceeds 32 KiB")
            h.update(raw)
            if not raw.strip():
                continue
            try:
                row = check(json.loads(raw))
                model, case, variant = row["model_id"], row["case_id"], row["variant"]
                key = (case, model, variant)
                if key in seen:
                    raise ValueError("duplicate case/model/variant")
                seen.add(key)
                rev, kind = row["model_revision"], row["source_kind"]
                if model in revisions and revisions[model] != rev:
                    raise ValueError("model revisions mixed under one model_id")
                if model in sources and sources[model] != kind:
                    raise ValueError("source kinds mixed under one model_id")
                revisions[model], sources[model] = rev, kind
                contract = (row["task_type"], row["state_sha256"], row["gold"],
                            row["evidence_complete"], tuple(sorted(row["eligible_options"])))
                cv = (case, variant)
                if cv in contracts and contracts[cv] != contract:
                    raise ValueError("case contract changed between models")
                contracts[cv] = contract
                if variant == "original":
                    originals[(case, model)] = contract
                rows.append(row)
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                raise ValueError(f"line {number}: {e}") from e
    if not rows:
        raise ValueError("empty observation dataset")
    for row in rows:
        if row["variant"] == "permuted":
            anchor = originals.get((row["case_id"], row["model_id"]))
            if anchor is None or anchor != contracts[(row["case_id"], "permuted")]:
                raise ValueError("permutation must have same original case, label and state")
    return rows, h.hexdigest()


def percentile(data: list[float], p: float) -> float | None:
    if not data:
        return None
    a = sorted(data)
    x = (len(a) - 1) * p
    i = int(x)
    return round(a[i] + (a[min(i + 1, len(a) - 1)] - a[i]) * (x - i), 3)


def mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    logs = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            - n * math.log(2) for i in range(min(b, c) + 1)]
    biggest = max(logs)
    return round(min(1.0, 2 * math.exp(biggest) *
                     sum(math.exp(value - biggest) for value in logs)), 6)


def ece(items: list[tuple[float, bool]]) -> float | None:
    if not items:
        return None
    buckets = [[] for _ in range(10)]
    for confidence, correct in items:
        buckets[min(int(confidence * 10), 9)].append((confidence, correct))
    return round(sum(len(bucket) / len(items) *
                     abs(statistics.mean(p for p, _ in bucket) -
                         statistics.mean(int(ok) for _, ok in bucket))
                     for bucket in buckets if bucket), 4)


def score(rows: list[dict], baseline: str) -> dict:
    models: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        models[row["model_id"]].append(row)
    reports = {}
    for model, observations in sorted(models.items()):
        original = [x for x in observations if x["variant"] == "original"]
        action = [x for x in original if x["evidence_complete"] and x["gold"] is not None]
        answered = [x for x in action if x["status"] == "ok" and x["prediction"] is not None]
        correct = sum(x["prediction"] == x["gold"] for x in answered)
        unsafe = [x for x in observations if x["prediction"] is not None and
                  (not x["evidence_complete"] or x["prediction"] not in x["eligible_options"])]
        by_variant = {(x["case_id"], x["variant"]): x for x in observations}
        permuted = [(x, by_variant[(x["case_id"], "permuted")]) for x in original
                    if (x["case_id"], "permuted") in by_variant]
        duration = [float(x["latency_ms"]) for x in original if x["latency_ms"] is not None]
        probs = [(float(x["confidence"]), x["prediction"] == x["gold"]) for x in answered
                 if x["prediction"] in x["eligible_options"] and x["confidence"] is not None]
        reports[model] = {
            "declared_revision": observations[0]["model_revision"],
            "declared_source_kind": observations[0]["source_kind"],
            "original_cases": len(original), "actionable_cases": len(action),
            "answered": len(answered), "correct": correct,
            "accuracy_on_answered": round(correct / len(answered), 4) if answered else None,
            "coverage": round(len(answered) / len(action), 4) if action else None,
            "unavailable_or_error": sum(x["status"] != "ok" for x in original),
            "unsafe_or_ungrounded_output_count": len(unsafe),
            "missing_evidence_outputs": sum(not x["evidence_complete"] for x in unsafe),
            "ineligible_outputs": sum(x["prediction"] not in x["eligible_options"] for x in unsafe),
            "permutation_pairs": len(permuted),
            "permutation_changed": sum(a["prediction"] != b["prediction"] for a, b in permuted),
            "ece_10_bins": ece(probs), "calibration_sample_count": len(probs),
            "latency_ms": {"p50": percentile(duration, .50), "p95": percentile(duration, .95),
                           "p99": percentile(duration, .99), "n": len(duration)},
        }
    comparisons = {}
    if baseline in models:
        anchors = {x["case_id"]: x for x in models[baseline] if x["variant"] == "original"}
        for model, observations in sorted(models.items()):
            if model == baseline:
                continue
            candidate = {x["case_id"]: x for x in observations if x["variant"] == "original"}
            paired = []
            for case, b in anchors.items():
                c = candidate.get(case)
                if c is None:
                    continue
                if not all(x["evidence_complete"] and x["gold"] is not None and
                           x["status"] == "ok" and x["prediction"] in x["eligible_options"]
                           for x in (b, c)):
                    continue
                paired.append((b["prediction"] == b["gold"], c["prediction"] == c["gold"]))
            b_only = sum(b and not c for b, c in paired)
            c_only = sum(c and not b for b, c in paired)
            comparisons[model] = {
                "baseline": baseline, "baseline_cases": len(anchors),
                "candidate_cases": len(candidate), "common_cases": len(anchors.keys() & candidate.keys()),
                "paired_valid": len(paired), "baseline_only_correct": b_only,
                "candidate_only_correct": c_only,
                "exact_mcnemar_p": mcnemar(b_only, c_only) if paired else None,
                "canary_authorized": False,
            }
    return {
        "models": reports, "comparisons_to_baseline": comparisons,
        "execution_attested": False, "promotion_approved": False,
        "notice": "DECLARED model IDs, not execution proof; no authority for routing, admission or claims.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("observations", type=Path)
    ap.add_argument("--baseline", default="baseline")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    try:
        rows, digest = load(args.observations)
        result = score(rows, args.baseline)
        result.update(observation_sha256=digest, row_count=len(rows))
        body = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.out:
            if args.out.resolve() == args.observations.resolve():
                raise ValueError("refusing to overwrite observations")
            args.out.write_text(body, encoding="utf-8")
        else:
            print(body, end="")
        return 0
    except (ValueError, OSError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
