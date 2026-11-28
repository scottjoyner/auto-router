#!/usr/bin/env python3
"""Ephemeral owner-local authenticated Ling shadow, not a production gateway.

A single test-only process binds 127.0.0.1 on an OS-assigned port, issues
two fixed synthetic requests through existing Ling :1236, audits every
model/catalog request to CIFS NAS before dispatch, and exits. No service
unit, Tailnet listener, firewall, router, or resident GGUF changes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import uuid
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 1236
ALIAS = "joyner-ling"
LING_MODEL = Path("/home/scott/fleet-gpu/models/Ling-3.0-tiny-abliterated-APEX-I-Compact.gguf")
LING_MODEL_BYTES = 3985017952
LING_MODEL_SHA256 = "f661876a860cc7093583a35a8187e66696c73a2cd512ece8117477a05ceda115"
LING_UID = 1001  # scott owns the already-running legacy Ling service
NAS = Path("/nas/fileserver/fleet-reports/trace-custody")
FIXTURES = (
    ("literal", "Reply with only the uppercase word READY.", 192),
    ("json", 'Reply with the exact JSON object {"ready":true}. No markdown.', 192),
)
SCHEMA = "joyner_ling_ephemeral_auth_shadow.v1"
MAX_BODY = 4096
MAX_RESPONSE = 120000


def check_host(*, hostname=None, mount_check=None):
    if (hostname or socket.gethostname().split(".")[0]) != "joyner":
        raise PermissionError("wrong host")
    if os.geteuid() != 1000:
        raise PermissionError("wrong owner")
    proc = mount_check
    if proc is None:
        proc = subprocess.run(
            ["findmnt", "-rn", "-T", str(NAS), "-o", "FSTYPE"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    filesystems = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode != 0 or not filesystems or filesystems[-1] != "cifs":
        raise PermissionError("NAS CIFS custody not mounted")
    if not NAS.is_dir():
        raise PermissionError("NAS custody absent")
    # Existing Ling backend itself must remain a loopback-only service.
    if not socket.gethostbyname("localhost").startswith("127."):
        raise PermissionError("loopback resolution unverified")


def verify_ling_backend(proc=Path("/proc")):
    """Fail closed on a changed Ling GGUF, process identity, or serving alias."""
    matched = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            args = [
                part.decode("utf-8", "replace")
                for part in (entry / "cmdline").read_bytes().split(b"\0")
                if part
            ]
            if not args or Path(args[0]).name != "llama-server":
                continue
            flags = {}
            for name in ("--port", "-m", "--alias", "--host"):
                values = [args[i + 1] for i in range(len(args) - 1) if args[i] == name]
                if len(values) != 1:
                    flags = {}
                    break
                flags[name] = values[0]
            if flags.get("--port") != str(PORT):
                continue
            identity = next(
                line
                for line in (entry / "status").read_text().splitlines()
                if line.startswith("Uid:")
            ).split()[1]
            if (
                int(identity) != LING_UID
                or flags.get("--host") != "127.0.0.1"
                or flags.get("--alias") != ALIAS
                or Path(flags.get("-m", "invalid")).resolve() != LING_MODEL
            ):
                raise PermissionError("Ling process owner, alias or model changed")
            matched.append(int(entry.name))
        except (OSError, ValueError, StopIteration):
            continue
    if len(matched) != 1:
        raise PermissionError("Ling serving process not unique")
    if not LING_MODEL.is_file() or LING_MODEL.stat().st_size != LING_MODEL_BYTES:
        raise PermissionError("Ling file identity invalid")
    h = hashlib.sha256()
    with LING_MODEL.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    if not hmac.compare_digest(h.hexdigest(), LING_MODEL_SHA256):
        raise PermissionError("Ling full GGUF hash mismatch")
    return {
        "verified_ling_model_sha256": LING_MODEL_SHA256,
        "verified_model_bytes": LING_MODEL_BYTES,
        "verified_process_count": len(matched),
        "verified_owner": "scott",
        "backend_port": PORT,
    }


def read_json_bytes(raw: bytes):
    if len(raw) > MAX_BODY:
        raise ValueError("body too large")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("JSON object required")
    return obj


def validate_body(obj):
    if set(obj) - {"model", "messages", "max_tokens", "temperature", "seed", "stream"}:
        raise ValueError("unsupported request fields")
    if obj.get("model") != ALIAS or obj.get("stream") is not False:
        raise ValueError("invalid alias or streaming")
    if type(obj.get("max_tokens")) is not int or obj["max_tokens"] > 192 or obj["max_tokens"] < 1:
        raise ValueError("token cap invalid")
    if obj.get("temperature") != 0 or obj.get("seed") != 7:
        raise ValueError("non-reproducible sampling config")
    messages = obj.get("messages")
    if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict):
        raise ValueError("one synthetic message required")
    if set(messages[0]) != {"role", "content"} or messages[0].get("role") != "user":
        raise ValueError("invalid message structure")
    if not any(
        messages[0]["content"] == text and obj["max_tokens"] == cap for _, text, cap in FIXTURES
    ):
        raise ValueError("only pinned synthetic fixtures allowed")
    return obj


def response_metadata(payload):
    if not isinstance(payload, dict):
        raise ValueError("backend non-object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("backend choices missing")
    ch = choices[0]
    msg = ch.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("content"), str):
        raise ValueError("backend content invalid")
    text = msg["content"]
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "response_model": payload.get("model") if isinstance(payload.get("model"), str) else None,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text_length": len(text),
        "text_not_stored": True,
        "finish_reason": ch.get("finish_reason"),
        "prompt_tokens": usage.get("prompt_tokens")
        if type(usage.get("prompt_tokens")) is int
        else None,
        "completion_tokens": usage.get("completion_tokens")
        if type(usage.get("completion_tokens")) is int
        else None,
    }


def quality(case, payload):
    if not isinstance(payload, dict):
        return False
    try:
        answer = payload["choices"][0]["message"]["content"].strip()
        if case == "literal":
            return answer == "READY"
        if case == "json":
            return json.loads(answer) == {"ready": True}
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return False


class Audit:
    def __init__(self, directory=NAS):
        now = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = directory / f"joyner-ling-auth-shadow-{now}-{uuid.uuid4().hex[:12]}.json"
        self.document = {
            "schema": SCHEMA,
            "host": "joyner",
            "date_utc": dt.datetime.now(dt.UTC).isoformat(),
            "backend": "127.0.0.1:1236",
            "shadow_bind": "127.0.0.1:ephemeral",
            "model": ALIAS,
            "synthetic_only": True,
            "trace_includes_denied_and_failed_requests": True,
            "raw_content_saved": False,
            "secret_saved": False,
            "cutover_authorized": False,
            "routed_to_fleet": False,
            "requests": [],
            "status": "IN_PROGRESS",
        }
        self._persist()

    def _persist(self):
        raw = (json.dumps(self.document, indent=2, sort_keys=True) + "\n").encode()
        if len(raw) > 80_000:
            raise ValueError("audit too large")
        tmp = self.path.parent / ("." + self.path.name + "." + uuid.uuid4().hex + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            if self.path.read_bytes() != raw:
                raise OSError("NAS receipt readback mismatch")
        finally:
            if tmp.exists():
                tmp.unlink()
        return hashlib.sha256(raw).hexdigest()

    def pending(self, kind, authorized, body_sha256=None):
        row = {
            "attempt_id": uuid.uuid4().hex,
            "observed_utc": dt.datetime.now(dt.UTC).isoformat(),
            "kind": kind,
            "auth_verdict": "pass" if authorized else "denied",
            "body_sha256": body_sha256,
            "state": "PENDING",
            "content_saved": False,
        }
        self.document["requests"].append(row)
        self._persist()
        return row

    def finish(self, row, status, classification, elapsed_ms=None, metadata=None):
        row.update(
            {
                "state": "FINISHED",
                "http_code": status,
                "outcome_class": classification,
                "elapsed_ms": elapsed_ms,
                "result": metadata,
                "completed_utc": dt.datetime.now(dt.UTC).isoformat(),
            }
        )
        return self._persist()

    def close(self):
        self.document["status"] = "COMPLETE_RESEARCH_ONLY"
        return self._persist()


class ShadowHTTP(HTTPServer):
    allow_reuse_address = False

    def __init__(self, audit, token, backend=None):
        super().__init__(("127.0.0.1", 0), ShadowHandler)
        self.audit = audit
        self.token = token
        self.backend = backend or backend_request


def backend_request(body):
    conn = HTTPConnection("127.0.0.1", PORT, timeout=15)
    try:
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError("backend status not 200")
        raw = resp.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError("backend response too large")
        obj = json.loads(raw)
        response_metadata(obj)  # enforce valid response before returning
        return obj
    finally:
        conn.close()


class ShadowHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        return  # no request line or headers logged

    def _auth(self):
        authorization = self.headers.get("Authorization", "")
        value = authorization[7:] if authorization.startswith("Bearer ") else ""
        return bool(value) and hmac.compare_digest(value, self.server.token)

    def _reply(self, status, data):
        raw = json.dumps(data, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != "/v1/models":
            self._reply(404, {"error": "not_found"})
            return
        authorized = self._auth()
        try:
            row = self.server.audit.pending("catalog", authorized)
            code = 200 if authorized else 401
            self.server.audit.finish(row, code, "catalog_verified" if authorized else "auth_denied")
        except (OSError, ValueError):
            self._reply(503, {"error": "custody_unavailable"})
            return
        if authorized:
            self._reply(200, {"object": "list", "data": [{"id": ALIAS, "object": "model"}]})
        else:
            self._reply(401, {"error": "unauthorized"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._reply(404, {"error": "not_found"})
            return
        authorized = self._auth()
        body_len = self.headers.get("Content-Length", "")
        valid_length = body_len.isdecimal() and 0 < int(body_len) <= MAX_BODY
        raw = self.rfile.read(int(body_len)) if valid_length else b""
        sha = hashlib.sha256(raw).hexdigest() if raw else None
        try:
            row = self.server.audit.pending("chat_completion", authorized, sha)
        except (OSError, ValueError):
            self._reply(503, {"error": "custody_unavailable"})
            return
        start = time.perf_counter()
        result = None
        try:
            if not authorized:
                status = 401
                state = "auth_denied"
                result = {"error": "unauthorized"}
            elif not valid_length:
                status = 413
                state = "invalid_length"
                result = {"error": "invalid_length"}
            else:
                try:
                    body = validate_body(read_json_bytes(raw))
                except (ValueError, TypeError, KeyError):
                    status = 400
                    state = "invalid_fixture"
                    result = {"error": "invalid_fixture"}
                else:
                    try:
                        result = self.server.backend(body)
                        status = 200
                        state = "completed"
                    except (OSError, TimeoutError, RuntimeError, ValueError, HTTPException):
                        status = 502
                        state = "backend_failure"
                        result = {"error": "upstream_unavailable"}
            extra = response_metadata(result) if status == 200 else None
            if extra is not None:
                prompt = body["messages"][0]["content"]
                extra["case"] = next(n for n, t, c in FIXTURES if t == prompt)
                extra["quality_passed"] = quality(extra["case"], result)
            try:
                self.server.audit.finish(
                    row, status, state, round((time.perf_counter() - start) * 1000, 2), extra
                )
            except (OSError, ValueError):
                self._reply(503, {"error": "custody_unavailable"})
                return
            self._reply(status, result)
        except (BrokenPipeError, ConnectionResetError):
            return


def issue(port, method, path, token=None, body=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=18)
    try:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(
            method,
            path,
            body=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        resp = conn.getresponse()
        raw = resp.read(MAX_RESPONSE + 1)
        return resp.status, json.loads(raw)
    finally:
        conn.close()


def self_test():
    check_host()
    identity = verify_ling_backend()
    token = secrets.token_urlsafe(32)
    audit = Audit()
    audit.document["backend_identity"] = identity
    audit._persist()
    server = ShadowHTTP(audit, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    observations = []
    try:
        tests = [
            ("anonymous_catalog", "GET", "/v1/models", None, None),
            ("authorized_catalog", "GET", "/v1/models", token, None),
            (
                "anonymous_inference",
                "POST",
                "/v1/chat/completions",
                None,
                {
                    "model": ALIAS,
                    "messages": [{"role": "user", "content": FIXTURES[0][1]}],
                    "max_tokens": FIXTURES[0][2],
                    "temperature": 0,
                    "seed": 7,
                    "stream": False,
                },
            ),
        ]
        for name, text, max_tokens in FIXTURES:
            tests.append(
                (
                    name,
                    "POST",
                    "/v1/chat/completions",
                    token,
                    {
                        "model": ALIAS,
                        "messages": [{"role": "user", "content": text}],
                        "max_tokens": max_tokens,
                        "temperature": 0,
                        "seed": 7,
                        "stream": False,
                    },
                )
            )
        for name, method, path, key, body in tests:
            code, response = issue(server.server_port, method, path, key, body)
            meta = response_metadata(response) if code == 200 and method == "POST" else None
            observations.append(
                {
                    "fixture": name,
                    "http_code": code,
                    "quality_passed": quality(name, response) if meta else None,
                    "backend_model_alias": meta["response_model"] if meta else None,
                    "generated_text_sha256": meta["text_sha256"] if meta else None,
                    "content_retained": False,
                }
            )
        audit.document["self_test_results"] = observations
        audit.document["requests_processed"] = len(audit.document["requests"])
        digest = audit.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=4)
    return {
        "nas_path": str(audit.path),
        "nas_sha256": digest,
        "request_count": len(observations),
        "results": observations,
        "listener_stopped": not thread.is_alive(),
        "cutover_authorized": False,
    }


if __name__ == "__main__":
    import sys

    try:
        print(json.dumps(self_test(), sort_keys=True))
    except (OSError, PermissionError, ValueError, subprocess.TimeoutExpired) as err:
        print(json.dumps({"status": "NO_GO", "error_class": type(err).__name__}), file=sys.stderr)
        sys.exit(2)
