#!/usr/bin/env python3
"""Opt-in local Von Choice observation adapter. No imports from router or von-sdk.

DRY RUN DEFAULT: validates a human-sanitized fixture without any HTTP request.
--execute can cause a *preexisting* Von server to load model weights: operator gate.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from typed_decision_shadow import ID, VARIANTS, check

CASE_FIELDS = frozenset({"case_id", "variant", "task_type", "state", "instructions",
                         "criteria", "gold", "evidence_complete"})
SENSITIVE = re.compile(
    r"(?:\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    r"|\b(?:\d{1,3}\.){3}\d{1,3}\b"
    r"|\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{8,})\b"
    r"|(?:/nas/|/home/scott/|bearer\s+|password\s*[:=]|api[_-]?key\s*[:=]))",
    re.IGNORECASE,
)
MAX_CASES = 500


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("sanitized state/criteria/instruction text length invalid")
    if any(ord(c) < 32 and c not in "\n\t" for c in value) or SENSITIVE.search(value):
        raise ValueError("input contains control chars or sensitive-pattern indicator")
    return value


def validate_case(row: object) -> dict:
    if not isinstance(row, dict) or set(row) != CASE_FIELDS:
        raise ValueError("fixture must use only approved case fields")
    if not isinstance(row["case_id"], str) or not ID.fullmatch(row["case_id"]):
        raise ValueError("invalid opaque case id")
    if row["variant"] not in VARIANTS or row["task_type"] != "choice":
        raise ValueError("adapter only supports Choice tasks and declared variants")
    if type(row["evidence_complete"]) is not bool:
        raise ValueError("evidence_complete must be boolean")
    if row["variant"] == "missing_evidence" and row["evidence_complete"]:
        raise ValueError("missing evidence must be marked incomplete")
    _text(row["state"], 2048)
    _text(row["instructions"], 320)
    criteria = row["criteria"]
    if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 16:
        raise ValueError("require 2-16 options")
    for key, description in criteria.items():
        if not isinstance(key, str) or not ID.fullmatch(key):
            raise ValueError("invalid opaque option key")
        _text(description, 320)
    gold = row["gold"]
    if gold is not None and (not isinstance(gold, str) or not ID.fullmatch(gold)):
        raise ValueError("invalid gold label")
    if row["evidence_complete"] and gold not in criteria:
        raise ValueError("complete cases require independently labeled eligible gold")
    return row


def fixture(path: Path) -> tuple[list[dict], str]:
    if not path.is_file() or path.stat().st_size > 4_000_000:
        raise ValueError("fixture missing or over 4 MB")
    rows, keys, originals = [], set(), {}
    h = sha256()
    with path.open("rb") as file:
        for i, raw in enumerate(file, 1):
            if len(raw) > 8192:
                raise ValueError("case exceeds 8 KiB")
            h.update(raw)
            if not raw.strip():
                continue
            if i > MAX_CASES:
                raise ValueError("case limit exceeded")
            try:
                row = validate_case(json.loads(raw))
                key = (row["case_id"], row["variant"])
                if key in keys:
                    raise ValueError("duplicate case/variant")
                keys.add(key)
                if row["variant"] == "original":
                    originals[row["case_id"]] = row
                rows.append(row)
            except (ValueError, TypeError, json.JSONDecodeError) as err:
                raise ValueError(f"case line {i}: {err}") from err
    if not rows:
        raise ValueError("empty fixture")
    for row in rows:
        if row["variant"] == "permuted":
            original = originals.get(row["case_id"])
            if (original is None or original["state"] != row["state"] or
                    original["instructions"] != row["instructions"] or
                    original["criteria"] != row["criteria"] or
                    original["gold"] != row["gold"] or
                    original["evidence_complete"] != row["evidence_complete"]):
                raise ValueError("permutation must preserve state/criteria/labels")
            if list(original["criteria"]) == list(row["criteria"]):
                raise ValueError("permutation must actually reorder criteria")
    return rows, h.hexdigest()


def loopback_endpoint(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or
                parsed.username is not None or parsed.password is not None or
                parsed.path not in ("", "/") or parsed.query or parsed.fragment or
                parsed.port is None or not (1 <= parsed.port <= 65535)):
            raise ValueError("only http://127.0.0.1:PORT is allowed")
    except ValueError as err:
        raise ValueError("only an explicit numeric 127.0.0.1 endpoint is allowed") from err
    return f"http://127.0.0.1:{parsed.port}/v1/systemone"


def observation(case: dict, *, model_id: str, revision: str, status: str,
                choice: str | None, confidence: float | None, duration: float | None) -> dict:
    row = {
        "case_id": case["case_id"], "model_id": model_id, "model_revision": revision,
        "task_type": case["task_type"], "variant": case["variant"],
        "state_sha256": sha256(case["state"].encode()).hexdigest(),
        "gold": case["gold"], "prediction": choice, "confidence": confidence,
        "eligible_options": sorted(case["criteria"]),
        "evidence_complete": case["evidence_complete"], "latency_ms": duration,
        "status": status, "source_kind": "model_observation",
    }
    return check(row)


def decode_response(body: bytes, headers: object, case: dict, expected_model: str):
    if len(body) > 128_000:
        raise ValueError("Von response exceeds 128 KiB")
    data = json.loads(body)
    if not isinstance(data, dict) or data.get("model") != expected_model:
        raise ValueError("Von response model mismatch")
    if data.get("truncation") or headers.get("X-Von-Truncated"):
        raise ValueError("Von truncated the state; no valid comparison")
    answers = data.get("answers")
    if not isinstance(answers, dict) or set(answers) != {"shadow_0"}:
        raise ValueError("missing/extra Von answer")
    answer = answers["shadow_0"]
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("expected Choice answer")
    prediction = answer.get("choice")
    probs = answer.get("probabilities")
    if (not isinstance(prediction, str) or not ID.fullmatch(prediction) or
            not isinstance(probs, dict) or set(probs) != set(case["criteria"])):
        raise ValueError("Von answer violates option contract")
    if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1
           for x in probs.values()) or abs(sum(probs.values()) - 1) > 0.01:
        raise ValueError("invalid probability distribution")
    if prediction not in probs:
        raise ValueError("prediction absent from probability map")
    return prediction, float(probs[prediction])


def request_von(case: dict, endpoint: str, *, timeout: float, expected_model: str):
    # No environment proxy variables, no redirect handler, no remote hostname/DNS lookup.
    from urllib.request import HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = build_opener(ProxyHandler({}), NoRedirect)
    payload = json.dumps({
        "model": expected_model,
        "state": case["state"],
        "questions": {"shadow_0": {
            "type": "choice", "instructions": case["instructions"],
            "criteria": case["criteria"],
        }},
    }).encode()
    req = Request(endpoint, data=payload, headers={"Content-Type": "application/json"},
                  method="POST")
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as response:
            if response.status != 200:
                return "error", None, None, None
            raw = response.read(128_001)
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            decision, p = decode_response(raw, response.headers, case, expected_model)
            return "ok", decision, p, elapsed
    except (HTTPError, URLError, OSError, ValueError, TimeoutError, UnicodeError):
        # Never print exception text: model server errors could contain a raw prompt.
        return "error", None, None, None


def output_path(path: Path) -> Path:
    actual = path.resolve()
    if not actual.is_relative_to(Path("/tmp")) or actual == Path("/tmp"):
        raise ValueError("output must be a new file under /tmp, never on NAS")
    return actual


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cases", type=Path)
    p.add_argument("--execute", action="store_true", help="send to an already running local Von server")
    p.add_argument("--ack-sanitized", action="store_true")
    p.add_argument("--ack-preloaded", action="store_true",
                   help="acknowledge possible lazy model/weight loading by the Von server")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--model-id", default="von")
    p.add_argument("--model-revision", default="von-1.3.0")
    p.add_argument("--out", type=Path)
    p.add_argument("--timeout", type=float, default=30.0)
    args = p.parse_args()
    try:
        rows, digest = fixture(args.cases)
        endpoint = loopback_endpoint(args.url)
        if not ID.fullmatch(args.model_id) or not ID.fullmatch(args.model_revision):
            raise ValueError("invalid model id/revision")
        if not math.isfinite(args.timeout) or not 0.5 <= args.timeout <= 60:
            raise ValueError("timeout must be 0.5-60s")
        if not args.execute:
            print(json.dumps({"validated_cases": len(rows), "fixture_sha256": digest,
                              "network_requests": 0, "model_inference": False}))
            return 0
        if not (args.ack_sanitized and args.ack_preloaded and args.out):
            raise ValueError("execution requires --ack-sanitized --ack-preloaded and --out")
        destination = output_path(args.out)
        # Atomic exclusive create, never append/overwrite existing evidence.
        with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                              getattr(os, "O_NOFOLLOW", 0), 0o600), "w") as out:
            for case in rows:
                status, choice, confidence, duration = request_von(
                    case, endpoint, timeout=args.timeout, expected_model="von-1.3.0")
                item = observation(case, model_id=args.model_id, revision=args.model_revision,
                                   status=status, choice=choice, confidence=confidence,
                                   duration=duration)
                out.write(json.dumps(item, sort_keys=True) + "\n")
        print(json.dumps({"observations": len(rows), "out": str(destination),
                          "fixture_sha256": digest,
                          "execution_attested": False, "production_promotable": False}))
        return 0
    except (ValueError, OSError) as error:
        # Validation errors contain only fixed messages, never the raw state.
        print("Von shadow adapter refused input/output: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
