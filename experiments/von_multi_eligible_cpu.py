#!/usr/bin/env python3
"""48-call fictional MULTI-ELIGIBLE Von Choice objective experiment.

Both fictional candidates are independently declared admitted. A pure numeric
oracle computes latency or energy from invented device attributes. This tests
ranking, NOT live admission, source provenance, or real service predictions.
Default mode never loads weights. Execution requires an externally enforced
CPU/memory cgroup, checked pinned model, low priority and explicit consent.
"""
from __future__ import annotations

import argparse
import json
import math
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

MODEL_ID = "von-1.3.0"
TEMPLATES = ("raw", "derived")
ORDERS = ("forward", "reverse")
MAX_CALLS = 48
# Pure fiction. (ID, objective, output tokens, A: ttft/tps/watts, B: ditto).
# Both candidates are healthy, LOADED, owner-authorized, leased and have capacity.
# We do NOT model queuing, caching, model quality, thermal or power overhead.
PROFILES = (
    ("latency01", "latency_ms", 32, (80, 40, 65), (250, 100, 100)),
    ("latency02", "latency_ms", 32, (55, 70, 100), (130, 45, 60)),
    ("latency03", "latency_ms", 512, (400, 120, 150), (60, 75, 85)),
    ("latency04", "latency_ms", 512, (40, 45, 100), (400, 160, 180)),
    ("latency05", "latency_ms", 1024, (120, 100, 150), (30, 110, 90)),
    ("latency06", "latency_ms", 128, (30, 95, 110), (300, 80, 70)),
    ("energy01", "energy_j", 64, (50, 100, 150), (120, 80, 55)),
    ("energy02", "energy_j", 64, (100, 90, 48), (60, 120, 130)),
    ("energy03", "energy_j", 768, (100, 150, 160), (100, 80, 65)),
    ("energy04", "energy_j", 768, (200, 65, 55), (100, 160, 140)),
    ("energy05", "energy_j", 256, (100, 80, 75), (50, 160, 140)),
    ("energy06", "energy_j", 256, (300, 130, 85), (100, 90, 90)),
)


def expected(profile: tuple[int, int, int], tokens: int, objective: str) -> float:
    ttft, tps, watts = profile
    seconds = ttft / 1000 + tokens / tps
    return seconds * (1000 if objective == "latency_ms" else watts)


def oracle(profile: tuple) -> str:
    _, objective, tokens, a, b = profile
    x, y = expected(a, tokens, objective), expected(b, tokens, objective)
    if math.isclose(x, y, rel_tol=1e-9):
        raise ValueError("source profile objective tie")
    return "node_a" if x < y else "node_b"


def primitive_heuristic(profile: tuple) -> str:
    """A cheap but imperfect deterministic comparator (not the exact optimizer)."""
    _, objective, _, a, b = profile
    if objective == "latency_ms":
        return "node_a" if a[1] >= b[1] else "node_b"  # higher tps only
    return "node_a" if a[2] <= b[2] else "node_b"  # fewer watts only


def canonical_facts(profile: tuple) -> dict:
    id_, objective, tokens, a, b = profile
    return {
        "case_id": id_, "objective": objective, "output_tokens": tokens,
        "node_a": {"ttft_ms": a[0], "generation_tokens_s": a[1], "watts": a[2]},
        "node_b": {"ttft_ms": b[0], "generation_tokens_s": b[1], "watts": b[2]},
        "eligible_options": ["node_a", "node_b"],
    }


def build() -> list[dict]:
    rows = []
    for profile in PROFILES:
        id_, objective, tokens, a, b = profile
        fact = canonical_facts(profile)
        fact_text = json.dumps(fact, sort_keys=True, separators=(",", ":"))
        digest = sha256(fact_text.encode()).hexdigest()
        gold = oracle(profile)
        computed = {"node_a": expected(a, tokens, objective),
                    "node_b": expected(b, tokens, objective)}
        for template in TEMPLATES:
            if template == "raw":
                state = (
                    "Fictional planning data. BOTH nodes are currently healthy, "
                    "LOADED, authenticated for this owner, have current signed leases "
                    "and free slots. Output tokens: " + str(tokens) + ". "
                    f"Node A: TTFT {a[0]} ms, generation {a[1]} tokens/second, "
                    f"incremental draw {a[2]} watts. "
                    f"Node B: TTFT {b[0]} ms, generation {b[1]} tokens/second, "
                    f"incremental draw {b[2]} watts."
                )
                instructions = (
                    "Select the already-authorized runtime with the LOWER estimated "
                    + ("total completion latency. Include TTFT and tokens / token-rate."
                       if objective == "latency_ms"
                       else "incremental energy: watts times (TTFT plus tokens/token-rate).")
                )
                criteria = {
                    "node_a": "Route A with the Node A measurements shown in the state",
                    "node_b": "Route B with the Node B measurements shown in the state",
                }
            else:
                state = (
                    "Fictional planning data. BOTH nodes are currently healthy, "
                    "LOADED, authenticated for this owner, have current signed leases "
                    "and free slots. Independent deterministic estimator reports "
                    f"Node A {computed['node_a']:.2f} {objective}; "
                    f"Node B {computed['node_b']:.2f} {objective}."
                )
                instructions = (
                    "Choose the ALREADY-ELIGIBLE node with the smallest supplied "
                    f"{objective} estimate. Do not infer additional authority."
                )
                criteria = {
                    "node_a": f"Node A estimated {objective} {computed['node_a']:.2f}",
                    "node_b": f"Node B estimated {objective} {computed['node_b']:.2f}",
                }
            for order in ORDERS:
                opts = criteria if order == "forward" else dict(reversed(list(criteria.items())))
                row = {
                    "case_id": id_, "objective": objective, "tokens": tokens,
                    "template": template, "order": order, "gold": gold,
                    "eligible_options": ["node_a", "node_b"],
                    "metrics_a": list(a), "metrics_b": list(b),
                    "expected_a": round(computed["node_a"], 5),
                    "expected_b": round(computed["node_b"], 5),
                    "primitive_choice": primitive_heuristic(profile),
                    "facts_sha256": digest, "state": state,
                    "instructions": instructions, "criteria": opts,
                }
                row["question_sha256"] = question_digest(row)
                rows.append(row)
    return rows


def validate_design(rows: list[dict]) -> None:
    if len(rows) != MAX_CALLS or len({r["case_id"] for r in rows}) != 12:
        raise ValueError("expected twelve source profiles x four variants")
    winners = [oracle(p) for p in PROFILES]
    if CounterLiteral(winners, "node_a") != 6 or CounterLiteral(winners, "node_b") != 6:
        raise ValueError("imbalanced fictional optimum labels")
    for row in rows:
        same_case = [x for x in rows if x["case_id"] == row["case_id"]]
        if len(same_case) != 4:
            raise ValueError("missing matched source family")
        if any(x[field] != row[field] for x in same_case for field in (
            "facts_sha256", "gold", "eligible_options", "objective", "tokens",
            "metrics_a", "metrics_b", "expected_a", "expected_b",
        )):
            raise ValueError("immutable synthetic facts changed between variants")
        if {x["question_sha256"] for x in same_case}.__len__() != 4:
            raise ValueError("option-order/question template hash collision")
        ordered = [x for x in same_case if x["template"] == row["template"]]
        if len(ordered) != 2 or len({x["order"] for x in ordered}) != 2:
            raise ValueError("incomplete option-order pair")
        if ordered[0]["state"] != ordered[1]["state"] or (
                ordered[0]["instructions"] != ordered[1]["instructions"]):
            raise ValueError("option-order-only variant changed any input facts")
        if set(row["criteria"]) != {"node_a", "node_b"}:
            raise ValueError("model-facing Choice has unexpected options")
        if row["eligible_options"] != ["node_a", "node_b"]:
            raise ValueError("not a multi-eligible experiment")


def CounterLiteral(values: list[str], label: str) -> int:
    return sum(v == label for v in values)


def analyze(records: list[dict]) -> dict:
    if len(records) != MAX_CALLS:
        raise ValueError("incomplete inference cohort")
    out: dict = {"all_records": len(records), "unique_fictional_profiles": 12,
                 "authorized_candidates_per_case": 2, "by_template": {},
                 "order_pairs": 0, "order_changes": 0, "independent_labels": False}
    for template in TEMPLATES:
        subset = [r for r in records if r["template"] == template]
        if len(subset) != 24:
            raise ValueError("missing template cohort")
        latencies = [r["decision_latency_ms"] for r in subset]
        out["by_template"][template] = {
            "total": len(subset),
            "model_correct": sum(r["prediction"] == r["gold"] for r in subset),
            "primitive_correct": sum(r["primitive_choice"] == r["gold"] for r in subset),
            "exact_math_correct": len(subset),
            "model_total_objective_regret": round(sum(r["regret_model"] for r in subset), 4),
            "primitive_total_objective_regret": round(
                sum(r["regret_primitive"] for r in subset), 4),
            "model_latency_p50_ms": round(statistics.median(latencies), 3),
            "model_latency_p95_ms": round(
                sorted(latencies)[int((len(latencies) - 1) * .95)], 3),
            "incorrect_with_high_probability_over_08": sum(
                r["prediction"] != r["gold"] and r["confidence"] >= .8
                for r in subset),
        }
        for case in {r["case_id"] for r in subset}:
            pair = [r for r in subset if r["case_id"] == case]
            if len(pair) != 2:
                raise ValueError("missing matched option-order decisions")
            out["order_pairs"] += 1
            out["order_changes"] += int(pair[0]["prediction"] != pair[1]["prediction"])
    out["objective_units_mixed_do_not_sum_cross_templates"] = True
    return out


def run(work: Path, output: Path) -> dict:
    physical_preflight()
    require_resource_scope()
    design = build()
    validate_design(design)
    if output.exists() or not output.resolve(strict=False).is_relative_to(work):
        raise ValueError("output must be new and inside admitted /tmp workspace")
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
    records = []
    for row in design:
        start = time.perf_counter()
        response = engine.evaluate(
            state=row["state"], questions={"shadow_0": {
                "type": "choice", "instructions": row["instructions"],
                "criteria": row["criteria"],
            }}, model=MODEL_ID,
        )
        elapsed = round((time.perf_counter() - start) * 1000, 3)
        prediction, probability = decode_response(
            json.dumps(response.model_dump()).encode(), {},
            {"criteria": row["criteria"]}, MODEL_ID,
        )
        best = min(row["expected_a"], row["expected_b"])
        selected = row["expected_a"] if prediction == "node_a" else row["expected_b"]
        primitive = (row["expected_a"] if row["primitive_choice"] == "node_a"
                     else row["expected_b"])
        records.append({
            "case_id": row["case_id"], "objective": row["objective"],
            "template": row["template"], "order": row["order"],
            "facts_sha256": row["facts_sha256"],
            "question_sha256": row["question_sha256"],
            "eligible_options": row["eligible_options"], "gold": row["gold"],
            "primitive_choice": row["primitive_choice"], "prediction": prediction,
            "confidence": probability, "decision_latency_ms": elapsed,
            "regret_model": round(selected - best, 5),
            "regret_primitive": round(primitive - best, 5),
            "expected_a": row["expected_a"], "expected_b": row["expected_b"],
        })
    evidence = {
        "research_type": "fictional_two_eligible_objective_cpu_choice",
        "hf_revision": HF_REV, "weights_sha256": hashes,
        "cases": records, "summary": analyze(records),
        "synthetic_cases": True, "real_authority_evidence": False,
        "independent_labels": False, "signed_execution_attested": False,
        "safe_for_production": False, "new_router_policy": False,
        "cpu_model_calls": MAX_CALLS,
        "latency_and_energy_units_are_distinct": True,
    }
    payload = json.dumps(evidence, sort_keys=True, indent=2) + "\n"
    with os.fdopen(os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                          getattr(os, "O_NOFOLLOW", 0), 0o600), "w") as file:
        file.write(payload)
    return evidence


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--ack-cpu-impact", action="store_true")
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    try:
        work = admissible_workspace(args.workspace)
        design = build()
        validate_design(design)
        if not args.execute:
            print(json.dumps({
                "fictional_situations": len(PROFILES),
                "validated_variants": len(design), "actual_model_calls": 0,
                "capacity": physical_preflight(require_capacity=False),
                "model_download_attempted": False,
            }, sort_keys=True))
            return 0
        if not args.ack_cpu_impact or args.out is None:
            raise ValueError("execution requires acknowledgment and output destination")
        report = run(work, args.out)
        print(json.dumps({
            "cpu_model_calls": report["cpu_model_calls"],
            "summary": report["summary"], "safe_for_production": False,
        }, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, ImportError, RuntimeError) as exc:
        print("multi-eligible pilot refused/failed: " + type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
