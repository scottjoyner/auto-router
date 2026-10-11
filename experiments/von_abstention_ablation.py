#!/usr/bin/env python3
"""Matched 2-option vs 3-option Von Choice ablation; synthetic data only.

Exactly the same state, instruction, A/B descriptions, ordering, and label;
only addition/removal of the explicit defer option changes. 40 model calls,
run offline in a hard CPU/memory-scoped xwing process, never auto-router.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from hashlib import sha256
from pathlib import Path

from von_choice_shadow import decode_response, question_digest
from von_cpu_pilot import (
    HF_REV,
    admissible_workspace,
    check_snapshot,
    physical_preflight,
    require_resource_scope,
)
from von_matched_wording import ABSTAIN, MODEL_ID, cases

CALL_LIMIT = 40
CONDITIONS = ("forced_pair", "abstain_available")


def build() -> list[dict]:
    rows = []
    matched = [x for x in cases() if x["template"] == "explicit"]
    for base in matched:
        for condition in CONDITIONS:
            options = dict(base["criteria"])
            if condition == "forced_pair":
                del options[ABSTAIN]
            query = {"state": base["state"], "instructions": base["instructions"],
                     "criteria": options}
            rows.append({
                "case_id": base["case_id"], "order": base["order"],
                "family": base["family"], "evidence_complete": base["evidence_complete"],
                "eligible_routes": list(base["eligible_routes"]), "gold": base["gold"],
                "condition": condition,
                "state_sha256": sha256(base["state"].encode("utf-8")).hexdigest(),
                "question_sha256": question_digest(query), **query,
            })
    return rows


def validate_design(rows: list[dict]) -> None:
    if len(rows) != CALL_LIMIT:
        raise ValueError("require exactly forty matched source trials")
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["case_id"], row["order"]), []).append(row)
    if len(groups) != 20:
        raise ValueError("require twenty paired prompt contexts")
    for pair in groups.values():
        if len(pair) != 2 or {r["condition"] for r in pair} != set(CONDITIONS):
            raise ValueError("pair must have exactly two option-set conditions")
        no_defer = next(r for r in pair if r["condition"] == "forced_pair")
        with_defer = next(r for r in pair if r["condition"] == "abstain_available")
        for key in ("state", "instructions", "state_sha256", "gold", "eligible_routes",
                    "evidence_complete"):
            if no_defer[key] != with_defer[key]:
                raise ValueError("facts, instructions or authority changed")
        if list(no_defer["criteria"].items()) != [
            (key, desc) for key, desc in with_defer["criteria"].items() if key != ABSTAIN
        ]:
            raise ValueError("only addition of defer is permitted")
        if no_defer["question_sha256"] == with_defer["question_sha256"]:
            raise ValueError("option addition must change question fingerprint")
        if set(no_defer["criteria"]) != {"node_a", "node_b"}:
            raise ValueError("forced pair has extra options")
    if sum(not x["evidence_complete"] for x in rows) != 8:
        raise ValueError("missing-authority cohort must be present")


def summarize(observations: list[dict]) -> dict:
    if len(observations) != CALL_LIMIT:
        raise ValueError("incomplete experiment; do not report selective subset")
    stats = {"conditions": {}, "paired_actionable": 0,
             "paired_forced_correct_defer_abstain": 0,
             "paired_same_runtime_decision": 0, "paired_missing_evidence": 0}
    for condition in CONDITIONS:
        subset = [x for x in observations if x["condition"] == condition]
        actionable = [x for x in subset if x["evidence_complete"]]
        missing = [x for x in subset if not x["evidence_complete"]]
        if len(subset) != 20 or len(actionable) != 16 or len(missing) != 4:
            raise ValueError("incomplete condition cohort")
        stats["conditions"][condition] = {
            "total": len(subset), "actionable": len(actionable),
            "correct_eligible_routes": sum(
                x["prediction"] == x["gold"] for x in actionable),
            "wrong_runtime": sum(x["prediction"] in ("node_a", "node_b")
                                 and x["prediction"] != x["gold"] for x in actionable),
            "defer_even_though_eligible": sum(
                x["prediction"] == ABSTAIN for x in actionable),
            "missing_evidence": len(missing),
            "safe_no_route_on_missing": sum(
                x["prediction"] == ABSTAIN for x in missing),
            "unauthorized_route_on_missing": sum(
                x["prediction"] in ("node_a", "node_b") for x in missing),
            "median_latency_ms": round(statistics.median(
                x["latency_ms"] for x in subset), 2),
            "mean_chosen_probability": round(statistics.mean(
                x["confidence"] for x in subset), 4),
        }
    for key in {(x["case_id"], x["order"]) for x in observations}:
        pair = [x for x in observations if (x["case_id"], x["order"]) == key]
        if len(pair) != 2:
            raise ValueError("incomplete counterfactual pair")
        a = next(x for x in pair if x["condition"] == "forced_pair")
        b = next(x for x in pair if x["condition"] == "abstain_available")
        if a["evidence_complete"]:
            stats["paired_actionable"] += 1
            if a["prediction"] == a["gold"] and b["prediction"] == ABSTAIN:
                stats["paired_forced_correct_defer_abstain"] += 1
            if a["prediction"] == b["prediction"]:
                stats["paired_same_runtime_decision"] += 1
        else:
            stats["paired_missing_evidence"] += 1
    return stats


def run(work: Path, result_path: Path) -> dict:
    physical_preflight()
    require_resource_scope()
    design = build()
    validate_design(design)
    if result_path.exists() or not result_path.resolve(strict=False).is_relative_to(work):
        raise ValueError("new results file must be inside the admitted temporary workspace")
    snapshot = work / "hf-cache" / "models--wfzyx--von" / "snapshots" / HF_REV
    hashes = check_snapshot(snapshot)
    os.environ.update({
        "VON_CHECKPOINT_DIR": str(snapshot), "VON_MODEL_ID": "wfzyx/von",
        "VON_CHAINS_DIR": "off", "VON_ON_OVERFLOW": "refuse",
        "VON_DEVICE": "cpu", "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "",
        "HIP_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2", "TOKENIZERS_PARALLELISM": "false",
    })
    from von.engine import VonEngine

    engine = VonEngine.get_instance(device="cpu")
    records = []
    for row in design:
        start = time.perf_counter()
        answer = engine.evaluate(
            state=row["state"], questions={"shadow_0": {
                "type": "choice", "instructions": row["instructions"],
                "criteria": row["criteria"],
            }}, model=MODEL_ID,
        )
        duration = round((time.perf_counter() - start) * 1000, 3)
        prediction, conf = decode_response(
            json.dumps(answer.model_dump()).encode(), {},
            {"criteria": row["criteria"]}, MODEL_ID,
        )
        records.append({
            key: row[key] for key in (
                "case_id", "order", "family", "evidence_complete", "eligible_routes",
                "gold", "condition", "state_sha256", "question_sha256",
            )
        } | {
            "prediction": prediction, "confidence": conf,
            "latency_ms": duration,
        })
    result = {
        "research_type": "fictional_two_vs_three_choice_cpu_ablation",
        "model": MODEL_ID, "hf_revision": HF_REV, "weight_hashes": hashes,
        "summary": summarize(records), "observations": records,
        "actual_model_calls": len(records), "signed_execution_attested": False,
        "independent_labels": False, "real_fleet_data": False,
        "safe_for_production": False, "routing_changes": False,
    }
    with os.fdopen(os.open(result_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                          getattr(os, "O_NOFOLLOW", 0), 0o600), "w") as output:
        json.dump(result, output, sort_keys=True, indent=2)
        output.write("\n")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True, type=Path)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--ack-cpu-impact", action="store_true")
    p.add_argument("--out", type=Path)
    opts = p.parse_args()
    try:
        work = admissible_workspace(opts.workspace)
        design = build()
        validate_design(design)
        if not opts.execute:
            print(json.dumps({
                "paired_cases": 20, "calls_if_executed": 40,
                "models_loaded": False,
                "capacity": physical_preflight(require_capacity=False),
            }, sort_keys=True))
            return 0
        if not opts.ack_cpu_impact or opts.out is None:
            raise ValueError("missing explicit execution acknowledgement or output file")
        result = run(work, opts.out)
        print(json.dumps({
            "actual_model_calls": result["actual_model_calls"],
            "summary": result["summary"], "safe_for_production": False,
        }, sort_keys=True))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError, KeyError) as error:
        print("ablation refused/failed: " + type(error).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
