#!/usr/bin/env python3
"""Hash-pin 48 actual Von CPU Choice responses and report separated objective units.

No inference, model packages, network, production router, or NAS access.
Hash identity and typed schemas are necessary but NOT physical attestation.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

from von_cpu_pilot import HF_REV, WEIGHTS
from von_multi_eligible_cpu import analyze, build

REPORT_SHA256 = "216cdb6753ac87f6d9197435d27db24f2971ca0b390948617197f94234313423"
EXPECTED_FIELDS = frozenset({
    "research_type", "hf_revision", "weights_sha256", "cases", "summary",
    "synthetic_cases", "real_authority_evidence", "independent_labels",
    "signed_execution_attested", "safe_for_production", "new_router_policy",
    "cpu_model_calls", "latency_and_energy_units_are_distinct",
})
ROW_FIELDS = frozenset({
    "case_id", "objective", "template", "order", "facts_sha256", "question_sha256",
    "eligible_options", "gold", "primitive_choice", "prediction", "confidence",
    "decision_latency_ms", "regret_model", "regret_primitive",
    "expected_a", "expected_b",
})
SOURCE_FIELDS = {
    "case_id", "objective", "template", "order", "facts_sha256", "question_sha256",
    "eligible_options", "gold", "primitive_choice", "expected_a", "expected_b",
}


def read_report(path: Path) -> dict:
    if not path.is_file() or path.is_symlink() or not (0 < path.stat().st_size <= 150_000):
        raise ValueError("pinned report must be a small regular file")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != REPORT_SHA256:
        raise ValueError("original xwing report checksum mismatch")
    return json.loads(raw)


def verify(report: dict) -> list[dict]:
    if not isinstance(report, dict) or set(report) != EXPECTED_FIELDS:
        raise ValueError("unexpected report schema")
    if (report["research_type"] != "fictional_two_eligible_objective_cpu_choice"
            or report["hf_revision"] != HF_REV or report["cpu_model_calls"] != 48
            or any(report[k] is not False for k in (
                "real_authority_evidence", "independent_labels",
                "signed_execution_attested", "safe_for_production", "new_router_policy"))
            or report["synthetic_cases"] is not True
            or report["latency_and_energy_units_are_distinct"] is not True):
        raise ValueError("provenance or safety boundary invalid")
    if not isinstance(report["weights_sha256"], dict) or any(
            report["weights_sha256"].get(k) != v for k, v in WEIGHTS.items()):
        raise ValueError("weight revision/digest mismatch")
    desired = {(r["case_id"], r["template"], r["order"]): r for r in build()}
    rows = report["cases"]
    if not isinstance(rows, list) or len(rows) != len(desired):
        raise ValueError("source observations incomplete")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_FIELDS:
            raise ValueError("unexpected output data keys")
        key = (row["case_id"], row["template"], row["order"])
        if key in seen or key not in desired:
            raise ValueError("duplicate or unrecognized evaluation case")
        seen.add(key)
        ref = desired[key]
        if any(type(row[k]) is not type(ref[k]) or row[k] != ref[k]
               for k in SOURCE_FIELDS):
            raise ValueError("source metrics, label, or contract changed")
        if row["prediction"] not in ("node_a", "node_b"):
            raise ValueError("invalid Choice output")
        if (type(row["confidence"]) not in (int, float)
                or not math.isfinite(row["confidence"])
                or not 0 <= row["confidence"] <= 1):
            raise ValueError("invalid reported confidence")
        t = row["decision_latency_ms"]
        if type(t) not in (int, float) or not math.isfinite(t) or t < 0:
            raise ValueError("invalid inference duration")
        low = min(row["expected_a"], row["expected_b"])
        model = row["expected_a" if row["prediction"] == "node_a" else "expected_b"]
        naive = row["expected_a" if row["primitive_choice"] == "node_a" else "expected_b"]
        if (abs(row["regret_model"] - round(model - low, 5)) > 0.00001
                or abs(row["regret_primitive"] - round(naive - low, 5)) > 0.00001):
            raise ValueError("regret measurement inconsistent with source values")
    if len(seen) != len(desired) or analyze(rows) != report["summary"]:
        raise ValueError("aggregate did not match all source model responses")
    return rows


def reconcile(rows: list[dict]) -> dict:
    by_template: dict = {}
    for template in ("raw", "derived"):
        by_objective = {}
        for objective in ("latency_ms", "energy_j"):
            subset = [x for x in rows if x["template"] == template
                      and x["objective"] == objective]
            if len(subset) != 12:
                raise ValueError("incomplete template/objective stratum")
            times = [x["decision_latency_ms"] for x in subset]
            row = {
                "variant_rows": len(subset),
                "independent_fictional_profiles": len({x["case_id"] for x in subset}),
                "objective_unit": "milliseconds" if objective == "latency_ms" else "joules",
                "exact_objective_oracle_correct": len(subset),
                "naive_deterministic_correct": sum(
                    x["primitive_choice"] == x["gold"] for x in subset),
                "von_correct": sum(x["prediction"] == x["gold"] for x in subset),
                "von_selection_counts": dict(Counter(x["prediction"] for x in subset)),
                "von_regret_total_in_objective_units": round(
                    sum(x["regret_model"] for x in subset), 4),
                "naive_regret_total_in_objective_units": round(
                    sum(x["regret_primitive"] for x in subset), 4),
                "median_inference_latency_ms": round(statistics.median(times), 3),
                "mean_chosen_probability": round(
                    statistics.mean(x["confidence"] for x in subset), 4),
            }
            if objective == "latency_ms":
                row["latency_gain_over_naive_after_model_decision_ms"] = round(
                    sum(x["regret_primitive"] - x["regret_model"]
                        - x["decision_latency_ms"] for x in subset), 3)
            else:
                row["model_inference_energy_measured"] = False
                row["net_energy_advantage_claimed"] = False
            by_objective[objective] = row
        by_template[template] = by_objective
    result = {
        "schema": "fictional_two_eligible_von_cost_audit_v1",
        "report_sha256": REPORT_SHA256,
        "hf_model_revision": HF_REV,
        "actual_cpu_inference_observations": len(rows),
        "unique_fictional_situations": 12,
        "distinct_objectives": ["latency_ms", "energy_j"],
        "each_fictional_case_has_two_generator_declared_eligible_nodes": True,
        "authenticated_real_eligibility_evidence": False,
        "independently_labelled_real_cases": 0,
        "signed_physical_execution_receipts": False,
        "production_admission_enabled": False,
        "objective_units_are_never_combined": True,
        "breakdown": by_template,
        "interpretation": (
            "Exact deterministic arithmetic is optimal for these invented fully observed "
            "objectives by construction. Von and a simpler heuristic are compared "
            "only to that registered numeric oracle. The small synthetic sample "
            "cannot prove operational ranking quality or authenticated eligibility."
        ),
    }
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    try:
        original = read_report(args.report)
        result = reconcile(verify(original))
        blob = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.out is None:
            print(blob, end="")
        else:
            output = args.out.resolve(strict=False)
            if (not output.is_relative_to(Path("/tmp")) or output == Path("/tmp")
                    or args.out.is_symlink()
                    or output == args.report.resolve()):
                raise ValueError("output must be new and under /tmp")
            with os.fdopen(os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                                  getattr(os, "O_NOFOLLOW", 0), 0o600), "w") as file:
                file.write(blob)
        return 0
    except (ValueError, KeyError, OSError, TypeError, json.JSONDecodeError) as err:
        print("two-eligible evaluation refused: " + type(err).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
