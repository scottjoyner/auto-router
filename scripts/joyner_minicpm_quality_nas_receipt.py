#!/usr/bin/env python3
"""Host-locked, six-request synthetic dual-path test with NAS-first trace custody.

Never captures tokens, full prompts, response text or private client headers.
No model loading, routing change, proxy mutation, or service restart.
Every request gets a durable pending record before being sent and a result
record after completion. A failed receipt write stops the test before more I/O.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import joyner_minicpm_guarded_canary as original

NAS = Path("/nas/fileserver/fleet-reports/trace-custody")
ALIAS = "joyner-toolcall"
ROLE_URLS = {
    "legacy": "http://127.0.0.1:1234/v1/chat/completions",
    "guarded": "http://100.83.215.83:18320/v1/chat/completions",
}
CASES = (
    ("arithmetic", "What is 17 plus 25? Give only the decimal answer.", 96),
    ("json", "Reply with a JSON object: one key named ready, Boolean value true.", 96),
    ("literal", "Reply with the single uppercase word READY, nothing else.", 48),
)
FOLLOWUP_CASES = (
    ("arithmetic", "What is 17 plus 25? Give only the decimal answer.", 256),
    ("literal", "Reply with the single uppercase word READY, nothing else.", 256),
)


class NeverRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def is_verified_nas() -> bool:
    if socket.gethostname().split(".")[0] != "joyner":
        return False
    proc = subprocess.run(["findmnt", "-rn", "-t", "cifs", "-M", "/nas"],
                          capture_output=True, text=True, timeout=5, check=False)
    return proc.returncode == 0 and "cifs" in proc.stdout and NAS.is_dir()


def persist(path: Path, data: dict) -> str:
    """Atomically write/read-verify bounded JSON to a single fixed NAS directory."""
    if path.parent != NAS or not path.name.startswith("joyner-minicpm-dualpath-"):
        raise ValueError("receipt path outside fixed NAS custody directory")
    raw = (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
    if len(raw) > 110_000:
        raise ValueError("receipt exceeds bounded maximum")
    temp = NAS / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        if path.read_bytes() != raw:
            raise OSError("NAS read-after-write mismatch")
        return hashlib.sha256(raw).hexdigest()
    finally:
        if temp.exists():
            temp.unlink()


def quality(case: str, content: str) -> bool:
    cleaned = content.strip()
    if case == "arithmetic":
        return bool(re.fullmatch(r"42[.]?", cleaned))
    if case == "json":
        try:
            return json.loads(cleaned) == {"ready": True}
        except (json.JSONDecodeError, ValueError):
            return False
    if case == "literal":
        return cleaned == "READY"
    return False


def request(role: str, body: dict, token: str | None, case: str) -> dict:
    endpoint = ROLE_URLS[role]  # closed source-defined set
    opener = build_opener(ProxyHandler({}), NeverRedirect())
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "joyner-local-quality-custody/1"}
    if role == "guarded":
        if not token:
            raise ValueError("guarded token absent")
        headers["Authorization"] = "Bearer " + token
    started = time.perf_counter()
    try:
        req = Request(endpoint, data=json.dumps(body).encode(),
                      headers=headers, method="POST")
        with opener.open(req, timeout=15) as response:
            payload = json.loads(response.read(120_000))
            code = response.status
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        content = (message.get("content") if isinstance(message, dict) else None)
        content = content if isinstance(content, str) else ""
        usage = payload.get("usage") if isinstance(payload, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        return {
            "http_code": code,
            "latency_ms": round((time.perf_counter()-started)*1000, 2),
            "response_model_id": payload.get("model"),
            "generated_text_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "generated_text_length": len(content),
            "generated_text_saved": False,
            "quality_passed": quality(case, content),
            "finish_reason": choice.get("finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "error_class": None,
        }
    except HTTPError as e:
        status = e.code
        e.close()
        error = "HTTPError"
    except (OSError, URLError, ValueError, json.JSONDecodeError, IndexError, KeyError) as e:
        status = None
        error = type(e).__name__
    return {
        "http_code": status,
        "latency_ms": round((time.perf_counter()-started)*1000, 2),
        "response_model_id": None,
        "generated_text_sha256": None,
        "generated_text_length": None,
        "generated_text_saved": False,
        "quality_passed": False,
        "finish_reason": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "error_class": error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--long-capped-followup", action="store_true",
        help="exactly two fixed synthetic cases, capped at 256 tokens each",
    )
    args = parser.parse_args(argv)
    cases = FOLLOWUP_CASES if args.long_capped_followup else CASES
    if not is_verified_nas():
        print("NO_GO: verified NAS mount or Joyner hostname missing", file=sys.stderr)
        return 2
    identity = original.extract_guarded_identity()
    if not identity or not original.allow_paths(*identity):
        print("NO_GO: guarded model identity/key custody", file=sys.stderr)
        return 2
    model, key_file = identity
    if original.sha256(model) != original.EXPECTED_MODEL_SHA256:
        print("NO_GO: guarded weights changed", file=sys.stderr)
        return 2
    token = key_file.read_text().strip()
    if not token or "\n" in token or len(token)>512:
        print("NO_GO: invalid guarded credential format", file=sys.stderr)
        return 2
    uid = uuid.uuid4().hex[:12]
    now = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = NAS / f"joyner-minicpm-dualpath-{now}-{uid}.json"
    receipt = {
        "schema": "joyner_minicpm_dualpath_nas_trace.v1",
        "created_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "receipt_id": uid,
        "producer_hostname": "joyner",
        "executed_as_user": "joyner",
        "model_sha256": original.EXPECTED_MODEL_SHA256,
        "model_size_bytes": original.EXPECTED_MODEL_BYTES,
        "requested_api_alias": ALIAS,
        "model_alias_explicitly_not_admitted": True,
        "synthetic_only": True,
        "requests_are_sequential": True,
        "fixture_set": "long_capped_followup" if args.long_capped_followup else "initial_short",
        "raw_prompts_saved": False,
        "model_outputs_saved": False,
        "credentials_saved": False,
        "authorization_class": "owner_local_private_api_key_file",
        "storage": "NAS_CIFS_READ_AFTER_WRITE_VERIFIED",
        "status": "IN_PROGRESS",
        "cutover_authorized": False,
        "production_routing_changed": False,
        "requests": [],
    }
    persist(path, receipt)
    for name, prompt, max_tokens in cases:
        body = {
            "model": ALIAS,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "seed": 7,
            "stream": False,
        }
        prompt_digest = hashlib.sha256(prompt.encode()).hexdigest()
        for role in ("legacy", "guarded"):
            entry = {
                "id": uuid.uuid4().hex,
                "case": name,
                "role": role,
                "requested_model_alias": ALIAS,
                "prompt_sha256": prompt_digest,
                "synthetic_input": True,
                "max_tokens_requested": max_tokens,
                "started_utc": datetime.datetime.now(datetime.UTC).isoformat(),
                "state": "REQUEST_PENDING",
                "request_parameters": {"temperature": 0, "seed": 7,
                                       "stream": False, "max_tokens": max_tokens},
            }
            receipt["requests"].append(entry)
            persist(path, receipt)  # never send unless custody is writable
            try:
                outcome = request(role, body, token if role == "guarded" else None, name)
            except Exception as exc:
                outcome = {"http_code": None, "error_class": type(exc).__name__,
                           "quality_passed": False, "generated_text_saved": False}
            entry.update(outcome)
            entry["completed_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
            entry["state"] = "REQUEST_RECORDED"
            persist(path, receipt)
    tests=[]
    for name, _, _ in cases:
        rows = [x for x in receipt["requests"] if x["case"] == name]
        tests.append({"case":name,
                      "responses_identical_sha256":len(rows)==2 and
                        rows[0].get("generated_text_sha256") ==
                        rows[1].get("generated_text_sha256") and
                        rows[0].get("http_code") == rows[1].get("http_code") == 200,
                      "both_quality_pass":len(rows)==2 and
                        all(r.get("quality_passed") is True for r in rows),
                      "both_http_200":len(rows)==2 and
                        all(r.get("http_code")==200 for r in rows)})
    receipt["comparisons"] = tests
    receipt["status"] = "COMPLETE_DIAGNOSTIC_ONLY"
    receipt["quality_acceptance"] = bool(tests and all(x["both_quality_pass"] for x in tests))
    receipt["cutover_authorized"] = False
    receipt["completed_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
    digest = persist(path, receipt)
    print(json.dumps({"path":str(path),"sha256":digest,
                      "request_count":len(receipt["requests"]),
                      "quality_acceptance":receipt["quality_acceptance"],
                      "comparisons":tests,"cutover_authorized":False},sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
