# x1-370 Fleet Commander backchannel and offline recovery handoff

**Status (2026-11-03): Control-plane reachable; OS repair HOLD.**

## Verified access evidence

Fleet Commander reported 159,424 cumulative tool calls, with 158,620 successes
and 804 failures (~99.5% successful), and no active terminal sessions when
queried. The latest sampled 300 local calls had no recorded error flag.
This indicates the tool is reachable, not that every fleet service is healthy.

Four independently authenticated SSH routes to x1-370 were confirmed:

| From host | Host-key verification | x1 reported hostname | Kernel |
| --- | --- | --- | --- |
| joyner | strict PASS | x1-370 | 7.0.0-42-generic |
| xwing | strict PASS | x1-370 | 7.0.0-42-generic |
| deathstar | strict PASS | x1-370 | 7.0.0-42-generic |
| destroyer | strict PASS | x1-370 | 7.0.0-42-generic |

Every source independently ran the checked-in read-only
`research/x1_backchannel_readiness.py` from its own SSH session.
All four returned `reachable=true`, `host_identity_matched=true` and
`may_repair_os=false`, `out_of_band_console_verified=false`.

### Non-privileged operator use

The verified script was staged in the **owner's home directory**,
with a source SHA256 of
`df64d4b1ad793787724052802a7a2748fa355d6ec81e8261be3d72d6be8498d0`.

On one of the four trusted Linux nodes, run:

```bash
python3 "$HOME/fleet-recovery-evidence/x1-370-backchannel-20261103/x1_backchannel_readiness.py" --check
```

This command connects to x1 using existing authorized SSH public-key
credentials, `StrictHostKeyChecking=yes`, `BatchMode=yes` and no
TTY/forwarding or privilege escalation. It asks only for hostname and
kernel version. If the trusted key, route, identity or connection fails,
the checker reports `reachable=false` and returns status 2.

**This is OS-dependent SSH access, not independent recovery access.**
When x1 is powered off, Linux boot is broken, Tailscale is down, or its
network is unavailable, none of these connections is guaranteed.
Do not weaken SSH host-key checking, edit credentials, or bypass
the Fleet Commander no-new-privileges sandbox.

## Offline rescue artifacts

- Owner-only verified Ubuntu 24.04.5.1 amd64 ISO on Joyner:
  `/home/scott/fleet-recovery-evidence/ubuntu-secureboot-rescue-20261103-ZYF46C6v/ubuntu-24.04.5.1-desktop-amd64.iso`
- Independently verified NAS mirror:
  `/nas/backups/x1-370-signed-live-recovery-20261103-Uxlmr0xv/ubuntu-24.04.5.1-desktop-amd64.iso`
- Signed vendor SHA256:
  `4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7`.
- Nine cached repair packages verified on NAS under:
  `/nas/backups/x1-370-offline-apt-debs-20261103-cskmisux`.

Owner-controlled physical console and a new, empty rescue USB remain
unavailable/unproven. Existing camera/media USB and other data disks
must not be overwritten. Secure Boot must not be disabled casually.

## Readiness boundaries

1. SSH backchannel PASS does not prove root/sudo entitlement or
   full-system backup/restore. The Fleet Commander process has
   `NoNewPrivs=1`, `CapEff=0`, and is not authorized to perform disk
   imaging or package repair.
2. Joyner has /dev/kvm, but `/dev/kvm` was not accessible to the
   current unprivileged user and no usable QEMU/OVMF binary was
   discovered in the scoped inventory. No clone boot was rehearsed.
3. x1's `libc6` package remains half-installed; its dpkg status SHA
   and pinned read-only APT transaction agree with issue #124.
4. Signed GRUB and Shim maintainer-script branches may change EFI.
   Repair must await physical console, full offline root+EFI image to
   verified NAS, independent restore, isolated clone rehearsal and
   explicit owner maintenance authorization.
5. Vendor AMD XDNA 16K route remains production NO-GO under issue #122.

Tracking: https://github.com/scottjoyner/auto-router/issues/124
Draft research: https://github.com/scottjoyner/auto-router/pull/125

## November 3, 2026 ~19:36–19:40 EST — exact MCP containment diagnosis

Read-only inspection of the actual x1 systemd user units gives:

| User unit | Active | NoNewPrivileges | Service restarts |
| --- | --- | --- | --- |
| openai-mcp-desktop-commander | active/running | yes | 0 |
| fleet-exec-relay | active/running | yes | 0 |
| fleet-exec-device@x1-370 | active/running | yes | 0 |

The Fleet Commander shell reports UID=1000, CapEff=0, NoNewPrivs=1.
Using existing authorized strict-host-key SSH **through Joyner**, a normal
x1 login also reports UID=1000 but NoNewPrivs=0; its visible
/etc/sudo.conf is owned by UID 0 with mode 0644 and its sudo policy
shows the expected user. This distinguishes the **restricted MCP tunnel
context** from the host's actual sudo configuration. The earlier
UID=65534 appearance should not be treated as proof of host file
ownership corruption. **No sudo command was executed.**

Do not disable systemd NoNewPrivileges, alter sandbox policy, copy
credentials, or route privileged OS repair through this MCP tunnel.
The standard host maintenance channel must be explicitly authorized
and is still blocked for recovery by absent consistent offline backup
and physical console/restore proof.

New read-only self-check:
research/x1_control_plane_boundary_audit.py --check

It checks only fixed systemd user-unit liveness, expected containment
properties and the current process NoNewPrivs flag. It does not query
secrets or grant authority. Even a complete PASS returns
privileged_recovery_authorized=false, machine_agent_task_auth_proven=false,
offline_console_and_rollback_proven=false. Its 10 synthetic tests
cover missing units, wrong containment, failed service result,
unreliable context, and safe command construction.

Separate **productive task dispatch** blocker: on five fleet nodes,
fleet-node-agent is an active **user** systemd service, while the system
unit is inactive. [Auto-Assist issue #501](https://github.com/scottjoyner/auto-assist/issues/501)
reports that actual per-node credentials are rejected HTTP 401 by
task listing; production has two registered identities, zero approved
slots and expired signed capacity projection. Repeatedly restarting
agents or giving them the operator Basic credential is forbidden.
This machine-auth migration and claim gating must be reviewed
separately from Fleet Commander MCP connectivity.

At last exact head, draft recovery research CI on PR #125 remains
read-only and does not imply production or boot recovery authorization.
