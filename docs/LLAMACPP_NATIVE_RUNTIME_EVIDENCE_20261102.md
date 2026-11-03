# Native llama.cpp runtime evidence — opt-in research gate

This draft supplements the **LM Studio** native-inventory discovery in
[Auto-Router PR #110](https://github.com/scottjoyner/auto-router/pull/110).
It adds an **independent read-only `llama.cpp` evidence probe**; there are
no provider registry changes, production router changes, model loads,
service restarts, GPU lease changes, or task dispatches.

## Why this matters for useful inference

The existing Auto-Router discovery counts an OpenAI `/v1/models` list only
as a catalog when LM Studio's `/api/v1/models` is unavailable. That is safe
against false promotion, but it leaves existing **llama.cpp** servers on
Joyner, Deathstar, Destroyer, Lenovo and OptiPlex without positive native
residency evidence. Separate local models can already produce engineering
reviews, embeddings and other research; no additional benchmark is required
for the native source-correlation proof.

## Read-only probe contract

`scripts/probe_llamacpp_residency.py --url http://127.0.0.1:1236`
requests only GET `/props` and GET `/v1/models` with a total JSON size cap,
no inherited proxies, no redirects, and no credentials. The URL must be
`localhost` or a numeric loopback/private/Tailnet address with an explicit
port. Public DNS, URL credentials, queries and unexpected paths are rejected
*before* sending requests.

A **runtime-residency candidate** requires all of the following in the same
bounded probe: HTTP 200 objects from both endpoints; nonempty native
`model_path` (presence only, **never printed**); nonblank native
`model_alias` agreeing **exactly** with one and only one `/v1/models` ID;
`is_sleeping` explicitly false; and a positive bounded integer `total_slots`.
An optional user-supplied expected model name is checked with strict equality
as a separate field. The alias is returned only as a SHA-256 digest, not a
potentially private path or filename. Responses with an HTTP-200 `error`
object, ambiguous IDs, missing path, zero/invalid slots, sleeping model,
redirection or unavailable endpoint fail closed.

The result explicitly states:

- `native_runtime_residency_candidate`: same-runtime model observation only
- `chat_capability_attested`: **false** (embedding and other roles require a separate task-type check)
- `weight_artifact_attested`: **false** (server self-report is not an independent artifact hash)
- `signed_runtime_admission`: **NOT_CHECKED**
- `authorizes_dispatch`: **false** regardless of response or CLI exit code

No automatic mapping between observed aliases and configured model names is
permitted. Server-reported four slots do not equal four approved concurrent
Task leases. The canonical AssistX work-supply gate continues to hold on an
expired signed projection, and the five legacy worker units still need valid
runtime credentials plus deployment/claim-fencing review.

## Physical evidence, November 2, 2026

A read-only SSH-local census observed **13** `/props` + `/v1/models` exact
matches across **six** Linux machines. A second run of the exact new module
from x1-370 over Tailnet confirmed **11/13** endpoints reachable and
correlated: Joyner 2, Deathstar 2, Destroyer 2, Beelink 1, Lenovo 2,
OptiPlex 2. Lenovo's additional :1237 and OptiPlex's additional :1237
answered on SSH-local loopback, but were unavailable over their Tailnet IPs.
They are *not* reachable Auto-Router lanes at this time. The script did not
issue model prompts, release leases, change firewalls or route Task work.

## Smallest next admission steps (not authorized by this PR)

Cross-check each runtime-reported model ID against the configuration and
independent weight/artifact identity, establish chat vs embedding capability,
verify host/resource ownership and exact current listener, obtain signed
runtime access-path evidence, and prove one properly claimed Task and
independent completion receipt. Only then may operators evaluate opt-in
per-lane configuration changes and bounded concurrency. Do not extrapolate
reported llama.cpp slots into fleet productive token generation.
