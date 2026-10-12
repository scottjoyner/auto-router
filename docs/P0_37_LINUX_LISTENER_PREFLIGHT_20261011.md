# P0 #61 — Read-only Linux listener-to-process evidence preflight

**October 11, 2026 (EDT)** · **Draft only / no inference authority**

Dependency: [P0 physical native-process witness issue #61](https://github.com/scottjoyner/auto-router/issues/61) and [provider-signature verifier draft PR #60](https://github.com/scottjoyner/auto-router/pull/60), stacked on the shared-owner experiment [PR #51](https://github.com/scottjoyner/auto-router/pull/51).

## Why this slice is necessary

Auto-router and AssistX now have passing hosted proofs for signed, request-bound human **test** identities and a shared PostgreSQL root/attempt authority. The separate provider-witness contract verifies detached Ed25519 event *claims* without claiming those events came from a real model process. To move toward physical proof, first establish whether an observer can independently resolve a TCP listener to a **specific stable OS process** in its actual execution context.

This draft adds **only** `scripts/p0_37_linux_listener_preflight.py`, an isolated Linux standard-library read-only diagnostic. It does not import the live router, attach hooks, sign receipts, alter privileges, inspect credentials, load models, issue inference, hash large GGUF files, or write to the NAS. The default mode makes **no network requests**.

### Read-only workflow

```sh
python3 scripts/p0_37_linux_listener_preflight.py --json
# Optional single loopback-only, bounded LM Studio native GET:
python3 scripts/p0_37_linux_listener_preflight.py --native-inventory --json
```

There is no SSH or configurable remote target. The script only opens:

- `/proc/net/tcp`, `/proc/net/tcp6` and `/proc/sys/kernel/random/boot_id`;
- up to 2,500 visible numeric `/proc/<pid>/fd` directories, bounded to at most 256 FDs per PID and 4 seconds of scanning;
- `/proc/<pid>/stat` for **candidate** listener owners, measuring PID and Linux process-start tick identity twice; and
- **only with `--native-inventory`**, one `GET http://127.0.0.1:1234/api/v1/models` (2s timeout, max 256 KiB body, no redirects or POST). The inventory JSON must be unambiguous and every model must supply valid `loaded_instances`; it returns **counts only**, never credentials or model responses.

It matches the Linux TCP LISTEN socket inode against FD symlink targets `socket:[inode]`; if a candidate is found, it rechecks the PID start ticks, the FD association and the listening socket. A vanished process, changed PID, recycled socket inode, stale/malformed table, insufficient permissions or exceeded scanning budget **does not receive affirmative attribution**.

**Crucial trust boundary:** even when a PID is stably associated with the listener, that is only a *socket ownership candidate*. The tool does not prove the process has loaded the selected model artifact, received a specific signed attempt, emitted a native token or delivered a response. It does not possess a provider-observer signing key. Every response hardcodes:

```json
{
  "native_per_attempt_request_observed": false,
  "model_artifact_sha256_measured": false,
  "observer_signature_verified": false,
  "provider_execution_attested": false,
  "client_delivery_proven": false,
  "physical_inference_permitted": false,
  "operator_approved": false
}
```

## Fresh Fleet Commander x1-370 observations

At approximately 2026-10-12 01:10 UTC (October 11 EDT):

- LM Studio **TCP port 1234 has one LISTEN socket** from kernel procfs.
- **26 models in native /api/v1/models, 0 native resident models and 0 loaded instances**. A catalog entry or listening API is not a running model.
- In Fleet Commander's isolated process namespace/security context, **1,017 PIDs scanned; 956 inaccessible FD directories; one process exceeded the FD scan budget**. The tool returned **`scan_state=partial_unattributed`**, **zero attributable socket-owning PIDs**, with explicit permission/budget unknown reasons. This is not proof the socket has no owner. The earlier ad-hoc scan likewise had substantial `/proc/<pid>/fd` access limits.
- Independently, the existing *read-only* `p0_37_host_pressure_preflight.py --local --expect-host x1-370` assessed **`HEADROOM_ONLY`**, with approximately **198.26 GiB** root space available, **77.41%** root use, `io.full.avg10` approximately **0.07%**. This checks host headroom only; its own result still contains `physical_inference_permitted=false`, `operator_approved=false`.
- Therefore x1-370 has capacity headroom under that diagnostic, **but no resident-model or independently attributable per-request process** in these snapshots. No inference canary is approved.

**Beelink remains physical NO-GO** based on separate storage-capacity and I/O constraints, despite one native resident-model instance in the prior bounded inventory. Do not switch to Beelink simply to obtain a positive loaded-model count.

## Tests and remaining physical gates

`tests/test_p0_37_linux_listener_preflight.py` exercises isolated synthetic `/proc` fixtures. It tests the exact Linux start-time field (22), process names containing parentheses, TCP socket inode matching, disappearing or PID-reused processes, partial FD visibility, exhaustion of PID/FD budgets, malformed or oversized kernel records, no-network default, bounded single-loopback GET, duplicate or fake model inventory and strict unknown semantics. These tests can run without a model or a database.

Reaching genuine provider execution attestation still requires *separately approved* least-privilege node observation with kernel/process visibility, independent pinned signing-key custody, exact model artifact and native resident-instance verification, independently correlated real provider **attempt arrival and terminal evidence**, external authority/failover fencing, encrypted owner-scoped trace custody, real independently authenticated humans and physical-client readback. A read-only preflight result, even `scan_state=complete`, never opens those gates.

No production configuration, gateway signer, model, NAS data, port, permissions or identities were modified. This branch and its ancestors remain **DRAFT, unmerged, disabled/uninstalled**.
