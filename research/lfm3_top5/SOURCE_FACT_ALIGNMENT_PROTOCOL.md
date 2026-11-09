# Source-fact alignment guard — research only

This is a deliberately **non-deploying**, pure offline consistency check for **synthetic, separately annotated source facts**. It is **not** a way to extract trusted facts from prose, attest source custody, certify the model, or make release decisions.

## Proposed workflow

1. An authorized source-of-truth process (future work; **not present**) exports typed low-level facts (such as `sni_mapping_reviewed=true` and `sni_host_binding_present=false`) separately from model output, with their actual evidence markers and independent reviewer provenance.
2. In this research-only version the adapter accepts solely `origin=synthetic_operator_annotation`, requires explicit review of every E1-E9 marker in the supplied note, enforces a note SHA-256 binding, a small allowlist of strictly Boolean source facts, and nonempty paired facts for positive categories. The `review_status=verified` field is **a local assertion, not a trusted signature**.
3. The model may propose only `category` and `evidence`. The guard independently checks for exactly one category supported by separately supplied typed facts, and verifies that the cited marker supports it. Conflicting, incomplete, ill-typed or malformed inputs produce HOLD. It does not auto-relabel wrong predictions, so false answers remain visible to quality metrics.
4. Two known **unblinded development-only** failure classes (wrong `database_service_down` when E2 establishes missing SNI binding; wrong `node_auth_contract` when E2 establishes an unlinked beta build) are covered by synthetic adversarial tests. This is a **post-hoc regression test**, not held-out accuracy evidence.
5. The same taxonomy also includes node task credential mismatch, missing independent GPU tensor witness, database listener absent after completed probe, and insufficient evidence. Absence of facts does **not** establish healthy production status or exhaustive real-world evidence.

### Immutable safety boundary

All outputs preserve `independent_source_custody_verified=false`, `model_value_verified=false`, and `production_authorized=false`. A `PASS_SYNTHETIC_*` result only means alignment with **caller-authored synthetic annotations**; it must never be presented as a trustworthy root-cause diagnosis or release approval. No live host facts, user secrets, private source receipts, model weights, public ingress, provider settings, production `src/` or `deploy/` files are touched here.

**Independent next gate:** connect a separately verified source-fact pipeline with authenticated provenance, case attribution and reviewer custody. Freeze any such adapter before independently authored held-out narratives are revealed. Keep draft PR #148/#152/#153 and all production gates HOLD.
