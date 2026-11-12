# Fleet-exec off-host protocol rehearsal — xwing and destroyer

**2026-11-03, approximately 21:00 EST. Research-only; production relay and device untouched.**

## Exact source parity

The active x1-370 user units were inspected through their process source
arguments **without reading bearer token, private certificate, or environment values**.

- Live relay PID 3113 points at
  `/home/scott/knowledge/20-Projects/fleet-exec/relay.py`,
  SHA256 `4904966d9e78a24f2b74a70c34d48febb0dfb399f37bed14ba8b52f748cdfb29`.
- Live device PID 3107 points at
  `/home/scott/knowledge/20-Projects/fleet-exec/device.py`,
  SHA256 `d1b624d4dc97adb2b33bb8b93c05441260f931ba255ed235a06f0044a17afa3b`.

Ten explicitly allowlisted Python source/test files were copied by
strict-host-key SSH to **each** isolated owner-only research directory:

`$HOME/fleet-recovery-evidence/fleet-exec-offline-research-20261103`

No host credentials, signed production keys, tokens or unit configuration
were copied; no production relay or agent was restarted. Tests used
temporary ephemeral tokens and certificates with loopback sockets.

## Confirmed test results

| Focused check | xwing | destroyer |
| --- | --- | --- |
| `policy_test` authorization checks | 39/39 PASS | 39/39 PASS |
| `certs_test` synthetic certificate checks | 24/24 PASS | 24/24 PASS |
| `relay_test.MutualTLSIsUsedEndToEnd.test_the_relay_reports_that_it_is_using_mutual_tls` | PASS | PASS |
| `relay_test.TokensGateEverything.test_a_device_token_cannot_dispatch` | PASS | PASS |

Total focused checks: **65/65 passing per host**.
These are isolated tests, not real production task dispatch.

The broader first selected relay suite on Destroyer was stopped by an
explicit **102-second timeout** without a conclusive summary. Do not
claim it passed, and do not interpret the timeout alone as a proven
production relay defect. A narrower single-test mTLS check and
device-token authorization denial both passed on each host.

## OpenSSL 3.5 output compatibility found and corrected offline

The original `certs_test.py` assumes the certificate subject always
contains literal `CN = x1-370` or `CN = operator`. On both candidates,
OpenSSL **3.5.5** emits `subject=CN=x1-370, O=fleet-exec`.
The untouched baseline certificate suite passed 22/24 and failed
only these **two formatting assertions**.

The isolated test copies were changed to parse the **exact** CN value
with bounded subject delimiters while allowing optional whitespace
around `=`. On both hosts 24/24 certificate tests then passed.
The actual certificate-generation and verification code was **not**
changed. A small reviewable patch is included at:

`research/patches/fleet_exec_openssl35_subject.patch`

The patch was reconstructed from the original source and a verified
Destroyer test copy. It applies cleanly and reproduces exactly the
tested file; original source SHA256:
`c1f331b356645a8e2566dd8743316b1a273089c82ee2db50d0d69fc28d990359`.
Patched test SHA256:
`5164fd11ac1cae50badf23d325a7d6f4980e896538c1a5ec9301b8ac5d609521`.
**Patch is proposed only; live x1 source remains unchanged.**

## Failover blocker beyond basic transport

The active relay's source has volatile state:

- `Registry.pending` in a Python dictionary (line 104).
- `Registry.results` in a Python dictionary (line 110).
- `Registry.delivered` in an in-memory list (line 114).
- `Registry.acked` in an in-memory set (line 118).

Read-only source inspection did not establish a persisted replay
journal/lease or single-controller fencing protocol in this relay.
The existing README acknowledges that restarting the relay loses
in-flight responses despite append-only audit evidence. An independent
standby with fresh credentials is **not** sufficient for safe active
takeover unless work state is reconciled and duplicate claims fenced.

Before production failover, separately require:

1. Owner-approved distinct control-plane identity for standby (do not
   clone the production tunnel API key).
2. Separate, authorized fleet-exec relay and per-device mTLS
   provisioning; don't reuse another host's private key.
3. Durable in-flight `call_id`/generation/ack/result evidence in
   independently verified custody; explicitly specify replay rules.
4. An externally pinned, monotonic controller epoch and single-leader
   claim fencing, with denial during quorum/authority uncertainty.
5. Destructive/side-effect task quarantine during failover; attest
   exactly which work completed before any retry.
6. Rehearsed isolation and rollback of the primary and candidate
   standbys. These focused loopback tests do **not** prove these gates.

Production `x1-370` Ubuntu recovery is separately blocked on
physical console, unmounted full root+EFI image, restore and clone
rehearsal under issue #124. Auto-Assist task agent authorization is
separately P0 HOLD under issue #501.

Tracking: https://github.com/scottjoyner/auto-router/pull/125
Source recovery: https://github.com/scottjoyner/auto-router/issues/124
Agent authorization: https://github.com/scottjoyner/auto-assist/issues/501
