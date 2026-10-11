#!/usr/bin/env python3
"""Eight fully fictional Von CPU sanity probes; never imports the production router.

4 ordinary classification controls, 4 typed fleet-choice controls.
Always run under a separate CPU/memory quota and explicit short timeout.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from von_choice_shadow import decode_response
from von_cpu_pilot import (
    HF_REV,
    admissible_workspace,
    check_snapshot,
    physical_preflight,
    require_resource_scope,
)

CASES = [
    ("cls_refund", "general", "A customer requests a refund for a duplicate invoice charge.",
     "Classify the service request category.",
     {"billing": "Invoices, duplicate payments, or refunds",
      "infrastructure": "Outages, infrastructure, server or network failures"}, "billing"),
    ("cls_outage", "general", "The application cannot connect because the network switch is down.",
     "Classify the incident category.",
     {"billing": "Invoices, duplicate payments, or refunds",
      "infrastructure": "Outages, infrastructure, server or network failures"}, "infrastructure"),
    ("cls_account", "general", "The customer wants to change their display name on the account.",
     "Classify the service request category.",
     {"billing": "Invoices, duplicate payments, or refunds",
      "account": "Profile, identity, and display-name changes"}, "account"),
    ("cls_delivery", "general", "Where is the package that I ordered last week?",
     "Classify the service request category.",
     {"fulfillment": "Shipment, order tracking, and delivery status",
      "infrastructure": "Outages, infrastructure, server or network failures"}, "fulfillment"),
    ("route_a", "routing", "Node A: current health OK, valid lease, loaded model. Node B: OFFLINE.",
     "Which runtime has current eligible admission evidence?",
     {"node_a": "Current healthy and loaded runtime A with valid admission lease",
      "node_b": "Offline runtime B, no admission allowed"}, "node_a"),
    ("route_b", "routing", "Node A: OFFLINE. Node B: current health OK, valid lease, loaded model.",
     "Which runtime has current eligible admission evidence?",
     {"node_a": "Offline runtime A, no admission allowed",
      "node_b": "Current healthy and loaded runtime B with valid admission lease"}, "node_b"),
    ("route_lease", "routing",
     "Node A: earlier healthy but lease expired. Node B: current valid lease and loaded model.",
     "Which runtime is currently eligible for admission?",
     {"node_a": "Runtime A has expired authorization",
      "node_b": "Runtime B has an unexpired lease and loaded process"}, "node_b"),
    ("route_loaded", "routing",
     "Node A: loaded model with current execution witness. Node B: only in available catalog.",
     "Which runtime can accept this request right now?",
     {"node_a": "Runtime A has live LOADED model and current capacity",
      "node_b": "Runtime B is only AVAILABLE, not loaded"}, "node_a"),
]


def run(work: Path, out: Path) -> dict:
    physical_preflight()
    require_resource_scope()
    if out.exists() or not out.resolve(strict=False).is_relative_to(work):
        raise ValueError("output must be a new file inside the private /tmp workspace")
    snapshot = work / "hf-cache" / "models--wfzyx--von" / "snapshots" / HF_REV
    hashes = check_snapshot(snapshot)
    os.environ.update({
        "VON_CHECKPOINT_DIR": str(snapshot), "VON_CHAINS_DIR": "off",
        "VON_ON_OVERFLOW": "refuse", "VON_DEVICE": "cpu",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "CUDA_VISIBLE_DEVICES": "", "HIP_VISIBLE_DEVICES": "",
        "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
        "TOKENIZERS_PARALLELISM": "false",
    })
    from von.engine import VonEngine

    engine = VonEngine.get_instance(device="cpu")
    records = []
    for case_id, family, state, instructions, options, gold in CASES:
        start = time.perf_counter()
        response = engine.evaluate(
            state=state, questions={"shadow_0": {
                "type": "choice", "instructions": instructions, "criteria": options,
            }}, model="von-1.3.0",
        )
        milliseconds = round((time.perf_counter() - start) * 1000, 3)
        prediction, confidence = decode_response(
            json.dumps(response.model_dump()).encode(), {},
            {"criteria": options}, "von-1.3.0",
        )
        records.append({
            "case_id": case_id, "family": family, "gold_synthetic": gold,
            "predicted": prediction, "correct": prediction == gold,
            "chosen_probability": confidence, "latency_ms": milliseconds,
        })
    by_family = {
        k: {"total": sum(x["family"] == k for x in records),
            "correct": sum(x["family"] == k and x["correct"] for x in records)}
        for k in ("general", "routing")
    }
    result = {
        "hf_commit": HF_REV, "checkpoint_sha256": hashes, "cases": records,
        "summaries": by_family, "independent_labels": False,
        "execution_attested": False, "source_is_fictional": True,
        "safe_for_production": False, "model_runtime": "von-1.3.0 cpu",
    }
    with os.fdopen(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                          getattr(os, "O_NOFOLLOW", 0), 0o600), "w") as file:
        file.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--out", type=Path)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--ack-cpu-impact", action="store_true")
    a = p.parse_args()
    try:
        work = admissible_workspace(a.workspace)
        if not a.execute:
            print(json.dumps({
                "planned_cases": len(CASES), "inference_attempted": False,
                **physical_preflight(require_capacity=False),
            }))
            return 0
        if not a.ack_cpu_impact or a.out is None:
            raise ValueError("execute requires ack and output")
        result = run(work, a.out)
        print(json.dumps({
            "cases": len(result["cases"]), "summary": result["summaries"],
            "source_is_fictional": True, "safe_for_production": False,
            "file": str(a.out),
        }, sort_keys=True))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print("control pilot failed or refused: " + type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
