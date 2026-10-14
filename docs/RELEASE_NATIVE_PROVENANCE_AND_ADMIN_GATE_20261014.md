# Auto-Router — release admin boundary and native process provenance

**As of 2026-10-14 EDT:** documentation-only, **P0 HOLD**. An unmerged PR and a test receipt cannot be represented as running production authorization.

## Admin authorization and provider truth

[Draft #41](https://github.com/scottjoyner/auto-router/pull/41) guards inherited admin GET/POST handlers and adds a read-only authenticated GET **/admin/providers/effective** endpoint showing in-memory effective state with sensitive fields redacted. The draft reported 45 focused tests passed and two skipped. The previously running router responded 200 to an anonymous admin provider GET, so this source change is **not** proof the live endpoint is protected. Do not issue administrative writes as a documentation test.

Provider YAML, effective in-memory router state, provider discovery, loaded native model, admitted model handle, completed request and delivered response are distinct evidence. Mounted configuration identity alone does not establish the process or model that executed a request.

## Authenticating a human and real native task

[Release P0 #37](https://github.com/scottjoyner/auto-router/issues/37) requires authenticated tenant/subject identity bound to a server-minted root/attempt, authorized model, native provider/process/slot and verified completion. A service bearer token or client-supplied user, request ID, trace ID or IP address is not independent human authentication.

The research Prism source allocates a native task ID **before** slot assignment, and may allocate children for multiple prompts/completions. [Draft #64](https://github.com/scottjoyner/auto-router/pull/64) therefore enforces an original signed single short plain-text chat shape prior to consuming a fixture receipt. This may qualify a candidate request shape but does not prove any real task ran.

[Draft #66](https://github.com/scottjoyner/auto-router/pull/66) defines **verify-only synthetic** two-phase receipts:

- **Phase A:** distinct signing root binds authenticated actor and exact forwarded HTTP method/path/body digest and byte length, original request and the complete native task allocation set; slot ID is necessarily absent.
- **Phase B:** a second signer binds subsequently assigned slot to all Phase-A signed bytes, real task identity, process/boot/model identity and bounded event metadata.
- The original raw HTTP header set must reject duplicate/mixed-case protected auth names, conflicting bearer modes and control characters **before** lossy map normalization. Header shape validation does not prove human identity.

The source-only llama.cpp/Prism instrumentation is compile-time OFF in the experiments. There is no deployed native signer/observer, protected key custody, durable anti-replay ledger, trustworthy full model/build digest or approved two-user execution canary proven by #66. [P0 #65](https://github.com/scottjoyner/auto-router/issues/65) tracks allocation/slot timing; [#61](https://github.com/scottjoyner/auto-router/issues/61) tracks real native physical provenance.

## Distinct release gates

| Gate | Minimum independent evidence | State |
|---|---|---|
| Admin boundary | deployed exact build, authenticated caller compatibility and negative auth review | HOLD |
| AssistX token transport | peer-authenticated transport and coordinated four-service rotation | HOLD |
| Native actor/receipt | real actor-to-process-to-slot-to-model identity and complete terminal/delivery evidence | HOLD |
| Replay and safety | resource-governed monotonic anti-replay, no forged source or sibling claim | HOLD |
| Physical canary | explicit operator approval, safe healthy host and two-human trace evidence | NOT AUTHORIZED |

## Documentation-only verification

Pin PR head, source test revision, deployed router revision and checked CI run separately. Review expected negative tests and public source, but do not infer a live pass from synthetic results. No model inference, autoload, host restart, credential borrowing, unauthorized admin action or NAS write is authorized by this note. Downstream release dependencies remain [AssistX routing #365](https://github.com/scottjoyner/auto-assist/pull/365) and [Kipnerter 1.7 RC0 #395](https://github.com/scottjoyner/kipnerter-ios/pull/395).
## Verified default-source discrepancy (2026-10-14 x1-370)

At the clean `origin/main` parent **747530a9a79b**, `src/auto_router/main.py:551-552` defines `GET /admin/providers` without `Depends(require_admin)`; adjacent `/admin/providers/health` and `/admin/context` are likewise unguarded in these route functions. `src/auto_router/security.py:32` defines `require_admin`, but these inherited endpoints do not call it. `src/auto_router/secure_live.py` wraps `main_live.app` for inference idempotency, stream lifetime and executor inference auth; that is **not** evidence of a global administrative route guard. The source inspected has no `/admin/providers/effective` implementation. Draft #41 changes these semantics, so verify its reviewed head, authorized caller compatibility, and subsequent actual deployed process before documenting the fix as live. No unauthenticated network probe or admin POST was performed for this review.
