#!/usr/bin/env python3
"""Verify two pinned, fictional Von inference reports and compare no-action policies.

NO model import, network, remote shell, production router, raw prompt export,
NAS writes or changes to authoritative admission. Not an attestation verifier.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from hashlib import sha256
from pathlib import Path

from von_abstention_ablation import build as ablation_cases
from von_abstention_ablation import summarize as ablation_summary
from von_cpu_pilot import HF_REV, WEIGHTS
from von_matched_wording import ABSTAIN, cases as wording_cases
from von_matched_wording import summarize as wording_summary

REPORT_DIGESTS = {
    "wording": "18b70de2c4dac8b264d53dd4234b3600dd8df6f7b1dc6bbcf2a0b6d240f31c7f",
    "ablation": "4a3a42bee2beaa8b4fede40c7564c6aeccb9817627776e6f9daf59a336f57028",
}
MAX_REPORT_BYTES = 400_000
OBS_FIELDS = {
    "wording": frozenset({
        "case_id", "family", "template", "order", "gold", "eligible_routes",
        "evidence_complete", "state_sha256", "question_sha256",
        "prediction", "confidence", "latency_ms", "correct",
    }),
    "ablation": frozenset({
        "case_id", "family", "order", "gold", "eligible_routes",
        "evidence_complete", "condition", "state_sha256", "question_sha256",
        "prediction", "confidence", "latency_ms",
    }),
}
FIXED_KEYS = {
    "wording": OBS_FIELDS["wording"] - {
        "prediction", "confidence", "latency_ms", "correct",
    },
    "ablation": OBS_FIELDS["ablation"] - {
        "prediction", "confidence", "latency_ms",
    },
}
REPORT_KEYS = {
    "wording": frozenset({
        "research_type", "hf_commit", "checkpoint_sha256", "total_model_calls",
        "summary", "observations", "source_is_fictional", "independent_labels",
        "signed_execution_attested", "safe_for_production",
        "route_execution_attempted", "confidence_is_not_admission",
    }),
    "ablation": frozenset({
        "research_type", "model", "hf_revision", "weight_hashes",
        "summary", "observations", "actual_model_calls",
        "signed_execution_attested", "independent_labels", "real_fleet_data",
        "safe_for_production", "routing_changes",
    }),
}


def checked_json(path: Path, kind: str) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("reports must be existing regular files")
    size = path.stat().st_size
    if size <= 0 or size > MAX_REPORT_BYTES:
        raise ValueError("report exceeds bounded evidence size or is empty")
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    if digest != REPORT_DIGESTS[kind]:
        raise ValueError("pinned report SHA-256 mismatch: " + kind)
    obj = json.loads(raw)
    if not isinstance(obj, dict) or set(obj) != REPORT_KEYS[kind]:
        raise ValueError("unexpected report schema: " + kind)
    return obj, digest


def _canonical_key(row: dict, kind: str) -> tuple:
    if kind == "wording":
        return (row["case_id"], row["template"], row["order"])
    return (row["case_id"], row["order"], row["condition"])


def _finite_probability(value: object) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 1


def validate_records(report: dict, kind: str) -> list[dict]:
    if kind == "wording":
        if (report["hf_commit"] != HF_REV or
                report["research_type"] != "fictional_matched_wording_cpu_inference" or
                report["total_model_calls"] != 60 or
                report["source_is_fictional"] is not True or
                report["confidence_is_not_admission"] is not True or
                report["route_execution_attempted"] is not False):
            raise ValueError("wording provenance or safety claim mismatch")
        for name, digest in WEIGHTS.items():
            if report["checkpoint_sha256"].get(name) != digest:
                raise ValueError("wording checkpoint component mismatch")
        expected = wording_cases()
        summarize = wording_summary
    else:
        if (report["hf_revision"] != HF_REV or
                report["research_type"] != "fictional_two_vs_three_choice_cpu_ablation" or
                report["model"] != "von-1.3.0" or
                report["actual_model_calls"] != 40 or
                report["real_fleet_data"] is not False or
                report["routing_changes"] is not False):
            raise ValueError("ablation provenance or safety claim mismatch")
        for name, digest in WEIGHTS.items():
            if report["weight_hashes"].get(name) != digest:
                raise ValueError("ablation checkpoint component mismatch")
        expected = ablation_cases()
        summarize = ablation_summary
    if (report["independent_labels"] is not False or
            report["signed_execution_attested"] is not False or
            report["safe_for_production"] is not False):
        raise ValueError("report must not claim trust or production admission")
    obs = report["observations"]
    if not isinstance(obs, list) or len(obs) != len(expected):
        raise ValueError("incomplete or unexpected model-observation cohort")
    expected_by_key = {_canonical_key(x, kind): x for x in expected}
    if len(expected_by_key) != len(expected):
        raise ValueError("nonunique code-generated case keys")
    seen = set()
    for row in obs:
        if not isinstance(row, dict) or set(row) != OBS_FIELDS[kind]:
            raise ValueError("unexpected observation metadata fields")
        key = _canonical_key(row, kind)
        if key in seen or key not in expected_by_key:
            raise ValueError("duplicate or unknown observation key")
        seen.add(key)
        frozen = expected_by_key[key]
        if any(row[field] != frozen[field] or type(row[field]) is not type(frozen[field])
               for field in FIXED_KEYS[kind]):
            raise ValueError("model result does not match the pinned fictional source case")
        choice = row["prediction"]
        choices = {"node_a", "node_b", ABSTAIN}
        if kind == "ablation" and row["condition"] == "forced_pair":
            choices.remove(ABSTAIN)
        if type(choice) is not str or choice not in choices:
            raise ValueError("invalid declared model prediction")
        if not _finite_probability(row["confidence"]):
            raise ValueError("missing or invalid chosen probability")
        t = row["latency_ms"]
        if type(t) not in (float, int) or not math.isfinite(t) or t < 0:
            raise ValueError("missing or invalid decision latency")
        if kind == "wording":
            if type(row["correct"]) is not bool or row["correct"] != (choice == row["gold"]):
                raise ValueError("fabricated result correctness flag inconsistent")
    if len(seen) != len(expected_by_key):
        raise ValueError("one or more expected model responses absent")
    # Independently recompute the publisher's aggregate, rejecting tampered totals.
    if summarize(obs) != report["summary"]:
        raise ValueError("reported aggregates diverge from individual observations")
    return obs


def deterministic_policy(eligible: list[str]) -> str:
    """Fail-closed; covers ONLY unique eligible choices, never authorizes a candidate."""
    if len(eligible) == 1 and eligible[0] in ("node_a", "node_b"):
        return eligible[0]
    return ABSTAIN


def gate_model_answer(prediction: str, eligible: list[str]) -> str:
    """For diagnostic evaluation only. Does NOT issue a production route or token."""
    return prediction if prediction in eligible else ABSTAIN


def metrics(rows: list[dict], *, group: str) -> dict:
    cohort = []
    for item in rows:
        allowed = item["eligible_routes"]
        chosen = item["prediction"]
        policy = deterministic_policy(allowed)
        gated = gate_model_answer(chosen, allowed)
        cohort.append({
            "source": item, "policy": policy, "model": chosen,
            "gated": gated,
        })
    with_route = [x for x in cohort if x["source"]["eligible_routes"]]
    without = [x for x in cohort if not x["source"]["eligible_routes"]]
    def count(part: list[dict], name: str, target: str) -> int:
        return sum(x[name] == x["source"][target] for x in part)
    def invalid(part: list[dict], name: str) -> int:
        return sum(x[name] not in x["source"]["eligible_routes"] and
                   x[name] != ABSTAIN for x in part)
    unique_contexts = {
        x["source"]["case_id"] for x in cohort
    }
    return {
        "group": group,
        "variant_rows": len(cohort), "unique_fictional_situations": len(unique_contexts),
        "actionable_variants": len(with_route),
        "missing_authority_variants": len(without),
        "multi_eligible_variants": sum(
            len(x["source"]["eligible_routes"]) > 1 for x in cohort),
        "deterministic_policy": {
            "correct_actionable": count(with_route, "policy", "gold"),
            "safe_deferrals_missing": sum(x["policy"] == ABSTAIN for x in without),
            "unsafe_runtime_suggestions": invalid(cohort, "policy"),
        },
        "model_raw": {
            "correct_actionable": count(with_route, "model", "gold"),
            "safe_deferrals_missing": sum(x["model"] == ABSTAIN for x in without),
            "defer_despite_eligible": sum(x["model"] == ABSTAIN for x in with_route),
            "unsafe_runtime_suggestions": invalid(cohort, "model"),
        },
        "model_proposal_gated": {
            "correct_actionable": count(with_route, "gated", "gold"),
            "safe_deferrals_missing": sum(x["gated"] == ABSTAIN for x in without),
            "defer_despite_eligible": sum(x["gated"] == ABSTAIN for x in with_route),
            "unsafe_runtime_suggestions": invalid(cohort, "gated"),
        },
        "interpretation": (
            "Unique simulated eligible_routes are derived from a fictional "
            "generator, not signed runtime authority. There are zero "
            "multi-eligible cases, so incremental ranking value is not measurable."
        ),
    }


def comparison(wording: list[dict], ablation: list[dict]) -> dict:
    by_template = {
        template: metrics([x for x in wording if x["template"] == template],
                          group="wording_" + template)
        for template in ("terse", "explicit", "authority")
    }
    by_condition = {
        condition: metrics([x for x in ablation if x["condition"] == condition],
                           group="ablation_" + condition)
        for condition in ("forced_pair", "abstain_available")
    }
    all_report = {
        "schema": "fictional_routing_policy_value_v1",
        "pin": {"model_hf_revision": HF_REV, "reports_sha256": REPORT_DIGESTS},
        "source_claim": "real CPU inference on entirely fictional, correlated cases",
        "metadata_only": True, "real_fleet_case_count": 0,
        "independently_verified_labels": 0,
        "hardware_execution_attested": False, "authenticated_user_receipts": False,
        "action_admission_performed": False,
        "source_observation_count": len(wording) + len(ablation),
        "unique_fictional_situations_total": len({
            r["case_id"] for r in wording + ablation
        }),
        "by_template": by_template, "by_condition": by_condition,
        "conclusion": (
            "The deterministic singleton-eligible-or-defer policy solves all "
            "simulated cases without Von. The experiments contain no multiple-"
            "eligible-choice cases and do not demonstrate incremental routing value. "
            "Do not generalize to real fleets or grant admission authority."
        ),
        "production_approval": False,
    }
    return all_report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wording", type=Path, required=True)
    p.add_argument("--ablation", type=Path, required=True)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    try:
        w, _ = checked_json(args.wording, "wording")
        a, _ = checked_json(args.ablation, "ablation")
        wrows = validate_records(w, "wording")
        arows = validate_records(a, "ablation")
        result = comparison(wrows, arows)
        encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.out is None:
            print(encoded, end="")
        else:
            output = args.out.resolve(strict=False)
            if (not output.is_relative_to(Path("/tmp")) or output == Path("/tmp") or
                    args.out.is_symlink() or output in (args.wording.resolve(),
                                                      args.ablation.resolve())):
                raise ValueError("output must be a new regular file under /tmp")
            with os.fdopen(os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                                  getattr(os, "O_NOFOLLOW", 0), 0o600), "w") as handle:
                handle.write(encoded)
        return 0
    except (ValueError, OSError, json.JSONDecodeError, KeyError, TypeError) as err:
        # Do not echo low-trust file path or exception text containing raw data.
        print("offline comparison refused: " + type(err).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
