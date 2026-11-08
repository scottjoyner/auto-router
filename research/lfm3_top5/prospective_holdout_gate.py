"""Fail-closed preregistered held-out scorer, with ZERO release authority.

Only independently held-out fictional or sanitized cases are admissible for a
future comparative study. This pure scorer cannot attest authorship, external
custody, real model execution, kernel dispatch, or production eligibility.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from .prospective_rule_v1 import CODES, classify
from .unstructured_triage_audit import exact_answer, _pairs_no_duplicate, _invalid_constant

SCHEMA = "lfm3.prospective.prereg.v1"
DATA_SCHEMA = "lfm3.prospective.cases.v1"
MODEL_SHA = "43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"
CHALLENGES = ("negation", "chronology", "ambiguity", "distractor",
              "missing_evidence", "overlap")
MODES = ("cpu", "vulkan8")
MIN_CASES = 36
MIN_PER_CATEGORY = 5
MIN_PER_CHALLENGE = 5
EXACT_FLOOR = 0.85
MIN_ABSOLUTE_GAIN = 0.15

def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)

def sha(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

def hold(reason: str) -> dict:
    return {"decision": "HOLD", "reason": reason, "model_value_verified": False,
            "external_custody_verified": False, "accelerator_attested": False,
            "production_authorized": False}

def _exact_manifest(m: object) -> bool:
    if type(m) is not dict or set(m) != {
        "schema", "checkpoint_sha256", "baseline_sha256", "scorer_sha256",
        "min_cases", "min_per_category", "min_per_challenge",
        "exact_floor", "min_absolute_gain", "production_authorized"
    }:
        return False
    expected = {"schema": SCHEMA, "checkpoint_sha256": MODEL_SHA,
                "min_cases": MIN_CASES, "min_per_category": MIN_PER_CATEGORY,
                "min_per_challenge": MIN_PER_CHALLENGE, "exact_floor": EXACT_FLOOR,
                "min_absolute_gain": MIN_ABSOLUTE_GAIN, "production_authorized": False}
    if any(type(m.get(k)) is not type(v) or m[k] != v for k,v in expected.items()):
        return False
    here = Path(__file__).resolve().parent
    hashes = {"baseline_sha256": here / "prospective_rule_v1.py",
              "scorer_sha256": here / "prospective_holdout_gate.py"}
    return all(type(m[k]) is str and
               hashlib.sha256(p.read_bytes()).hexdigest() == m[k]
               for k,p in hashes.items())

def _cases_valid(data: object) -> bool:
    if type(data) is not dict or set(data) != {"schema", "cases"} or data["schema"] != DATA_SCHEMA:
        return False
    rows = data["cases"]
    if type(rows) is not list or not MIN_CASES <= len(rows) <= 250:
        return False
    ids, notes = set(), set()
    categories, challenges = Counter(), Counter()
    for row in rows:
        if type(row) is not dict or set(row) != {"id", "note", "expected", "challenges"}:
            return False
        cid, note, expected, tags = (row[k] for k in ("id","note","expected","challenges"))
        if type(cid) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_-]{2,39}", cid) or cid in ids:
            return False
        if type(note) is not str or not 80 <= len(note) <= 4000 or note in notes:
            return False
        if type(expected) is not dict or set(expected) != {"category","evidence"}:
            return False
        if type(expected["category"]) is not str or expected["category"] not in CODES:
            return False
        ev = expected["evidence"]
        if ev is not None and (type(ev) is not str or not re.fullmatch(r"E[1-9]", ev)):
            return False
        if expected["category"] != "insufficient_evidence" and ev is None:
            return False
        if type(tags) is not list or not tags or len(tags) != len(set(map(str,tags))):
            return False
        if any(type(t) is not str or t not in CHALLENGES for t in tags):
            return False
        ids.add(cid); notes.add(note)
        categories[expected["category"]] += 1
        challenges.update(tags)
    return all(categories[c] >= MIN_PER_CATEGORY for c in CODES) and all(
        challenges[t] >= MIN_PER_CHALLENGE for t in CHALLENGES)

def _answer_rows_valid(rows: object, ids: set[str], seen: set[str]) -> bool:
    if type(rows) is not list or len(rows) != len(ids):
        return False
    got, sessions = set(), set()
    for row in rows:
        if type(row) is not dict or set(row) != {
            "id", "response", "request_id", "session_id", "http_plus_validation_ms"
        }:
            return False
        cid, rid, sid = row["id"], row["request_id"], row["session_id"]
        if cid not in ids or cid in got or type(cid) is not str:
            return False
        if type(rid) is not str or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", rid) or rid in seen:
            return False
        if type(sid) is not str or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", sid):
            return False
        if type(row["response"]) is not str or not 1 <= len(row["response"]) <= 2000:
            return False
        t = row["http_plus_validation_ms"]
        if type(t) not in (int,float) or not math.isfinite(t) or not 0 < t <= 600000:
            return False
        got.add(cid); seen.add(rid); sessions.add(sid)
    return got == ids and len(sessions) >= 2

def evaluate(manifest: object, dataset: object, sealed_dataset_sha256: object,
             predictions: object) -> dict:
    """Scores a supplied corpus only; an external seal MUST predate all scoring."""
    try:
        if not _exact_manifest(manifest):
            return hold("unfrozen_or_drifted_preregistration")
        if not _cases_valid(dataset):
            return hold("invalid_or_unrepresentative_case_contract")
        if type(sealed_dataset_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", sealed_dataset_sha256):
            return hold("missing_external_dataset_commitment")
        if sha(dataset) != sealed_dataset_sha256:
            return hold("heldout_case_digest_mismatch")
        if type(predictions) is not dict or set(predictions) != set(MODES):
            return hold("missing_mode_or_extra_mode")
        ids = {c["id"] for c in dataset["cases"]}
        seen = set()
        for mode in MODES:
            if not _answer_rows_valid(predictions[mode], ids, seen):
                return hold("missing_replayed_or_malformed_inference_rows_" + mode)
        truth = {r["id"]: r for r in dataset["cases"]}
        baseline = {cid: classify(item["note"]) for cid,item in truth.items()}
        baseline_correct = sum(baseline[cid] == item["expected"] for cid,item in truth.items())
        metrics = {}
        for mode in MODES:
            answers = {r["id"]: r for r in predictions[mode]}
            correct, unsupported, format_invalid, abstentions, abstention_total = 0, 0, 0, 0, 0
            for cid, item in truth.items():
                row = answers[cid]
                expected = item["expected"]
                exact = exact_answer(row["response"], expected)
                correct += int(exact)
                if not exact:
                    try:
                        obj = json.loads(row["response"], object_pairs_hook=_pairs_no_duplicate,
                                         parse_constant=_invalid_constant)
                    except (ValueError, RecursionError):
                        obj = None
                    if type(obj) is not dict or set(obj) != {"category", "evidence"}:
                        format_invalid += 1
                    elif obj["category"] != "insufficient_evidence" and obj != expected:
                        unsupported += 1  # unsupported positive attribution or evidence
                if expected["category"] == "insufficient_evidence":
                    abstention_total += 1
                    abstentions += int(exact)
            rate = correct / len(ids)
            gain = (correct - baseline_correct) / len(ids)
            metrics[mode] = {
                "unique_case_count": len(ids), "exact_correct": correct,
                "baseline_exact_correct": baseline_correct,
                "exact_rate": round(rate, 6),
                "absolute_gain_vs_frozen_rule": round(gain, 6),
                "unsupported_or_inexact": len(ids) - correct,
                "unsupported_attributions": unsupported,
                "invalid_format": format_invalid,
                "correct_insufficient_evidence": abstentions,
                "insufficient_evidence_cases": abstention_total,
                "meets_preregistered_quality_floor": (
                    rate >= EXACT_FLOOR and gain >= MIN_ABSOLUTE_GAIN and
                    unsupported == 0 and abstentions == abstention_total
                ),
            }
        return {
            "decision": "PASS_OFFLINE_SCORING_CONTRACT_ONLY",
            "case_count": len(ids),
            "primary_independent_case_count": len(ids),  # NOT twice this for two modes
            "model_modes": metrics, "baseline_frozen": True,
            "model_value_verified": False, "external_custody_verified": False,
            "accelerator_attested": False, "production_authorized": False,
            "reason": "External preregistration/custody and inference receipts require independent review",
        }
    except (TypeError, ValueError, KeyError, OSError, OverflowError, RecursionError):
        return hold("untrusted_input_or_unavailable_source")
