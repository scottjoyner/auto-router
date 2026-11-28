# Router offline image custody and controlled cutover

**P0 HOLD / NO-GO.** This release workflow creates a verifiable review archive. It does not deploy, rotate credentials, change firewall rules or repair the host.

## Provenance gate

For every main push that changes the release inputs, the hosted workflow:

1. Builds the Docker image from the exact GitHub Actions checkout SHA. Checks its OCI revision label and source-SHA tag.
2. Runs the nonroot, read-only, no-network ASGI smoke, verifying anonymous admin rejection and local JS assets.
3. Saves the actual image as a Docker archive, checks its embedded source label, image config ID, archive SHA-256 and separately retained JSON manifest.
4. On main pushes only, attests the archive through GitHub OIDC/Sigstore, verifies repository, signer workflow, main ref and source digest, and only after all these pass uploads the review archive with seven-day retention.

**Independent acceptance** must pin an expected source SHA and archive SHA-256 from a trusted release record, not solely from the downloaded manifest.

Use a separate trusted review host with the archived tar and manifest:

~~~bash
SOURCE_SHA="<40-hex-trusted-main-commit>"
ARCHIVE_SHA256="<64-hex-independently-recorded-archive-hash>"
ARCHIVE="router-${SOURCE_SHA}.docker.tar"

gh attestation verify "$ARCHIVE" \
  --repo scottjoyner/auto-router \
  --signer-workflow github.com/scottjoyner/auto-router/.github/workflows/router-attested-review.yml \
  --source-ref refs/heads/main \
  --source-digest "$SOURCE_SHA"

python3 tools/router_release_archive.py verify \
  --archive "$ARCHIVE" --manifest release-manifest.json \
  --expected-source-sha "$SOURCE_SHA" \
  --expected-archive-sha256 "$ARCHIVE_SHA256"
~~~

Result means **integrity/provenance PASS; production still HOLD**. The archive SHA-256 is not a registry OCI image digest. GitHub workflow attestation does not prove a two-party release approval or reproducible dependencies. The current Dockerfile still uses a mutable Python base tag and unpinned dependency ranges.

## Physical cutover gates — not automated

- [ ] Authorized offline/consistent root, EFI/GPT and relevant separate SSD image for x1-370, independent SHA-256 and spare-device restore/boot proof. Interrupted glibc/GRUB/AMD repair remains a separate HOLD.
- [ ] Protected scope-separated Router operator credentials and authorized Auto-Assign client credential reconciliation. Never write secrets to git, GitHub artifacts, terminal logs, or unprotected backup material.
- [ ] Explicitly approved source and signed archive; independently verify source, signer and exact digest BEFORE any image load or live process action.
- [ ] Dedicated network-limited, non-workload candidate canary with verified anonymous 401/403 on all mounted admin routes from host IPv4, IPv6, an actual Docker peer, and independently approved external vantage points. Separately prove authorized operator-positive GET and redaction.
- [ ] Client compatibility, model routing, service identity, database readiness, and Task authorization checked without admitting unapproved work.
- [ ] Spare/staging forward-and-rollback exercise including resource limits and signed retained recovery evidence. **Do not roll back into an exposed public old Router.** On candidate failure use a contained, non-public state.
- [ ] Operator-owned explicit final release GO with image ID, attestation, ingress matrix, recovery witness and time. Without all evidence the decision is HOLD.

The deployed Router was confirmed to return anonymous HTTP 200 on three admin GETs, including from a real Docker peer. Source merges, synthetic CI and isolated image smoke tests have **not** resolved this running P0 exposure.

No automated production deploy or cutover is provided in this workflow.
