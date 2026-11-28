# Joyner Ling: isolated signed-receipt security contract

October 23, 2026. This is NOT DEPLOYED. No new proxy, listener,
model server, GPU load, credential, routing or training change.

## Why it matters

Live legacy Joyner port 1236 serves Ling 3.0 tiny. Existing guarded
port 18321 serves a different Toolcall v5 3B model, so it is not
a Ling substitute. Guarded port 18320 serves MiniCPM5, not Ling.
The guarded llama.cpp services use API key files: their 401 behavior
does not prove per-user receipts, provenance, or replay protection.
The existing auto-router executor JWT has audience auto-router and
should not be reused directly at a Ling ingress.

## Future identity and replay contract

The isolated src/auto_router/joyner_ling_receipt_candidate.py
verifies an Ed25519 token with distinct typ, kid, issuer, audience,
scope inference:ling and exact Ling alias. It rejects cross-audience
auto-router tokens, HS256, malformed or duplicate JSON and invalid
signatures. User identity, task/claim/agent lineage, JWT ID and
projection generation must be present and well-formed.

The caller principal, current generation and resident model GGUF
SHA256 must come from INDEPENDENT trusted and fenced sources,
not a request header or self-reported model catalog. Tokens have a
maximum 120-second lifetime and exactly one attempt. The signed body
SHA256 binds the actual request bytes, selected model and output
budget, preventing request substitution.

The test-only persistence path inserts a one-use JWT ID receipt
through SQLite BEGIN IMMEDIATE, and preserves monotonic generation
high-water per principal. An owner-private directory and 0600 database
are required. No bearer token, message body or model prompt is
stored in the receipt DB.

Verification NEVER forwards to the model. The returned synthetic
receipt explicitly says proxy cutover and training are NOT approved.

Test keys are freshly generated in isolated disposable fixtures.
No real model or client credential is accessed. No production route,
service, container, CLI, or auto-router middleware imports this module.

## Independent deployment prerequisites

1. Inventory actual consumers of the old unguarded forwards. A
   12-second 25-snapshot no-client interval does NOT prove no users.
2. Obtain a service-owner witnessed actual Ling GGUF digest and
   pinned model runtime; do not bypass access controls for data.
3. Establish independent Ed25519 signing-key custody, rotation and
   revocation, trusted per-user identity and current projection
   freshness, resource lease/claim fencing and backend ownership.
4. Qualify durable receipts across crash/restart, cross-process
   races, shared storage, outage and replay/rollback conditions.
5. Build and test a separate proxy against a DISPOSABLE MOCK backend,
   including SSE streaming, byte integrity, disconnect, backpressure,
   log redaction, bad credentials, route denial and rollback.
6. Stage a loopback-only no-deployment test. Do not touch existing
   Joyner 1234/1236 or guarded 18320/18321 services without a
   controlled, verified client migration.

See auto-router issues 81 and 37. xwing training HOLD is unaffected.
