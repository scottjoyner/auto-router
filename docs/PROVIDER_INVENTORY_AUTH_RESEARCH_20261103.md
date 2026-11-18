# Auto-Router provider-inventory admin authentication — research proof

Date: 2026-11-03. Scope: one admin read endpoint. **No production deployment.**

## Observed gap

The existing `GET /admin/providers` in `src/auto_router/main.py`
returned provider inventories without the already available
`require_admin` dependency. A local anonymous HTTP check of the
running x1-370 router answered HTTP 200. Nearby
`GET /admin/admission` already used `require_admin` and answered
HTTP 401 anonymously. The high-level design requires authentication
on admin surfaces. We did not retrieve or disclose provider responses
or credentials during this review.

## Narrow research patch

Add FastAPI `Depends(require_admin)` to the inventory route, reusing
the existing shared auth mechanism and fail-closed behavior for missing
configuration. No custom token parser and no change to authentication
policy. Update only the integration router-client fixture to forward an
operator-supplied `AUTO_ROUTER_ADMIN_TOKEN` as a request header.
No token is embedded in source or logs. If that variable is absent,
the protected endpoint correctly denies the integration test.

Isolated synthetic TestClient tests explicitly skip app lifespan,
so neither provider enumeration nor service initialization occurs.
The fixture substitutes a fake provider registry and an unrelated
test-only token, then confirms anonymous, wrong and missing
tokens are denied; valid header, Basic and Bearer credentials are
accepted. This does not prove TLS, reverse-proxy, network ACL, or
production service deployment correctness.

## Safe release gates

1. Review the diff and verify exact source/CI status before merge.
2. Test in an isolated runtime with no real providers or secrets.
3. Run authenticated/unauthenticated ingress probes through the
   actual proposed reverse proxy, including alternate URL aliases
   and old runtime entrypoints.
4. Identify clients that formerly relied on anonymous inventory;
   migrate them to authenticated calls before any cutover.
5. Require operator approval, backup, reversible rollout,
   and post-cutover negative tests before production changes.

The repository worktree is local and research-only, not pushed,
merged, deployed or activated. Existing fleet routing remains unchanged.
