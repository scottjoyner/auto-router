# Auto-Router documentation changelog

> **Status:** UNRELEASED DRAFT / P0 HOLD. **Scope:** effective-provider and native attestation contract. **Source commit:** current GitHub main 747530a9a79b8d2f27ee85ff4c350d217c86fd6d; historical local 14c5eb223c5780a53e9a5b5c4b47de0139908d6b. **Last verified:** 2026-10-14. **Owner/reader:** router/security release owners. **Evidence links:** [candidate packet](RELEASE_CANDIDATE_EVIDENCE_20261014.md), [central hub](https://github.com/scottjoyner/knowledge/blob/docs/release-evidence-20261014/20-Projects/auto-suite/README.md). **Known gaps:** deployed admin endpoint, authenticated native physical proof.

## Unreleased — 2026-10-14 documentation candidate

- **Open draft only:** #41 inherited admin endpoint protection / effective in-memory provider semantics; #62 physical read-only preflight; #63/#64/#66 verify-only two-phase signed native protocol.
- **Not deployed or attested:** real original actor → allocation → process/slot → token → tenant delivery; green synthetic tests do not satisfy #37/#61/#65.
- **NO-GO:** effective provider/operator custody and physical execution proof.

### Upgrade/rollback

No auth cutover or listener update approved. Preserve prior private endpoint, exact config, replay high-water and tenant isolation; only an operator may stage a future cutover after independent evidence. See [candidate packet](RELEASE_CANDIDATE_EVIDENCE_20261014.md).
