# Auto-router P0 #37 — Beelink headroom NO_GO (2026-10-11)

**Scope:** Read-only evaluation of an *inventory-only* physical canary candidate. Do not start inference on Beelink under these observations. Issue: [#37](https://github.com/scottjoyner/auto-router/issues/37). Draft: [#45](https://github.com/scottjoyner/auto-router/pull/45).

## Discovery and direct host verification

The opt-in service-level GET-only provider preflight reported **14 LM Studio services / 10 physical nodes**. Beelink's LM Studio native model inventory reported **one resident instance** for `liquid/lfm2.5-1.2b`; two consecutive native GET samples returned the same loaded-instance ID. This is *resident inventory evidence only*, not model inference, process-execution attestation, proof of request ownership or provider response delivery.

A separate **read-only, strict-host-key-checked SSH inspection of Beelink** (host reported `BEELINK-RYZEN-7-MINI-PC`) found:

| Observation | Measured |
| --- | --- |
| Root filesystem `/dev/nvme0n1p2` | 501,809,635,328 bytes total; 3,221,737,472 bytes available to unprivileged users; `df` 100% |
| `/nas` filesystem `/dev/sdd2` | ~81 TiB total; ~41 TiB used; ~40 TiB available |
| Root inode usage | 4% (capacity blockage is blocks, not inode exhaustion) |
| RAM available | ~9.7 GiB (of 13 GiB total) |
| Swap in use | ~3.8 GiB |
| Load averages | ~4.10, 4.38, 4.24 |
| Linux I/O PSI | `full avg10` 17.76% at the first sample; new preflight later observed 23.64% |
| Processes with notable activity | uvicorn, Samba, rsync, and file-scanning processes |

**No workload was stopped, no filesystem cleanup was attempted, and no inference was sent.** A busy, storage-critical NAS node must not be selected for a synthetic model run simply because it has a loaded model.

## New fail-closed, read-only headroom preflight

Standalone diagnostic: `scripts/p0_37_host_pressure_preflight.py`

Offline tests: `tests/test_p0_37_host_pressure_preflight.py`

This script performs one explicitly requested SSH invocation using `BatchMode=yes`, `StrictHostKeyChecking=yes` and a bounded connection timeout, to execute only a fixed standard-library Python reader for `statvfs`, `/proc/pressure/io`, `/proc/meminfo`, `os.getloadavg`, `os.cpu_count` and `os.path.ismount`. No remote writes, model requests, autoload or routing changes.

It rejects an incorrect host name, missing/stale/future measurements, inaccessible pressure metrics and missing expected NAS mount. It can return `NO_GO`, `UNKNOWN` or `HEADROOM_ONLY`. **None of these states authorizes inference**—even `HEADROOM_ONLY` has `physical_inference_permitted=false`. The thresholds below are deliberately conservative *experimental* headroom bounds, not a production fleet-wide policy.

Reproduction on an authorized x1-370 checkout:

```sh
python scripts/p0_37_host_pressure_preflight.py \
  --ssh-host beelink \
  --expect-host BEELINK-RYZEN-7-MINI-PC \
  --require-nas-mount --json
```

Observed exit **2** (`NO_GO`), with three blockers:

- `root_free_below_10_gib`: root available **3.0 GiB** vs minimum **10 GiB**.
- `root_unprivileged_use_at_least_90_pct`: measured **99.36%** vs required **<90%**.
- `io_full_avg10_at_least_5_pct`: measured **23.64%** vs required **<5%**.
- The `/nas` mount was present. Identity and freshness checks passed. Root inode space was not the problem.

Separately, `scripts/p0_37_provider_residency_preflight.py` now includes explicit per-provider `inventory_candidate`, `host_pressure_checked=false` and `physical_inference_permitted=false`, and top-level `physical_inference_permitted=false`. This prevents inventory from being misrepresented as operator admission.

## Next safe sequence

1. **Leave Beelink's running API, Samba, rsync, scanners and NAS mounts undisturbed.** Coordinate any eventual root-space remediation separately with the NAS recovery/ingest owner; first establish what is writing to root and which data is under custody before considering deletion or relocation.
2. Recheck headroom with the opt-in SSH diagnostic *after* storage-pressure remediation. A green headroom sample would only mean local capacity is less constrained; it **does not approve inference**.
3. Identify a safe, already resident model on a node with validated headroom, or obtain approval for a later explicitly bounded canary. Do not autoload.
4. Separately prove a trusted two-real-user delegation flow, immutable per-user request attempts, independent provider-process correlation, approved encrypted producer+node+subject custody, trace replay and response delivery. Keep direct and bypassed calls UNKNOWN.
5. Run a tiny physical two-user LM Studio experiment only after all those independent gates, then perform a separate llama.cpp and physical-iPhone validation.

**P0 release disposition:** NO_GO for Beelink physical inference at the observed time. Draft PR chain #35 → #36 → #42 → #45 remains non-deployed. A passing synthetic test suite cannot substitute for live execution and custody proof.

## Follow-up mount, capacity and workload assessment (read-only)

At 2026-10-11 15:00 UTC, Beelink was rechecked by strict-host-key SSH:

- Root `/`: **ext4** on `/dev/nvme0n1p2`; `df -hT` showed **468 GiB
  filesystem, 441 GiB used, ~3.0 GiB available to unprivileged processes,
  rounded 100% utilization**. `stat -f` reported 4096-byte blocks,
  122,512,118 total, 7,028,026 free including reserved, but only
  786,375 unprivileged-available. The difference is ext4 reserved
  allocation, not discretionary free capacity for applications.
  Inode utilization was **4%**. Do not alter reserve percentage as
  an incidental response to a full root filesystem.
- Canonical `/nas`: **btrfs** on `/dev/sdd2`, ~81 TiB total with ~40 TiB
  available, distinctly mounted from root. `/mnt/nas5-output-primary`
  and `/mnt/nas5-output-direct` are separate exFAT mounts;
  `/mnt/recover` and `/media/scott/SSD_4TB` are NFS mounts.
  These mount identities must be verified before any recovery relocation
  or purported deletion of stale output.
- I/O PSI increased to `full avg10=28.77%`, `some avg10=32.13%`
  in another sample, with ~6.15 one-minute system load. Active high-CPU
  processes included `uvicorn`, Samba, `rsync`, multiple long-running
  `find` scans and Docker control-plane processes. Their file ownership
  and custody are not yet established.
- `journalctl --disk-usage` reported **2.0 GiB of active/archived journals**.
  This is a bounded data point, **not** a recommendation to purge logs.
  Even recovering those bytes would not by itself address the observed
  10 GiB free-space threshold or high I/O stall.
- Root top-level home listings included multiple named NAS5 recovery
  ledgers and dated recovery output directories. A listing cannot
  establish their aggregate byte count, backup custody or deletion safety.
  A recursive full-tree size/hash scan was **not** started because doing
  so under high I/O pressure could worsen the NAS workload.

**Operationally safe next diagnosis:** coordinate with the NAS recovery
owner to identify which filesystems and paths the current rsync/find/uvicorn
processes write, record active destinations and checkpoints, and map known
root-local spool/cache volumes through existing producer manifests or
service logs. Only then consider a bounded low-I/O size inventory or a
copy-verified evacuation/retention plan. Never perform blind
`rm`, `docker system prune`, journal vacuum, filesystem reserve adjustment,
or reformat under issue #37. Keep the model canary NO_GO.
