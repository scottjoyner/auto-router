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


## Production-image shadow gate — November 3 evening

A disposable Docker canary used the exact immutable CURRENT production router
image ID sha256:182d3d3c8aa6b8afbc45bb5094493f74baa58dbb5c0b0b54555b5718f03c1dd8,
with the staged router source mounted read-only, no network, no live secrets,
no host/production config mounts, read-only root, 512 MiB memory limit,
one CPU, cap-drop ALL, no new privileges and FastAPI lifespan disabled.
Actual runtime dependency versions: FastAPI 0.142.3, Starlette 1.7.0,
httpx 0.28.1, Pydantic 2.13.5.

Unlike the initial main-app-only check (14 admin routes), the corrected
test imports the real main_live entrypoint and confirms that all 20 mounted
/admin/ routes carry shared require_admin. Sample anonymous and wrong-token
GETs return 401, and synthetic authorized effective-snapshot GET returns 200
without leaking URL/userinfo/header secrets or representing model residency.
No dispatch, provider probes, routes, processes or production state changed.

This is **source and library compatibility only**, not deployed image proof:
current live anonymous GET /admin/providers still returns HTTP 200 from
x1-370 and Xwing. An unchanged runtime gate returns HOLD/exit 1.


## Current-main consumer validation, November 4, 2026

The previous dedicated workflow pinned AssistX draft PR #288 (nonmergeable) and tested only its pure inventory parser. This updated exact-head contract checks out **current-main-based, mergeable [AssistX PR #546](https://github.com/scottjoyner/auto-assist/pull/546), pinned commit 3c35e220c3a05f2e94d6e1b0733b9bae87bd65ef**. The parser remains byte-for-byte identical (SHA-256 b822fe337770dafc17ce04145627bdc6490f2876172af00fe7808f179e3c02c2), and the real patched Control Room source SHA-256 is bee6ed102474d3544e90c460eb6dfe3e9cbd6d3b8e6824877b0faa4b84becd77.

Additional synthetic tests import the exact Control Room source without executing AssistX application's package initializer, stubbing only unrelated ancillary modules. Its real provider-inventory callsite sends authenticated `GET /admin/providers/effective` to the Router FastAPI ASGI handler in-process, accepts the sanitized provider IDs, refuses invalid admin credentials without legacy downgrade, and permits authenticated fallback to the legacy route ONLY for a deliberate effective-route HTTP 404. It does not launch Router lifespan, use sockets, read production config, forward secrets, claim Tasks, load models or alter routes.

These tests are additional integration evidence, **not production release approval**: live Router anonymous /admin/providers still returns HTTP200, and fresh signed work capacity is absent.
