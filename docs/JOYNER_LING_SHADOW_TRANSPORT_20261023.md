# Joyner Ling shadow transport: offline/mock qualification only

October 23, 2026. The live Ling model lane remains on
127.0.0.1:1236, with separate legacy LAN and Tailscale socat
forwarders. This new module is NOT deployed, not wired into the
auto-router ASGI application, not bound to any socket, and has NO
production upstream address or model-loading code.

## Scope

src/auto_router/joyner_ling_shadow_transport.py provides a mock
ASGI transport. It accepts exactly POST /v1/chat/completions and
requires one correctly formed bearer token before processing a
bounded request. The verifier and upstream are explicitly injected:
tests connect the verifier from PR #86 to a DISPOSABLE async
generator. No open network socket or live inference is involved.

It preserves the raw chunks of the mock SSE reply, even when one
Unicode scalar is divided across byte boundaries. Streaming chunks
are bounded and are written with awaited send, rather than buffered
as an entire response. Mock disconnect terminates the stream and
closes the producer. Duplicate/malformed Authorization, transfer
encoding ambiguity, excessive length, missing identity and request
parse errors are denied without invoking the mock backend.
Bad upstream data cannot emit a fabricated SSE [DONE] marker.

Response headers include no-store and nosniff; no CORS wildcard,
client Authorization header, bearer credentials or prompts are ever
forwarded in response metadata. Error responses are generic, not
a copy of private exception text. These are synthetic checks,
not a real authenticated ingress.

A joint end-to-end disposable test uses an in-memory generated
Ed25519 private key, synthetic token, explicit trusted principal,
model digest and projection generation, plus a temporary mode0600
SQLite one-use receipt database. The first request reaches a fake
upstream exactly once; replay is rejected before dispatch.

## Deliberate limitations (NO-GO)

This is NOT a production-capable reverse proxy. It does not
authenticate a real client at the Tailnet ingress, mint signed
tokens, prove owner/projection or GPU leases, attest the real Ling
GGUF digest, terminate TLS, or connect to 127.0.0.1:1236. It has
no live systemd unit or process. The backend interface is a test
callback only.

Producer starvation, mid-stream errors, cancellation, queue and
backpressure behavior must undergo more fault-injection testing
with a real ASGI server and mock service on isolated loopback
before any deployment. A timed-out or truncated stream cannot
be interpreted as a successful completion; only a fully validated
SSE done marker can indicate successful test completion.

The guarded Tailnet :18321 still serves Toolcall v5 3B, not Ling.
Old forwards remain in place and are not certified unused by
the 12-second client census. The shared API-key-file guarded
endpoints remain insufficient per-user receipt evidence.

Next requirements: independently source and verify an actual
owner-authorized Ling model digest, sealed signing key custody,
per-user trust roots/negative test coverage, crash/restart
receipt fencing, real client and streaming acceptance, rollback,
and exclusive GPU memory/resource leases. This module alone
grants no routing, model promotion or training permission.

Tracking: auto-router issues #81 and #37.
