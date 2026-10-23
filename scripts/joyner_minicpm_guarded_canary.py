#!/usr/bin/env python3
"""Bounded synthetic MiniCPM source/guarded equivalence check on Joyner.

Reads the running guarded process's own api-key-file as its owner, in memory.
Never prints keys or request/response text, follows redirects, changes routing,
starts/stops servers, or accepts user-specified external destinations.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import socket
import stat
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SRC_URL = "http://127.0.0.1:1234"
GUARDED_URL = "http://100.83.215.83:18320"
EXPECTED_MODEL_SHA256 = "81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa"
EXPECTED_MODEL_BYTES = 688065920
SYNTHETIC_PROMPT = "Compute 17 + 25. Reply with just the decimal integer."

class NeverRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def extract_guarded_identity(proc: Path = Path("/proc")) -> tuple[Path, Path] | None:
    found = []
    for path in proc.iterdir():
        if not path.name.isdecimal():
            continue
        try:
            args = [x.decode("utf-8", "replace") for x in
                    (path / "cmdline").read_bytes().split(b"\0") if x]
            if not args or Path(args[0]).name != "llama-server":
                continue
            def arg(flag, argv=args):
                hits = [argv[i+1] for i in range(len(argv)-1) if argv[i] == flag]
                return hits[0] if len(hits) == 1 else None
            if arg("--port") != "18320":
                continue
            model = arg("--model") or arg("-m")
            key_file = arg("--api-key-file")
            if not model or not key_file:
                return None
            uid_line = next(line for line in (path / "status").read_text().splitlines()
                            if line.startswith("Uid:"))
            if int(uid_line.split()[1]) != os.geteuid():
                return None
            found.append((Path(model).resolve(), Path(key_file).resolve()))
        except (OSError, ValueError, StopIteration):
            continue
    return found[0] if len(found) == 1 else None


def allow_paths(model: Path, key_file: Path) -> bool:
    home = Path("/home/joyner").resolve()
    if os.geteuid() != 1000 or socket.gethostname().split(".")[0] != "joyner":
        return False
    if not model.is_relative_to(home) or not key_file.is_relative_to(home):
        return False
    if not model.is_file() or model.stat().st_size != EXPECTED_MODEL_BYTES:
        return False
    if not key_file.is_file():
        return False
    mode = key_file.stat().st_mode
    if not stat.S_ISREG(mode) or mode & stat.S_IROTH:
        return False
    return True


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def http_json(url: str, *, payload: dict | None = None, key: str | None = None):
    if url not in (SRC_URL + "/completion", GUARDED_URL + "/completion",
                   SRC_URL + "/v1/models", GUARDED_URL + "/v1/models"):
        raise ValueError("unexpected endpoint")
    opener = build_opener(ProxyHandler({}), NeverRedirect())
    headers = {"Accept": "application/json", "User-Agent": "joyner-guarded-canary/1"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    req = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                  headers=headers, method="POST" if payload is not None else "GET")
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=12) as response:
            value = json.loads(response.read(128000))
            return response.status, value, round((time.perf_counter() - started)*1000,2)
    except HTTPError as e:
        e.close()
        return e.code, None, round((time.perf_counter()-started)*1000,2)
    except (OSError, URLError, ValueError):
        return None, None, round((time.perf_counter()-started)*1000,2)


def model_names(data):
    if not isinstance(data,dict):
        return []
    return sorted(x.get("id") for x in data.get("data",[])
                  if isinstance(x,dict) and isinstance(x.get("id"),str))[:4]


def infer_result(status, data, ms):
    text = data.get("content") if isinstance(data,dict) else None
    text = text if isinstance(text,str) else ""
    timings = (
        data.get("timings")
        if isinstance(data, dict) and isinstance(data.get("timings"), dict) else {}
    )
    return {"http_status":status, "latency_ms":ms,
            "content_nonempty":bool(text.strip()),
            "answer_42_present":bool(re.search(r"(?<!\d)42(?!\d)",text)),
            "text_sha256":hashlib.sha256(text.encode()).hexdigest() if text else None,
            "generated_tokens":timings.get("predicted_n"),
            "text_not_persisted":True}


def assess_receipt(data):
    legacy=data["legacy"]
    guarded=data["guarded"]
    reasons=[]
    if not data["verified_guarded_model_sha256_matches"]:
        reasons.append("guarded_weights_not_verified")
    if data["anonymous_guarded_model_list_status"] not in (401,403):
        reasons.append("anonymous_guarded_access_not_rejected")
    if guarded["http_status"]!=200 or not guarded["answer_42_present"]:
        reasons.append("guarded_synthetic_task_failed")
    if legacy["http_status"]!=200 or not legacy["answer_42_present"]:
        reasons.append("legacy_synthetic_task_failed")
    if not data["candidate_model_aliases_match"]:
        reasons.append("model_alias_mismatch")
    # Success is intentionally NOT a cutover permit: trace custody, production
    # authorization, client census, rollback, and response invariants remain.
    return {"status":"DIAGNOSTIC_ONLY", "functional_canary_gates_passed":len(reasons)==0,
            "remaining_gaps":["authorized_caller_acceptance","per_request_trace_custody",
                              "real_consumer_census","rollback_witness","performance_slo"],
            "can_cutover":False, "can_stop_service":False, "reasons":reasons}


def main():
    result={"schema":"joyner_minicpm_guarded_canary.v1",
            "observed_utc":datetime.datetime.now(datetime.UTC).isoformat(),
            "read_only_service_configuration":True,
            "model_request_is_synthetic":True,
            "source_host":"joyner","no_secrets_or_content_logged":True}
    identity = extract_guarded_identity()
    if not identity or not allow_paths(*identity):
        print(json.dumps({"status":"NO_GO_PREFLIGHT","model_request_sent":False}))
        return 2
    model, key_file = identity
    if sha256(model) != EXPECTED_MODEL_SHA256:
        print(json.dumps({"status":"NO_GO_MODEL_HASH","model_request_sent":False}))
        return 2
    key = key_file.read_text().strip()
    if not key or len(key)>512 or "\n" in key:
        print(json.dumps({"status":"NO_GO_KEY_FORMAT","model_request_sent":False}))
        return 2
    src_code, src_models, _ = http_json(SRC_URL + "/v1/models")
    noauth_code, _, _ = http_json(GUARDED_URL + "/v1/models")
    auth_code, auth_models, _ = http_json(GUARDED_URL + "/v1/models",key=key)
    if src_code != 200 or noauth_code not in (401,403) or auth_code != 200:
        print(json.dumps({"status":"NO_GO_AUTH_BOUNDARY","model_request_sent":False,
                          "source_status":src_code,"anonymous_guarded_status":noauth_code,
                          "authorized_guarded_status":auth_code}))
        return 2
    names1, names2 = model_names(src_models), model_names(auth_models)
    prompt={"prompt":SYNTHETIC_PROMPT,"n_predict":16,"temperature":0,
            "seed":7,"cache_prompt":False,"stream":False}
    legacy=infer_result(*http_json(SRC_URL + "/completion",payload=prompt))
    guarded=infer_result(*http_json(GUARDED_URL + "/completion",payload=prompt,key=key))
    result.update({"verified_guarded_model_sha256_matches":True,
                   "guarded_model_sha256":EXPECTED_MODEL_SHA256,
                   "model_size_bytes":EXPECTED_MODEL_BYTES,
                   "legacy_model_aliases":names1,"guarded_model_aliases":names2,
                   "candidate_model_aliases_match":bool(names1) and names1==names2,
                   "anonymous_guarded_model_list_status":noauth_code,
                   "authorized_guarded_model_list_status":auth_code,
                   "legacy":legacy,"guarded":guarded})
    result["admission"]=assess_receipt(result)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result["admission"]["functional_canary_gates_passed"] else 3


if __name__=="__main__":
    sys.exit(main())
