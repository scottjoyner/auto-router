#!/usr/bin/env python3
"""Local read-only authenticated inference proof, never prints API keys or prompt."""
from __future__ import annotations
import argparse
import json
import time
import ipaddress
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from urllib.request import Request


def vram(path: str) -> int | None:
    if not path:
        return None
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


class DenyRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError("refusing_redirect_for_authenticated_request")


# Hard-code proven GPU listeners to prevent accidental credential exfiltration to
# another Tailnet process. Update only after reviewing the new service owner.
ALLOWED_GPU_LISTENERS = {
    ("100.108.99.47", 18271),
    ("100.108.99.47", 18302),
    ("100.83.215.83", 18320),
    ("100.83.215.83", 18321),
}


def validate_private_url(url: str) -> None:
    parsed = urlsplit(url)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise RuntimeError("require_ip_literal_and_port") from exc
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise RuntimeError("require_local_http_without_userinfo")
    if not (address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")):
        raise RuntimeError("destination_is_not_private_loopback_or_tailnet")
    if (str(address), port) not in ALLOWED_GPU_LISTENERS:
        raise RuntimeError("destination_is_not_a_registered_gpu_listener")


def request(url: str, key: str, body: bytes | None = None, timeout: int = 25):
    headers = {"Authorization": "Bearer " + key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers)
    with build_opener(DenyRedirects).open(req, timeout=timeout) as resp:
        return resp.status, json.load(resp)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--key-file", type=Path, required=True)
    p.add_argument("--expected-model", default="")
    p.add_argument("--list-only", action="store_true", help="Print model IDs without generating")
    p.add_argument("--vram-sysfs", default="")
    p.add_argument("--timeout", type=int, default=45)
    a = p.parse_args()
    key = a.key_file.read_text().strip()
    if not key:
        raise RuntimeError("Empty keyfile")
    base = a.url.rstrip("/")
    validate_private_url(base)
    start_vram = vram(a.vram_sysfs)
    started = time.monotonic()
    try:
        status, models = request(base + "/v1/models", key, timeout=a.timeout)
        names = [x.get("id") for x in models.get("data", []) if isinstance(x, dict)]
        if a.list_only:
            print(json.dumps({"model_ids": names, "status": status}))
            return 0 if status == 200 else 4
        if status != 200 or a.expected_model not in names:
            raise RuntimeError("expected_model_not_returned")
        req_body = json.dumps({
            "model": a.expected_model,
            "messages": [{"role": "user", "content": "Reply using only the word READY."}],
            "max_tokens": 96, "temperature": 0
        }).encode()
        status, reply = request(base + "/v1/chat/completions", key, req_body, a.timeout)
        choice = (reply.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = reply.get("usage") or {}
        output = {
            "ok": status == 200 and int(usage.get("completion_tokens") or 0) > 0,
            "http_status": status,
            "expected_model_listed": True,
            "response_text_nonempty": bool(msg.get("content")),
            "reasoning_text_nonempty": bool(msg.get("reasoning_content")),
            "finish_reason": choice.get("finish_reason"),
            "completion_tokens": usage.get("completion_tokens"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "elapsed_seconds": round(time.monotonic()-started, 3),
            "vram_before_bytes": start_vram,
            "vram_after_bytes": vram(a.vram_sysfs)
        }
        print(json.dumps(output, sort_keys=True))
        return 0 if output["ok"] else 4
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
        print(json.dumps({
            "ok": False, "error_type": type(exc).__name__,
            "status": getattr(exc, "code", None),
            "elapsed_seconds": round(time.monotonic()-started, 3),
            "vram_before_bytes": start_vram,
            "vram_after_bytes": vram(a.vram_sysfs)
        }, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
