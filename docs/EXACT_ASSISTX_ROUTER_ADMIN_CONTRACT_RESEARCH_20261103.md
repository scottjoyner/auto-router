# Exact cross-repo Admin Router ↔ AssistX catalog compatibility

DRAFT / SYNTHETIC RESEARCH ONLY / NO DEPLOYMENT OR TASK ADMISSION.

This source-only contract tests the ACTUAL Auto-Router PR #123 ASGI handler
against the ACTUAL AssistX PR #288 provider inventory consumer module,
not lookalike or separately authored JSON fixtures.

Pinned sources:
- Auto-Router parent: 28e5af9e2df55ec3cd0974727dae3c25248aeac4
- AssistX consumer PR #288: de805215fa8fdf801fe8bfea42e0dee24fa70e33
- AssistX consumer module SHA256:
  b822fe337770dafc17ce04145627bdc6490f2876172af00fe7808f179e3c02c2

The test uses a synthetic, redacted provider registry and TestClient against
the ACTUAL in-memory Auto-Router ASGI handlers. It intentionally never enters
the ASGI lifespan, which could initialize providers/background processing
or overwrite the synthetic registry. No sockets, model probes, loads or Task
requests occur. It asserts unauthenticated 401, wrong-token 401 and correct
synthetic-token 200; response sanitization; real AssistX parsing and precise
model case; no 404 fallback when the effective route exists; strict no
fallback on wrong token; and exactly one authenticated old-router legacy
GET only on a deliberately simulated effective-route 404.

The dedicated GitHub workflow checks out both exact source heads, rejects
a consumer file hash mismatch, and runs the test with read-only permissions.
No network credential is supplied to test code, and hosted CI only accesses
version-controlled test source, not production endpoints.

HOLD boundaries: This does not prove deployed image parity, real Tailnet
exposure closure, safe deployment of credential-bearing clients, actual
provider availability, loaded model identity, signed Task capacity, any
Graph/SGNC custody or approved Task dispatch. The present deployed
anonymous /admin/providers endpoint still answers HTTP 200.

Operator-controlled release: stage AssistX #288 and the separate bridge
caller fix #532 before router #41 and optional #123. Verify real caller
compatibility, pin all images, independently prove remote anonymous 401 and
authorized effective GET 200 with secrets redacted, and preserve rollback.
Do not merge/deploy or turn on optional admin route registrars automatically.
