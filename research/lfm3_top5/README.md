# LFM3 research-only five-lane integration

This directory is an isolated, non-package, non-production integration boundary
for the five LFM3 research contracts: local stage response, free endpoint
subagent receipts, SensorGraft context, fleet performance, and evaluation.

It is NOT imported by auto_router runtime, provider discovery, or signed
AssistX admission. The setuptools configuration packages only src/ and
does not distribute this research/ directory.

bridge.build_lfm3_research_advisory accepts synthetic or independently
pre-collected evidence and returns a purely advisory status. It cannot make a
provider eligible, authenticate a signature, change a route, execute a model,
write a training weight, or authorize public ingress. A synthetic
RESEARCH_CANDIDATE status means its five contracts agreed on the supplied
synthetic inputs, not that any production-quality evidence exists.

The research implementation snapshots are copied from the standalone local
Fleet Commander LFM3 lab after independent test acceptance; they may be
reviewed as vendor snapshots before any upstream reuse. Changes to live
Auto-Router source, provider configuration, deployments and TestFlight are out
of scope. No private source is sent to hosted free model endpoints.

Local smoke:

    PYTHONPATH=. python3 -m pytest -q tests/test_lfm3_research_shadow.py

The optional GitHub workflow runs only these tests; it never uses production
credentials or network inference. Do not represent local pass as hosted CI.

## November 7 evidence hardening

The five component files are byte-identical to the frozen LFM3 lab at commit
c180b21, as checked by VENDOR_PROVENANCE.json and the integration tests.

The integration now requires model-pinned explicit stop and consistent total
tokens; real positive zero-cost session receipts on both agents; a fail-closed
64-record memory batch cap enforced before invoking the trusted verifier;
unique benchmark observation IDs; and an approved external witness metadata
binding to the exact candidate and baseline run/checkpoint and workload IDs.

The checks are *research interface contracts only*. Neither external cryptographic
signature verification nor independent provider billing or measurement sampling
is established by these synthetic inputs. Production admission remains HOLD.

13 isolated synthetic integration tests passed locally. Hosted exact-head CI is
a separate confirmation and must not be claimed until it executes.

## Signed evaluation attestation (research protocol v1)

The research advisory now requires an Ed25519-verified signed claim as well as
its previous bound-witness metadata. No unsigned/bare-digest approval passes.
Signature verification uses the caller-pinned Ed25519 public key, exact complete
candidate and frozen baseline JSON SHA-256s, domain separation, and an explicit
externally supplied minimum epoch. Test private keys are generated in memory,
never used for production. The local cryptographic verifier has no signer API.

This research protocol is NOT native SensorGraft/SGNC proof and is NOT evidence
of external trusted key custody or anti-rollback durability. Production routing
and release remain HOLD. The scoped workflow installs only pytest and
cryptography on a hosted runner; it does not contact external inference APIs.
