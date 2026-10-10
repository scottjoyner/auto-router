# Effective in-memory provider registry and inherited admin endpoint security

**2026-10-10 — source and read-only evidence; NOT deployed.**

## Background

Fleet-hardware PR #28 verified that the mounted Auto-Router provider configuration contains stale Destroyer entries: enabled K2 36B at port 1235, although a live probe found resident k2-1b at that port, and alternate Ornith/legacy defaults pointing to an absent port 1234. That is a real **configuration conflict**, not sufficient proof that the router admitted or executed a wrong model.

The running git-router-1 container actually starts auto_router.secure_live:app. This strict runtime accepts runtime-projection updates rather than treating mounted YAML as immutable execution authority. A bounded, read-only request to its currently running /admin/providers handler showed **one enabled x1 provider** and **zero Destroyer providers** at the capture instant. The mounted config file checksum nevertheless matches the previously reported conflicting YAML. These facts can coexist.

Sanitized witness: docs/evidence/destroyer-effective-provider-20261010.json. It contains only a provider name/node ID, hostname, port, model aliases, SHA-256 source identities and refusal flags. The original admin response was only processed in memory; token/config/header contents were never copied into the artifact. The witness is a point-in-time observation and does not prove historical routing or future admission.

## Security issue and code change

While collecting this evidence, unauthenticated GET /admin/providers returned HTTP 200. The handler serialized full ProviderConfig objects that may contain arbitrary provider headers. The observed response did NOT contain populated sensitive header fields, so no disclosure of a live secret is claimed. Source review also found inherited administrative GET/POST handlers lacking the already existing require_admin dependency. No unauthorized admin POST was performed.

- Add require_admin to all inherited main.py admin read and mutation routes that lacked it. A missing or incorrect admin token returns HTTP 401 before handler execution. This code is **not applied to the running service** and its rollout needs current consumer verification.
- Add GET /admin/providers/effective as a guarded, deterministic and strictly sanitized snapshot of **the actual ASGI state.providers list**. The source includes enabled flags, resolved hostname/port, expected configured model aliases and an optional projection claim/status; it strips URL userinfo and query data, raw model filesystem paths, Authorization headers and provider API-key environment variable names.
- Every output explicitly says resident-model identity and endpoint availability are NOT verified, and it **grants no routing, device control, write or scheduling authority**.
- The effective endpoint never reloads provider YAML, contacts a model, refreshes discovery, acquires a lease, calls health checks or changes state.

## Evidence distinctions for Auto-Router issue #39

1. FILE PRESENT: deployed provider config checksum matches PR #28 source but may not govern active runtime.
2. ROUTER MEMORY: a runtime HTTP response captured at a specific time contained one x1 provider and no Destroyer provider. It does not prove which past requests were routed.
3. SIGNED PROJECTION: separately attest the active generation, expiry, key and checksum using the existing authenticated admission/projection surfaces. A fresh projection claim alone does not prove an actually loaded model.
4. PHYSICAL MODEL: real /health + /props from Destroyer show k2-1b :1235 and destroyer-ornith :1236, not a 36B endpoint. This is read-only service evidence, not routing activity.
5. ACTUAL EXECUTION: a completed request needs a claim-bound trace and provider/model receipt. None was collected in this effort; never infer a misrouted 36B request.

## Acceptance and rollout

Before promoting this patch, inventory legitimate consumers of the inherited legacy admin routes and migrate them to scoped administrator authentication. Test remote unauthenticated rejection for GET and POST without executing a mutating test operation; verify authorized operators still obtain the sanitized effective-memory snapshot. Protect credentials in approved secret stores. Do not log tokens or private source config.

No Auto-Router container reload/restart, admin POST, model inference, Govee switch, NAS scan/transfer, route promotion, or Destroyer binding modification was performed. The separate Destroyer raw endpoint and iPhone security acceptance in fleet-hardware issues #15/#19 remains OPEN. Auto-Router issue #40 tracks deployment of the admin-auth fix.
