# Joyner Ling signed, replay-resistant SSE witness — October 24, 2026

**Classification:** research-only; NO-GO for fleet routing, persistent listener, legacy forward removal or model loading.

## Physical result

Joined the existing Ed25519 signed Ling receipt verifier and one-use SQLite JTI reservation with the existing ASGI SSE transport and the resident Ling server, without installing any server or model. A disposable script, scripts/joyner_ling_signed_stream_witness.py, synthesizes HTTP ASGI calls with a fresh, memory-only Ed25519 test key and synthetic caller identity and projection. It delegates to the real local Ling server 127.0.0.1:1236 via a bounded streaming reader, bounded queue, and no external listener.

Exact model preflight: unique scott-owned server, loopback-only port 1236, alias joyner-ling and full GGUF SHA-256 f661876a860cc7093583a35a8187e66696c73a2cd512ece8117477a05ceda115 (3,985,017,952 bytes).

| Synthetic attempt | Gateway response | Ling backend invoked | Auth verdict |
| --- | --- | --- | --- |
| First Ed25519 signed, one-use request | HTTP 200, SSE final DONE marker, 5,598 bytes | Once | pass |
| Reuse of identical signed token and body | HTTP 401 | Never | denied |
| No bearer credential | HTTP 401 | Never | denied |

Full SSE content is hashed and discarded, never stored. The script requires model provenance, private NAS-first audit custody before each request, bounded stream bytes, and ephemeral local-only trust. No prompt, model output, token, signing key or HTTP authorization header is stored. Exactly three attempts were audited.

## Evidence and correction

The first physical run delivered 5,605 bytes through 22 ASGI chunks with the expected terminal SSE DONE marker, but its audit annotation incorrectly treated the presence of a replayed bearer as authorization. That immutable historical receipt remains at /nas/fileserver/fleet-reports/trace-custody/joyner-ling-auth-shadow-20261024T092247Z-e03f6ccd1c60.json (SHA-256 e429bb70cdf18099f3329e6717f748e8b440987a5148a5f6d60ba152cfb4cf63).

The corrected runner starts every request provisionally denied and changes the audit result only if Ed25519 verification and SQLite one-use reservation succeed. It resets per-request backend byte counts; a replay cannot inherit the previous request's upstream traffic. The final corrected receipt is:

/nas/fileserver/fleet-reports/trace-custody/joyner-ling-auth-shadow-20261024T092527Z-96ec1afe2896.json

SHA-256 14b03ca869082d6de6728dabf920d42e51882c6c351a0b33cf0e1cda81722134 (4,569 bytes). Independently verified from both Joyner and x1-370. Contains exact synthetic principal/generation and a SHA-256 of the accepted JTI, but no raw JTI or bearer.

Exact final script SHA-256 (identical on Joyner and x1-370): e422a4a720ac7bf1c471816f8a9f4b994c9dea2d15ba97aa339602462179173c. The underlying signed receipt and ASGI transport source modules were staged unmodified in a research-only package directory.

## Safety and remaining gates

This is a live-model transport experiment, not a production caller authentication proof. The signing key and SQLite store are ephemeral, not fleet trust roots or durable user-bound receipts. Full remote-client streaming, backpressure, disconnect, errors, quotas, revocation, credential custody, and authoritative consumer ownership remain unverified. The old socat LAN/Tailnet forwards still exist and must not be removed until a reversible, tested migration is qualified.

After the experiment, all four original inference servers remained healthy, no benchmark process was present, and R9700 GTT usage remained about 7.21 GiB. Ornith/Flint new-load admission remains NO-GO. No service, driver, router, firewall, model residency or production gateway was changed.
