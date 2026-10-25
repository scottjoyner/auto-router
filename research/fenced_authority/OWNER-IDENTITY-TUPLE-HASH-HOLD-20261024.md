# Signed broker owner identity hash ambiguity — research safety correction

**Status:** DRAFT / HOLD / NOT a fleet quota issuer or production migration.
Stacked above [Auto-Router draft #75](https://github.com/scottjoyner/auto-router/pull/75);
related [knowledge #57](https://github.com/scottjoyner/knowledge/issues/57).

## Reproduced ambiguity

The earlier signed broker proposal encoded an owner as

`owner_issuer + "\\x00" + owner_subject` and hashed that concatenated string.

Both inputs are individually signed but the signed-field validator permits
an embedded NUL. These two **different** valid identities collide *before*
SHA256 (the hash function is not broken):

- `("idp\\x00alice", "bob")`
- `("idp", "alice\\x00bob")`

The old string is `"idp\\x00alice\\x00bob"` in either case, so a
downstream broker treating that owner hash as an exact account principal
could confuse the owners.

## Proposed strictly research-only fix

Construct the owner hash from the canonical JSON representation of three
separate values:

`SHA256(canonical(["owner-issuer-subject-v2", owner_issuer, owner_subject]))`

This is unambiguous even with embedded control characters; the new domain
name explicitly identifies an incompatible encoding revision. It does NOT
alter the Ed25519 issuer signature verifier, signed resource witness, scope,
task, claim, JTI, model, account, attempt or request validation. The
research module still produces `provider_calls=0`, `sql_executed=false`,
`broker_admission_authorized=false` and
`provider_execution_authorized=false`.

## Tests and remaining limits

Two synthetic independent owner JWTs with corresponding properly signed
resource witnesses now return different owner hashes; a mismatched witness
continues to deny even when the old concatenated string would be equal.
All original denial/replay controls remain. Exact published research-only
source/fixtures passed 51 pytest tests on **both x1-370 and Xwing** in
auto-cleaned temporary directories. No production credentials, provider
network, database services, model execution or NAS access were required.

**Migration caution:** `owner-v2` is deliberately NOT digest-compatible
with existing `owner-v1` research fingerprints, even for normal names.
Do not mix those hashes with an existing ledger or dual-accept them without
an explicitly reviewed source-owner migration and atomic revocation fence.
This document offers no automatic migration or deployment.

The larger blockers remain: a signed short-lived resource witness can be
replayed after revocation within its TTL; no separately administered,
rollback-resistant high-water, trusted signed Task/owner issuer in production,
physical effect-time fence, model-side cancellation, authenticated shared
free-provider account spend, or independently signed archival traces exist.

Keep #75 and the stacked fix DRAFT, knowledge #57 OPEN, and fleet
autonomous/shared-provider inference NO-GO.
