# Joyner R9700 — authenticated Ling shadow tested against the live model

**Physical observation:** Saturday, October 24, 2026, approximately 04:34–04:42 EDT.
**Result:** local authentication and synthetic Ling task compatibility
qualified in a **temporary loopback-only shadow**. **Fleet deployment,
legacy-forward cutover and exclusive-GPU admission remain NO_GO.**

## Why this approach

Joyner currently has four working inference servers, and R9700 GTT is
nearly exhausted (~7.2 of 7.38 GiB). Loading another Ling copy would
consume unnecessary GPU system memory and risk the existing services.

The legacy Ling model is already resident as the OS user `scott` on
`127.0.0.1:1236`, with model alias `joyner-ling`. The legacy
LAN/Tailnet `socat` forwards are not authenticated; disabling them
without authoritative consumer inventory would be disruptive.

Instead, `scripts/joyner_ling_guarded_shadow.py` runs as user `joyner`
and opens only `127.0.0.1:<ephemeral>` for the duration of a single test.
It forwards only allowlisted synthetic tests into the existing
`127.0.0.1:1236` service. It never loads a GGUF, opens a Tailnet
listener, edits an existing systemd unit, changes a model route,
reconfigures `socat`, or enables production dispatch.

## Backend custody and full model identity

The script fails closed unless it verifies the current Ling process
is uniquely serving the exact alias and loopback port, owned by the
expected OS user, and its **full GGUF content** matches:

| Item | Physical proof |
| --- | --- |
| Model | `Ling-3.0-tiny-abliterated-APEX-I-Compact.gguf` |
| Exact model bytes | 3,985,017,952 |
| Full SHA-256 | `f661876a860cc7093583a35a8187e66696c73a2cd512ece8117477a05ceda115` |
| Expected incumbent | User `scott`, port `1236`, loopback, alias `joyner-ling` |
| Script SHA-256 on controller and Joyner | `3c1e18f7439bab5245c5968974acc458437fc676bfd85b464f0ea5e9e49438c0` |

Full SHA-256 was first computed against the actual model on Joyner's
local ext4 SSD using low-priority I/O. The shadow revalidates that SHA
at startup. None of the model bytes or credentials are copied to Git.

A fresh **in-memory** random bearer token is used only for that short
loopback test. It is never written to disk, transported outside Joyner,
included in a receipt, or made available through a public listener.
The shadow uses the standard library's constant-time comparison to
reject incorrect credentials.

## Physical test #1 — transport and authentication

Initial test with the original NAS preflight deliberately returned
NO_GO without inference because Joyner's `findmnt` reports an
`autofs` and `cifs` layered mount. The hardened preflight now
requires that the **final backing mount** be CIFS; it does not
accept `autofs` alone.

After that correction, the ephemeral shadow processed exactly five
bounded requests with an on-NAS audit for each authorization decision:

| Test | Actual HTTP result | Meaning |
| --- | --- | --- |
| Anonymous GET `/v1/models` | 401 | Denied |
| Authorized GET `/v1/models` | 200 | Shadow catalog advertises `joyner-ling` |
| Anonymous POST `/v1/chat/completions` | 401 | Denied; no backend inference |
| Authorized synthetic literal `READY` | 200 | Correct, 17 generated tokens, finished normally |
| Authorized synthetic JSON-ready object | 200 | Correct, 39 generated tokens, finished normally |

The final SHA-pinned model/process identity run returned the same
successful results and ended with the temporary listener **stopped**.
Both response model identifiers were `joyner-ling`.

Authorized synthetic test request latencies from the guarded path were
**794.32 ms** and **291.86 ms**, respectively. These are not production
SLOs or p95 measurements. Only exact fixed prompts, bounded maximum
tokens, temperature=0 and seed=7 are accepted by the shadow.
Streaming, tools, arbitrary chat prompts, model overrides, proxy
destinations and failover are **not implemented**.

## NAS-first audit and independent verification

The shadow writes an atomic pending NAS record **before every permitted
model request**, including a denied or failed request, and a completed
record afterward. If a NAS write fails before forwarding, the shadow
returns 503 and does not invoke the model. Receipts contain request
kind, tokenless authorization verdict, status/outcome, elapsed time,
model provenance, generated-token count, SHA-256 of model output and
quality flag. They do not retain prompts, generated content, bearer
tokens, raw HTTP headers or peer IP addresses.

The final physically verified receipt:

`/nas/fileserver/fleet-reports/trace-custody/joyner-ling-auth-shadow-20261024T084054Z-0dd5f6db242a.json`

SHA-256:
`aa24a09e3d69524421d629edd1b34397e52fd0415f0f7075bffed40c33a7de16`

The exact checksum was independently obtained from **Joyner and
x1-370**, and the five completed audit entries validated. Earlier
pre-identity positive receipt:
`joyner-ling-auth-shadow-20261024T083447Z-d7d5f01bea26.json`,
SHA-256 `cf608b3bd6978ccc7f1790f49c8e2f184256bbf9eb5dce60524f485989001632`.
Neither record includes the token or original model output.

After the final test, **four original llama-server instances** remained,
both existing guarded models returned HTTP 200, no shadow process was
running, and GTT use remained near the previous 7.2 GiB allocation
baseline. No separate Ling model was allocated.

## What is *still* required before a real Ling replacement

1. Authenticate actual authorized client identities, rather than an
   ephemeral owner-only test token; verify positive and negative
   requests through the final gateway.
2. Preserve the complete OpenAI-compatible API contract, including
   model discovery, error/status mapping, SSE streaming and tools if
   required by real consumers. This first shadow **only** proves two
   synthetic nonstreaming requests.
3. Tie every actual production and failure/failover request to
   authenticated **user-bound** provenance, tamper-evident receipts,
   per-consumer quotas and policy-governed redacted NAS custody.
4. Obtain an authoritative consumer census over meaningful normal
   agent workloads; previous 12s/45s TCP samples with no connections
   are not proof that legacy forwards are unused.
5. Verify a time-bounded reversible cutover including old-port
   compatibility, rollback and resource reservations. Only then
   may operator-approved service changes be considered.

**NO-GO for removing `:1236` forwards, switching clients, routing
Ling fleet-wide, or unloading the resident Ling model.** This
checkpoint closes a bounded local *authenticated Ling feasibility*
gap—not the full production security/deployment gate.
