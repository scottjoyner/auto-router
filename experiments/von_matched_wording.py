#!/usr/bin/env python3
"""Bounded CPU-only matched wording experiment using fictional fleet observations.

Purpose: hold the same state/ground truth fixed across three question templates
and two option orders. No production router imports, real telemetry, dispatch,
model downloads or HTTP. Outputs only allowlisted metadata.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from hashlib import sha256
from pathlib import Path

from von_cpu_pilot import (
    HF_REV, admissible_workspace, check_snapshot, physical_preflight,
    require_resource_scope,
)
from von_choice_shadow import decode_response, question_digest

FAMILIES = {
    "offline": (
        "current signed heartbeat HEALTHY, loaded process and active admission lease",
        "last signed heartbeat OFFLINE; currently not eligible",
    ),
    "lease": (
        "signed active unexpired admission lease, healthy loaded process",
        "admission lease EXPIRED; past approval cannot grant current access",
    ),
    "loaded": (
        "loaded inference model with current process witness, health and lease",
        "model AVAILABLE in catalog only; no current LOADED process witness",
    ),
    "owner": (
        "owner from server-authenticated principal with valid lease and healthy loaded model",
        "owner asserted only by untrusted request metadata, not authenticated",
    ),
}
TEMPLATES = ("terse", "explicit", "authority")
ORDERS = ("forward", "reverse")
MAX_CALLS = 60
ABSTAIN = "defer"
MODEL_ID = "von-1.3.0"


def cases() -> list[dict]:
    """10 independent fictional state families, 6 variants each = 60."""
    states = []
    for family, (good_fact, bad_fact) in FAMILIES.items():
        for correct in ("node_a", "node_b"):
            facts = {
                correct: good_fact,
                "node_b" if correct == "node_a" else "node_a": bad_fact,
            }
            states.append({
                "case_id": f"fictional_{family}_{correct}",
                "family": family,
                "facts": facts,
                "gold": correct,
                "eligible_routes": [correct],
                "complete": True,
            })
    # Deliberately provide no current authorization to either node.
    states += [
        {
            "case_id": f"fictional_missing_{idx}",
            "family": "missing",
            "facts": {
                "node_a": "catalog entry exists, but no signed lease or live heartbeat",
                "node_b": "earlier healthy report, but no recent signed health or owner proof",
            } if idx == 0 else {
                "node_a": "earlier healthy report, but no recent signed health or owner proof",
                "node_b": "catalog entry exists, but no signed lease or live heartbeat",
            },
            "gold": ABSTAIN, "eligible_routes": [], "complete": False,
        }
        for idx in (0, 1)
    ]
    output = []
    for state in states:
        fact_a, fact_b = state["facts"]["node_a"], state["facts"]["node_b"]
        description = (
            "Fictional snapshot for admission. Candidate A: " + fact_a + ". "
            "Candidate B: " + fact_b + ". "
            "Do not assume permissions, current health or loaded execution from "
            "catalog listings or historical observations."
        )
        for template in TEMPLATES:
            if template == "terse":
                instructions = "Choose a route for this request."
                options = {
                    "node_a": "Use Node A",
                    "node_b": "Use Node B",
                    ABSTAIN: "Defer: do not route a request",
                }
            elif template == "explicit":
                instructions = (
                    "Select the ONLY route with proven current authorization, "
                    "fresh signed health and loaded capacity. If no node meets "
                    "these conditions, defer without dispatch."
                )
                options = {
                    "node_a": "Node A. Observed evidence: " + fact_a,
                    "node_b": "Node B. Observed evidence: " + fact_b,
                    ABSTAIN: "Neither runtime is currently proven eligible; do not route",
                }
            else:
                instructions = (
                    "Strict policy: current authenticated evidence is required. "
                    "An old heartbeat, expired lease, catalog AVAILABLE status, "
                    "or client-asserted owner cannot authorize execution. "
                    "Which candidate is admissible, or should the system defer?"
                )
                options = {
                    "node_a": "Admit Node A only if all current requirements pass",
                    "node_b": "Admit Node B only if all current requirements pass",
                    ABSTAIN: "Request independent evidence; admit no runtime",
                }
            for order in ORDERS:
                opts = options if order == "forward" else {
                    "node_b": options["node_b"], "node_a": options["node_a"],
                    ABSTAIN: options[ABSTAIN],
                }
                question = {"state": description, "instructions": instructions,
                            "criteria": opts}
                output.append({
                    "case_id": state["case_id"], "family": state["family"],
                    "gold": state["gold"], "eligible_routes": state["eligible_routes"],
                    "evidence_complete": state["complete"],
                    "template": template, "order": order, **question,
                    "state_sha256": sha256(description.encode()).hexdigest(),
                    "question_sha256": question_digest(question),
                })
    return output


def validate_design(rows: list[dict]) -> None:
    if len(rows) != MAX_CALLS:
        raise ValueError("matched design requires precisely sixty cases")
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["case_id"], row["template"]), []).append(row)
    if len(groups) != 30:
        raise ValueError("must have thirty paired template groups")
    for group in groups.values():
        if len(group) != 2 or {x["order"] for x in group} != set(ORDERS):
            raise ValueError("missing one option-order pair")
        a, b = group
        for field in ("state", "gold", "eligible_routes", "evidence_complete",
                      "instructions", "state_sha256"):
            if a[field] != b[field]:
                raise ValueError("matched facts or authority changed with option order")
        if a["question_sha256"] == b["question_sha256"]:
            raise ValueError("option-order change did not change question fingerprint")
        if set(a["criteria"]) != {"node_a", "node_b", ABSTAIN}:
            raise ValueError("missing answer option")
    for case in {r["case_id"] for r in rows}:
        cohorts = [x for x in rows if x["case_id"] == case]
        if len(cohorts) != 6 or len({x["state_sha256"] for x in cohorts}) != 1:
            raise ValueError("case facts differ between the three templates")


def summarize(rows: list[dict]) -> dict:
    result: dict = {"by_template": {}, "by_family": {}, "order_pairs": 0,
                    "order_changes": 0, "total": len(rows)}
    for template in TEMPLATES:
        items = [x for x in rows if x["template"] == template]
        actionable = [x for x in items if x["evidence_complete"]]
        missing = [x for x in items if not x["evidence_complete"]]
        latencies = sorted(x["latency_ms"] for x in items)
        result["by_template"][template] = {
            "total": len(items), "actionable": len(actionable),
            "correct_routes": sum(x["prediction"] == x["gold"] for x in actionable),
            "wrong_route": sum(x["prediction"] in ("node_a", "node_b")
                               and x["prediction"] != x["gold"] for x in actionable),
            "defer_when_route_exists": sum(x["prediction"] == ABSTAIN for x in actionable),
            "missing_evidence": len(missing),
            "safe_defer_on_missing": sum(x["prediction"] == ABSTAIN for x in missing),
            "unsafe_route_on_missing": sum(x["prediction"] != ABSTAIN for x in missing),
            "mean_selected_confidence": round(
                statistics.mean(x["confidence"] for x in items), 4),
            "median_latency_ms": round(statistics.median(latencies), 2),
            "min_latency_ms": round(min(latencies), 2),
            "max_latency_ms": round(max(latencies), 2),
        }
        for case in {x["case_id"] for x in items}:
            pair = [x for x in items if x["case_id"] == case]
            if len(pair) != 2:
                raise ValueError("incomplete permutation pair")
            result["order_pairs"] += 1
            result["order_changes"] += int(pair[0]["prediction"] != pair[1]["prediction"])
    for family in sorted({x["family"] for x in rows}):
        items = [x for x in rows if x["family"] == family]
        result["by_family"][family] = {
            "total": len(items), "correct": sum(x["prediction"] == x["gold"] for x in items),
            "unauthorized_runtime_suggestions": sum(
                x["prediction"] in ("node_a", "node_b")
                and x["prediction"] not in x["eligible_routes"] for x in items),
        }
    return result


def run(work: Path, dest: Path) -> dict:
    physical_preflight()
    require_resource_scope()
    design = cases()
    validate_design(design)
    if dest.exists() or not dest.resolve(strict=False).is_relative_to(work):
        raise ValueError("result destination must be a new file under private /tmp workspace")
    snap = work / "hf-cache" / "models--wfzyx--von" / "snapshots" / HF_REV
    hashes = check_snapshot(snap)
    os.environ.update({
        "VON_CHECKPOINT_DIR": str(snap), "VON_MODEL_ID": "wfzyx/von",
        "VON_CHAINS_DIR": "off", "VON_ON_OVERFLOW": "refuse",
        "VON_DEVICE": "cpu", "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "",
        "HIP_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2", "TOKENIZERS_PARALLELISM": "false",
    })
    from von.engine import VonEngine

    engine = VonEngine.get_instance(device="cpu")
    completed = []
    for entry in design:
        start = time.perf_counter()
        response = engine.evaluate(
            state=entry["state"], questions={"shadow_0": {
                "type": "choice", "instructions": entry["instructions"],
                "criteria": entry["criteria"],
            }}, model=MODEL_ID,
        )
        duration = round((time.perf_counter() - start) * 1000, 3)
        prediction, confidence = decode_response(
            json.dumps(response.model_dump()).encode(), {},
            {"criteria": entry["criteria"]}, MODEL_ID,
        )
        completed.append({
            key: entry[key] for key in
            ("case_id", "family", "template", "order", "gold",
             "eligible_routes", "evidence_complete", "state_sha256",
             "question_sha256")
        } | {
            "prediction": prediction, "confidence": confidence, "latency_ms": duration,
            "correct": prediction == entry["gold"],
        })
    report = {
        "research_type": "fictional_matched_wording_cpu_inference",
        "hf_commit": HF_REV, "checkpoint_sha256": hashes,
        "total_model_calls": len(completed),
        "summary": summarize(completed), "observations": completed,
        "source_is_fictional": True, "independent_labels": False,
        "signed_execution_attested": False, "safe_for_production": False,
        "route_execution_attempted": False,
        "confidence_is_not_admission": True,
    }
    payload = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    with os.fdopen(os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                          getattr(os, "O_NOFOLLOW", 0), 0o600), "wb") as file:
        file.write(payload)
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True, type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--ack-cpu-impact", action="store_true")
    args = p.parse_args()
    try:
        work = admissible_workspace(args.workspace)
        design = cases()
        validate_design(design)
        if not args.execute:
            print(json.dumps({
                "synthetic_cases": len(design), "distinct_facts": 10,
                "question_templates": list(TEMPLATES), "option_orders": list(ORDERS),
                "actual_inference": False, "preflight": physical_preflight(False),
            }, sort_keys=True))
            return 0
        if not args.ack_cpu_impact or args.out is None:
            raise ValueError("execute requires acknowledgement and new output destination")
        report = run(work, args.out)
        print(json.dumps({"total_model_calls": report["total_model_calls"],
                          "summary": report["summary"],
                          "source_is_fictional": True, "safe_for_production": False},
                         sort_keys=True))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError, KeyError, AssertionError) as exc:
        # Model/server exceptions can include input state; do not echo details.
        print("matched CPU pilot refused/failed: " + type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
