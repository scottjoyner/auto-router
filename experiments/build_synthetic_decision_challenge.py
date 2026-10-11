#!/usr/bin/env python3
"""Generate a reproducible, fully fictional routing-choice challenge and policy oracle.

No real telemetry, models, sockets, routers, or NAS access. Labels are computed
from a separate policy table; this is NOT a genuine held-out operational corpus.
"""
from __future__ import annotations

import argparse
import json
import os
from hashlib import sha256
from pathlib import Path

from von_choice_shadow import fixture, observation, validate_case

FAMILIES = (
    ("offline", "responds to authenticated health", "declared OFFLINE by current heartbeat"),
    ("lease", "current signed reservation and unexpired lease",
     "reservation lease expired before this request"),
    ("loaded", "model LOADED with a current process witness",
     "model only AVAILABLE in catalog, not loaded"),
    ("capacity", "one runtime slot free after authenticated reservation check",
     "zero free slots after active reservations"),
    ("owner", "request principal matches trusted server-minted owner",
     "request ownership only comes from untrusted client metadata"),
    ("freshness", "live health received within thirty seconds",
     "last positive health sample is stale by six hundred seconds"),
    ("auth", "provider authorized by current effective in-memory policy",
     "provider listed in mounted YAML but absent from effective policy"),
    ("contradiction", "earlier heartbeat OFFLINE, later signed update ONLINE",
     "earlier heartbeat ONLINE, later signed update OFFLINE"),
    ("missing", "no authenticated evidence for model/process/lease ownership",
     "no authenticated evidence for model/process/lease ownership"),
)
CASES_PER_FAMILY = 16
TOTAL_ORIGINAL = len(FAMILIES) * CASES_PER_FAMILY


def build() -> tuple[list[dict], list[dict]]:
    cases, baselines = [], []
    for family, good, bad in FAMILIES:
        for iteration in range(CASES_PER_FAMILY):
            id_ = f"synthetic_{family}_{iteration:02d}"
            good_label = "node_a" if iteration % 2 == 0 else "node_b"
            missing = family == "missing"
            left = good if good_label == "node_a" else bad
            right = good if good_label == "node_b" else bad
            state = (
                f"Fictional fleet policy exercise {id_}. "
                f"Task type {family}. "
                f"Node A current source observations: {left}. "
                f"Node B current source observations: {right}. "
                "Assume no other unstated positive evidence. "
                "A mere catalog entry, earlier observation, or request-provided "
                "claim does not confer current admission."
            )
            criteria = {"node_a": "Select runtime A", "node_b": "Select runtime B"}
            gold = None if missing else good_label
            allowed = [] if missing else [good_label]
            row = validate_case({
                "case_id": id_, "variant": "original",
                "task_type": "choice", "state": state,
                "instructions": (
                    "Which candidate is currently authorized, loaded, "
                    "healthy and available for admission?"
                ),
                "criteria": criteria, "gold": gold, "eligible_options": allowed,
                "evidence_complete": not missing,
            })
            cases.append(row)
            if iteration % 2 == 0:
                permuted = dict(row)
                permuted["variant"] = "permuted"
                permuted["criteria"] = dict(reversed(list(criteria.items())))
                cases.append(validate_case(permuted))
    for case in cases:
        is_actionable = case["evidence_complete"]
        oracle = observation(
            case, model_id="policy_oracle", revision="synthetic-v1", status="ok",
            choice=case["gold"] if is_actionable else None,
            confidence=None, duration=None,
        )
        oracle["source_kind"] = "synthetic"
        baselines.append(oracle)
    return cases, baselines


def validate_generated(cases: list[dict], baselines: list[dict]) -> None:
    assert len(cases) == 216
    assert len(baselines) == len(cases)
    assert sum(c["variant"] == "original" for c in cases) == TOTAL_ORIGINAL
    assert sum(c["variant"] == "permuted" for c in cases) == 72
    assert sum(not c["evidence_complete"] for c in cases) == 24
    assert len({(c["case_id"], c["variant"]) for c in cases}) == len(cases)
    for case, baseline in zip(cases, baselines, strict=True):
        assert baseline["case_id"] == case["case_id"]
        assert baseline["eligible_options"] == sorted(case["eligible_options"])
        assert baseline["prediction"] == case["gold"]
        assert baseline["source_kind"] == "synthetic"


def write_new(path: Path, rows: list[dict]) -> str:
    dest = path.resolve(strict=False)
    if dest == Path("/tmp") or not dest.is_relative_to(Path("/tmp")):
        raise ValueError("outputs must be new files under /tmp")
    content = "".join(json.dumps(row, separators=(",", ":"), sort_keys=False) + "\n"
                      for row in rows).encode("utf-8")
    with os.fdopen(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                          getattr(os, "O_NOFOLLOW", 0), 0o600), "wb") as out:
        out.write(content)
    return sha256(content).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases-out", type=Path, required=True)
    p.add_argument("--oracle-out", type=Path, required=True)
    args = p.parse_args()
    cases, baselines = build()
    validate_generated(cases, baselines)
    if args.cases_out.resolve() == args.oracle_out.resolve():
        p.error("case and oracle paths must differ")
    if args.cases_out.exists() or args.oracle_out.exists():
        p.error("refusing to overwrite an existing output path")
    try:
        c_sha = write_new(args.cases_out, cases)
        b_sha = write_new(args.oracle_out, baselines)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    # Check case contract using the independent adapter/parser, not generator only.
    verified, digest = fixture(args.cases_out)
    assert len(verified) == len(cases) and digest == c_sha
    print(json.dumps({
        "synthetic_original_cases": TOTAL_ORIGINAL,
        "original_actionable": 128, "original_missing_evidence": 16,
        "original_plus_permutations": len(cases),
        "cases_sha256": c_sha, "oracle_sha256": b_sha,
        "real_model_inference": False, "real_fleet_data": False,
        "independent_operational_label_review": False,
        "safe_for_promotion": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
