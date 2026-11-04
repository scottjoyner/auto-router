# Private Docker Compose cutover preflight: streaming bounded stdout

**November 3, 2026. RESEARCH-ONLY / PRODUCTION HOLD.**
Stacks directly on Auto-Router draft PR #41 exact head
37237a0c481c9ffa33eb3f1cfe6dfe8d505b17ad.
No live router deployment, firewall/credential change, image load, build or
pull is authorized by the added tests.

## Physical release gap and original behavior

The live git-router-1 continues to return anonymous HTTP 200 to
GET /admin/providers, while /admin/admission returns 401. A prior
October 11 hosted PR #41 image build passed, but the run's first-page
GitHub artifact listing is now **empty**. The historical candidate image
SHA256 f538dd649b618fdc25ae4994b07b978f1cb31acd1dad112d676be2fc1dec777b
was verified within the old hosted runner but is NOT a present verified
deployable artifact on the fleet. Its mutable tag is not a rollback plan.

The existing read-only operator Compose structural preflight called
subprocess.run with capture_output=True and checked a four-megabyte ceiling
only after the entire private config had been buffered. A misbehaving
docker compose config process could consume unbounded memory before
the byte check, particularly on a near-swap-exhausted hub.

## Narrow source fix

The bounded Compose config helper launches only the command supplied by
its existing fixed callers. It never runs Compose up, build, pull, stop,
restart or any network-dependent deployment. It consumes output through
selectors and fixed-size reads, enforcing **4 MiB output maximum during
capture**, a 25-second deadline, and up to 4 KiB of synthetic stdin.
Raw stderr is discarded without logging; any bad exit, timeout,
oversized result or decoding failure returns a sanitized HOLD. Child
process groups are terminated and reaped on failure.

The two callers (ordinary pinned-image structural check and synthetic
in-memory image override) preserve their existing strict decision types,
whole Compose service comparison, and confidentiality of all interpolated
environment/secret/config values. No user passwords or raw private Compose
file contents are emitted in this evidence.

## Executed validations

- **29/29 synthetic tests passed**: existing private Compose structure and
  owner-secret redaction cases, exactly 4 MiB output accepted, cap+1 byte
  denied, nine-megabyte flood denied, nonzero process status, deadline,
  inherited-output-pipe grandchild, oversized synthetic input, and safe
  handling of private fields in the result.
- Python compilation and Ruff F/I passed.
- Real operator Compose was inspected on x1-370 with a dummy in-memory
  image SHA256 (not loaded), no build/pull/restart, no sensitive output.
  All eight structural invariants passed:
  PASS_SYNTHETIC_OVERLAY_STRUCTURE_ONLY; actual router remained running.
- Exact-head dedicated hosted source CI is required independently.

## Remaining high-priority blockers

1. Review the client-first authenticated AssistX #288 + proxy guard #538 and
   independent OpenCode bridge #532 changes, with explicit credential-owner
   and source/image approvals.
2. Refresh and independently verify an available, immutable router image
   artifact before considering any cutover. The old workflow artifact is not
   available in the current run listing.
3. Pin a loadable rollback image independently of the mutable latest tag.
4. Re-run all real client compatibility and remote unauthorized 401 + correct
   authenticated 200 acceptance checks from separate nodes before promotion.
5. Complete server Task/machine identity fencing, independent graph writer
   roles, unexpired signed capacity and SGNC custody. All remain HOLD.

**No live infrastructure state was changed.** A synthetic structural PASS
does NOT imply that operator custody, network policy, rollback or runtime
admission gates have passed.
