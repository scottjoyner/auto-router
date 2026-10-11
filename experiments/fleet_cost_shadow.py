#!/usr/bin/env python3
"""Strictly fictional, read-only fleet cost ranking prototype.

This module DOES NOT authenticate AssistX, attest real evidence, dispatch,
reserve, authorize, load models, access the network, or modify a fleet node.
Its input schema explicitly forbids claiming live authority. It provides a
testable contract for a future, separately reviewed trusted evidence adapter.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from hashlib import sha256
from pathlib import Path

ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
BUNDLE_FIELDS = {
    "schema", "kind", "snapshot_id", "as_of_ms", "authority_observed_ms",
    "authority_expires_ms", "request", "runtimes", "telemetry",
}
REQUEST_FIELDS = {
    "request_id", "owner_id", "model_id", "task_class", "p90_output_tokens",
    "objective", "max_latency_ms", "min_quality", "required_offline_s",
}
RUNTIME_FIELDS = {
    "runtime_id", "node_id", "owner_id", "request_id", "model_id",
    "capabilities", "health", "loaded_state", "free_slots",
    "lease_not_before_ms", "lease_until_ms", "observed_at_ms",
    "process_id", "reservation_id",
}
TELEMETRY_FIELDS = {
    "runtime_id", "model_id", "task_class", "process_id",
    "sampled_at_ms", "sample_count", "ttft_p95_ms",
    "token_rate_p10", "watts_p95", "quality_lower_bound",
    "battery_remaining_wh", "dependency_watts", "battery_sampled_at_ms",
}
MAX_ELIGIBILITY_AGE_MS = 30_000
MAX_TELEMETRY_AGE_MS = 120_000
MAX_POWER_AGE_MS = 60_000
MIN_SAMPLES = 8


def _keys(obj: object, fields: set[str], label: str) -> dict:
    if not isinstance(obj, dict) or set(obj) != fields:
        raise ValueError(label + " schema mismatch or unexpected fields")
    return obj


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid " + label)
    return value


def _int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError("invalid " + label)
    return value


def _float(value: object, label: str, *, minimum: float = 0) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or value < minimum:
        raise ValueError("invalid " + label)
    return float(value)


def _time(value: object, now: int, age_ms: int, label: str) -> None:
    t = _int(value, label)
    if not 0 <= now - t <= age_ms:
        raise ValueError(label + " future-dated or stale")


def validate(bundle: object) -> dict:
    b = _keys(bundle, BUNDLE_FIELDS, "bundle")
    if b["schema"] != "fleet-cost-shadow-v1" or b["kind"] != "fictional-only":
        raise ValueError("real or untrusted authority bundles are not accepted")
    _id(b["snapshot_id"], "snapshot id")
    now = _int(b["as_of_ms"], "as_of_ms", minimum=1)
    _time(b["authority_observed_ms"], now, MAX_ELIGIBILITY_AGE_MS, "authority timestamp")
    expiration = _int(b["authority_expires_ms"], "authority_expires_ms")
    if not now < expiration <= now + MAX_ELIGIBILITY_AGE_MS:
        raise ValueError("authority snapshot expired or excessively future-valid")
    r = _keys(b["request"], REQUEST_FIELDS, "request")
    for field in ("request_id", "owner_id", "model_id", "task_class"):
        _id(r[field], field)
    _int(r["p90_output_tokens"], "tokens", minimum=1)
    _int(r["max_latency_ms"], "max_latency_ms", minimum=1)
    _int(r["required_offline_s"], "required_offline_s")
    q = _float(r["min_quality"], "min_quality")
    if q > 1:
        raise ValueError("min_quality outside [0,1]")
    if r["objective"] not in ("latency_ms", "energy_j"):
        raise ValueError("unsupported numeric objective")
    if not isinstance(b["runtimes"], list) or not isinstance(b["telemetry"], list):
        raise ValueError("runtimes/telemetry must be lists")
    if len(b["runtimes"]) > 64 or len(b["telemetry"]) > 64:
        raise ValueError("bounded fleet-size budget exceeded")
    runtime_ids, process_ids, reservation_ids, telemetry_ids = set(), set(), set(), set()
    for entry in b["runtimes"]:
        a = _keys(entry, RUNTIME_FIELDS, "runtime evidence")
        for field in ("runtime_id", "node_id", "owner_id", "request_id",
                      "model_id", "process_id", "reservation_id"):
            _id(a[field], field)
        if a["runtime_id"] in runtime_ids:
            raise ValueError("duplicated or contradictory runtime authority")
        runtime_ids.add(a["runtime_id"])
        physical_process = (a["node_id"], a["process_id"])
        if physical_process in process_ids or a["reservation_id"] in reservation_ids:
            raise ValueError("duplicated physical process or reservation identity")
        process_ids.add(physical_process)
        reservation_ids.add(a["reservation_id"])
        if not isinstance(a["capabilities"], list) or not a["capabilities"] or (
                len(a["capabilities"]) != len(set(a["capabilities"]))):
            raise ValueError("invalid capability set")
        for cap in a["capabilities"]:
            _id(cap, "capability")
        if a["health"] not in ("HEALTHY", "OFFLINE", "UNKNOWN"):
            raise ValueError("unknown health")
        if a["loaded_state"] not in ("LOADED", "AVAILABLE", "UNKNOWN"):
            raise ValueError("unknown residence state")
        _int(a["free_slots"], "free_slots")
        _int(a["lease_not_before_ms"], "lease starts")
        _int(a["lease_until_ms"], "lease ends")
        _time(a["observed_at_ms"], now, MAX_ELIGIBILITY_AGE_MS, "runtime observation")
        if a["lease_not_before_ms"] >= a["lease_until_ms"]:
            raise ValueError("invalid lease interval")
    for entry in b["telemetry"]:
        t = _keys(entry, TELEMETRY_FIELDS, "telemetry evidence")
        for field in ("runtime_id", "model_id", "task_class", "process_id"):
            _id(t[field], "telemetry " + field)
        if t["runtime_id"] in telemetry_ids:
            raise ValueError("duplicated or contradictory telemetry")
        telemetry_ids.add(t["runtime_id"])
        if t["runtime_id"] not in runtime_ids:
            raise ValueError("telemetry without matching runtime")
        _int(t["sampled_at_ms"], "sampled_at_ms")
        _int(t["sample_count"], "sample_count")
        _float(t["ttft_p95_ms"], "ttft", minimum=0)
        _float(t["token_rate_p10"], "token throughput", minimum=0)
        _float(t["watts_p95"], "watts", minimum=0)
        quality = _float(t["quality_lower_bound"], "quality")
        if quality > 1:
            raise ValueError("quality outside [0,1]")
        if t["battery_remaining_wh"] is None or t["dependency_watts"] is None:
            if (t["battery_remaining_wh"] is not None or
                    t["dependency_watts"] is not None or
                    t["battery_sampled_at_ms"] is not None):
                raise ValueError("incomplete offline-survival evidence")
        else:
            _float(t["battery_remaining_wh"], "battery_remaining_wh", minimum=0)
            _float(t["dependency_watts"], "dependency_watts", minimum=0)
            if t["battery_sampled_at_ms"] is None:
                raise ValueError("missing battery timestamp")
            _int(t["battery_sampled_at_ms"], "battery_sampled_at_ms")
    return b


def eligible(entry: dict, request: dict, now: int) -> bool:
    return (
        entry["owner_id"] == request["owner_id"]
        and entry["request_id"] == request["request_id"]
        and entry["model_id"] == request["model_id"]
        and request["task_class"] in entry["capabilities"]
        and entry["health"] == "HEALTHY"
        and entry["loaded_state"] == "LOADED"
        and entry["free_slots"] > 0
        and entry["lease_not_before_ms"] <= now < entry["lease_until_ms"]
    )


def rank(bundle: object) -> dict:
    b = validate(bundle)
    r, now = b["request"], b["as_of_ms"]
    admitted = sorted(
        [a for a in b["runtimes"] if eligible(a, r, now)],
        key=lambda a: a["runtime_id"],
    )
    output = {
        "schema": "fleet-cost-shadow-result-v1",
        "snapshot_id": b["snapshot_id"],
        "snapshot_digest": sha256(json.dumps(
            b, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "input_kind": "fictional-only",
        "objective": r["objective"], "status": "NO_ELIGIBLE_RUNTIME",
        "eligible_count": len(admitted), "ranked_synthetic_ids": [],
        "rank_costs": [], "evidence_blockers": [],
        "authority_to_dispatch": False, "physical_execution_attested": False,
        "real_fleet_claims": False, "production_promotion_approved": False,
    }
    if not admitted:
        return output
    telemetry = {t["runtime_id"]: t for t in b["telemetry"]}
    scored, blockers = [], []
    for a in admitted:
        runtime_id = a["runtime_id"]
        t = telemetry.get(runtime_id)
        if t is None:
            blockers.append([runtime_id, "telemetry_missing"])
            continue
        if (t["model_id"] != a["model_id"] or
                t["task_class"] != r["task_class"] or
                t["process_id"] != a["process_id"]):
            blockers.append([runtime_id, "telemetry_model_process_mismatch"])
            continue
        if t["sample_count"] < MIN_SAMPLES:
            blockers.append([runtime_id, "insufficient_samples"])
            continue
        try:
            _time(t["sampled_at_ms"], now, MAX_TELEMETRY_AGE_MS, "telemetry")
        except ValueError:
            blockers.append([runtime_id, "telemetry_not_fresh"])
            continue
        if t["token_rate_p10"] <= 0 or t["watts_p95"] <= 0:
            blockers.append([runtime_id, "invalid_performance_bounds"])
            continue
        if t["quality_lower_bound"] < r["min_quality"]:
            blockers.append([runtime_id, "quality_floor_unproven"])
            continue
        latency_ms = t["ttft_p95_ms"] + (
            1000 * r["p90_output_tokens"] / t["token_rate_p10"]
        )
        if latency_ms > r["max_latency_ms"]:
            blockers.append([runtime_id, "deadline_bound_exceeded"])
            continue
        if r["required_offline_s"]:
            if t["battery_remaining_wh"] is None:
                blockers.append([runtime_id, "offline_survival_unproven"])
                continue
            try:
                _time(t["battery_sampled_at_ms"], now,
                      MAX_POWER_AGE_MS, "battery sample")
            except ValueError:
                blockers.append([runtime_id, "power_evidence_not_fresh"])
                continue
            if t["dependency_watts"] <= 0:
                blockers.append([runtime_id, "power_denominator_unproven"])
                continue
            # Conservative 70% usable battery allocation, not a measured runtime.
            survivable_s = (0.7 * 3600 * t["battery_remaining_wh"]
                            / t["dependency_watts"])
            if survivable_s < r["required_offline_s"]:
                blockers.append([runtime_id, "offline_survival_bound_exceeded"])
                continue
        energy_j = latency_ms * t["watts_p95"] / 1000
        cost = latency_ms if r["objective"] == "latency_ms" else energy_j
        scored.append((cost, runtime_id, {
            "runtime_id": runtime_id,
            "node_id": a["node_id"],
            "latency_conservative_proxy_ms": round(latency_ms, 3),
            "energy_conservative_proxy_j": round(energy_j, 3),
            "quality_lower_bound": t["quality_lower_bound"],
            "sample_count": t["sample_count"],
            "rank_objective_cost": round(cost, 3),
        }))
    if blockers:
        # A tempting partial ranking from remaining good candidates could
        # conceal better eligible candidates with missing evidence.
        output["status"] = "EVIDENCE_INCOMPLETE"
        output["evidence_blockers"] = sorted(blockers)
        return output
    # Sort unrounded costs; rounding is display-only and cannot reorder a close race.
    scored.sort(key=lambda item: (item[0], item[1]))
    output["status"] = "RANKED_SYNTHETIC_ONLY"
    output["ranked_synthetic_ids"] = [runtime for _, runtime, _ in scored]
    output["rank_costs"] = [public for _, _, public in scored]
    return output


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("fixture", type=Path)
    args = p.parse_args()
    try:
        if not args.fixture.is_file() or args.fixture.stat().st_size > 96_000:
            raise ValueError("fixture must be a small regular file")
        b = json.loads(args.fixture.read_text(encoding="utf-8"))
        print(json.dumps(rank(b), sort_keys=True, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as err:
        print("cost-shadow rejected input: " + type(err).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
