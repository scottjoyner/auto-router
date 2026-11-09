# Public LFM3 prospective contract — actual FreeTSA RFC 3161 receipt

**Scope: PUBLIC frozen source contract ONLY. Not an independently sealed corpus, original-model-run evidence, externally authenticated TSA, or release approval.**

## What was actually sent and signed

On **November 9, 2026 at 14:34:22 UTC (09:34:22 EST)**, FreeTSA returned a signed RFC 3161 response via its documented HTTPS `/tsr` endpoint to a request containing the SHA-256 of **839 bytes of public canonical JSON**. The service returned HTTP 200, and the token reported `Status: Granted`. The frozen public JSON identifies the exact source head, source tree, scorer, deterministic rule, preregistration and **expected** LFM3 checkpoint; it explicitly has `case_dataset_sha256: null` and all independent custody, author, reviewer, value and production claims false. Its digest, not private cases or credentials, was submitted. A local git commit froze the public manifest **before** the request; the local prereg proof archive is restricted, clean and has zero remotes.

The original, DER-encoded request, DER-encoded signed response, published public CA/leaf certificates and canonical public manifest are preserved as base64 fields in `public_contract_anchor/receipt.json`. This is only public-source material and contains no model outputs, real incidents, host secrets or case oracles. No second TSA request is needed to reproduce verification.

| Evidence item | SHA-256 |
| --- | --- |
| Canonical public contract JSON | `1f9a6bd4129a104e8e1846ed1a4c3727fc0f4834cb58353f877801dd833803a2` |
| Original RFC 3161 request DER | `02f86836879cff924fe01362126c9713f3c8021744e4a9bd4507e1ffc14b3d96` |
| Actual externally returned RFC 3161 response DER | `bfef99efe2b7afc3d32f7a9f8d664a65baf1ac0ae8561c0a2b181bddcca696cd` |
| FreeTSA self-published CA PEM fingerprint | `2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438` |
| FreeTSA self-published current TSA PEM fingerprint | `8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467` |
| FreeTSA root-CA signed CRL snapshot (public PEM) | `13d85acc0dd6a7ace445822b09dcba0de4236e84d60494dcbe6d7a6c97031935` |
| Frozen prospective preregistration JSON | `38aeb6eba605cfff44f6d46e15b0afe23b9fbe0b405561ae7848e1124d645696` |

Issuer documentation and published fingerprints: <https://freetsa.org/index_en.php>. Specification: <https://www.rfc-editor.org/rfc/rfc3161>. The vendor's advertised CA fingerprint **matched byte-for-byte** the PEM retrieved over HTTPS from `https://freetsa.org/files/cacert.pem`; the same held for its TSA leaf certificate at `https://freetsa.org/files/tsa.crt`. The provider independently returned the signed timestamp token, but a fingerprint on the provider's own webpage is not equivalent to an independently established trust anchor or checked CRL/OCSP/issuer policy.

## Additional real signed provider CRL snapshot

An issuer-supplied public root CRL was retrieved over HTTPS from `https://www.freetsa.org/crl/root_ca.crl` after the timestamp request. The 1,072-byte snapshot is embedded, unmodified, in the same public receipt as Base64, **in addition to the original signed token** (which is unchanged from receipt v1 in Git history). OpenSSL `crl -CAfile ... -verify` returned `verify OK`, and `verify -crl_check -attime <signed-token-UTC> -CAfile ... -CRLfile ...` returned `tsa.crt: OK`. Its `thisUpdate` is September 18, 2026 10:30:55 UTC and `nextUpdate` is September 18, 2027 10:30:55 UTC. The TSA leaf serial `C2E986160DA8E9CD` is not listed; the CRL states "No Revoked Certificates."

These are **real issuer-supplied revocation facts at the token's claimed time**, not independently trusted issuer identity. The same provider controls the root, CRL and documentation page, and there is no independent audit of CA key custody, time accuracy, issuer policy or live OCSP. The deterministic offline CLI now also checks the CRL's signature, pinned digest, signed dates, validity window, leaf serial and the historical revocation result. An old CRL does not certify current revocation status on an arbitrary later date.

## Reproduce offline without contacting fleet or TSA

From the PR worktree with Python and OpenSSL installed:

```bash
PYTHONPATH=. python3 -m scripts.lfm3_public_contract_timestamp_verify \
  --receipt research/lfm3_top5/public_contract_anchor/receipt.json
```

Exit **0** means `PASS_PROVIDER_PUBLIC_CONTRACT_TIMESTAMP_CRYPTO_ONLY`: the pinned public bytes, original SHA256 query imprint and nonce, signed response, signer chain against the supplied provider-published CA, token's embedded timestamp and a provider-signed, time-scoped CRL snapshot all match. Exit **2** means HOLD (invalid/missing evidence, altered/oversized data, model/release authority spoof, or unsupported scope). It prints a redacted JSON summary, never raw corpus or private key data. The verifier does **not** contact FreeTSA or access production, and the existing pure research package retains its no-`subprocess` contract.

## Why release and independent-corpus gates remain HOLD

1. The **public frozen research contract** was timestamped, not any future independent narrative dataset. The author, gold reviewer and witness have not enrolled, and a valid timestamp for public code cannot demonstrate unseen-case provenance or oracle independence.
2. This token was issued **after** earlier developer-authored LFM3 runs. It cannot retroactively make the 36-case study independently blinded, nor does it prove a timestamp occurred before any previously completed inference.
3. FreeTSA's signer and its CRL verify against FreeTSA's advertised root; that is meaningful provider-origin cryptographic and issuer-revocation evidence, but **no independently approved issuer authentication, independent root trust, live OCSP/current-status check, TSA policy review or independently witnessed timestamp chronology** was performed. A separate external reviewer must validate those items.
4. This actual public-contract token is not bound to a signed independent-case hash or to author/reviewer/witness signatures under #156. Before any future **new** independent held-out trial, its *new case dataset* must independently obtain and verify its own pre-inference RFC 3161 token and three-role signatures, using the separate optional `scripts/lfm3_rfc3161_anchor_verify.py` flow. Never reuse this public-contract receipt as that seal.
5. **No automatic model source admission or release:** `provider_crl_independently_trusted=false`, `provider_identity_independently_authenticated=false`, `independent_case_dataset_sealed=false`, `pre_inference_case_custody_verified=false`, `earlier_development_runs_retroactively_validated=false`, `model_value_verified=false` and `production_authorized=false` in every output. No production, credential, provider, model-weight, or gateway change is authorized.

**Operating decision:** Provider-issued public preregistration contract timestamp cryptography **PASS**; independently sealed held-out cases, provenance, independent source/value proof and production **HOLD**. External dependency tracked under issue #157.
