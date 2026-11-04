# Fleet Commander standby handoff — code-only, no production cutover

**November 3, 2026 ~20:15 EST. Research-only, explicit HOLD.**

## Actual topology and resolution scope

- x1-370 hosts the only confirmed active ChatGPT MCP tunnel
  (openai-mcp-desktop-commander.service), the fleet-exec relay, and its
  local fleet-exec device; all active, Result=success and restart count 0.
- All three are configured NoNewPrivileges=yes. That is an intentional
  access boundary; do not disable it to run sudo or dpkg.
- Four independent Linux nodes (joyner, xwing, deathstar, destroyer)
  can authenticate to x1 with strict-host-key SSH; they are **not**
  offline consoles for x1.
- xwing, deathstar, destroyer and joyner independently reached
  the x1 Fleet Exec relay TCP endpoint on its Tailnet address.
- No verified second active MCP tunnel or fleet-exec relay was found
  in the scoped alternative-node inventory. Deploying another tunnel
  with the SAME production credential is forbidden.
- Productive Auto-Assist task authorization is blocked separately by
  five node-agent HTTP 401s under scottjoyner/auto-assist issue #501.
  Sharing operator Basic auth or bypassing claims is prohibited.

## What was safely prepared off-host

The following **two standby candidates** are x86-64 Linux with Node.js
v22.23.4, npm 10.9.9, Tailscale and existing authorized SSH:

| Candidate | Existing relay TCP | Local package staging | Credential/service state |
| --- | --- | --- | --- |
| xwing | reachable | staged, hash verified | not registered/started |
| destroyer | reachable | staged, hash verified | not registered/started |

Both candidates contain an isolated owner-only directory:

  $HOME/fleet-recovery-evidence/fc-standby-readiness-20261103/

Items staged:

1. Existing x1 `tunnel-client` *program bytes only*, staged as
   `tunnel-client.staged` with file mode 0600 (not executable).
   Its exact SHA256 matches the original x1 binary:
   `0b372a21f30ba2b79d4550a6f18d10ab254132911a2c82d4b1028319f13b5b07`.
   Source was 19,644,576 bytes, statically linked x86-64 ELF.
2. `desktop-commander/` directory containing only npm lockfile-based
   server dependency installation. Source `package.json` SHA256:
   `d8c921452e02b925b5b8b0a49274bc4b346dffc38d7967f36864d65e211f1e31`.
   Source `package-lock.json` SHA256:
   `01c3d51e3dbc4e6e08ee6a5be6a4277d1692f35ff0477539cce8f78941270245`.
   Exact pinned dependency `@wonderwhy-er/desktop-commander@0.2.52`.
   Installation used `npm ci --ignore-scripts --no-audit --no-fund
   --omit=dev` with **isolated empty npm configs** and a scoped cache.
   No npm install/postinstall lifecycle scripts were run. This
   cannot prove that all optional/native runtime dependencies work.
3. `node --check` on the staged dist/index.js passed on both nodes.
   No MCP process was invoked; neither node has a registered service
   or live ChatGPT tunnel. No control-plane credential, operator token,
   private certificate or per-node agent secret was copied.

Joyner lacks Node/npm and accessible KVM in its current context, while
Deathstar lacks usable KVM. They remain useful SSH recovery channels.

## Standby activation remains blocked

Before *any* new tunnel can be activated:

1. Owner chooses a separate standby node, authority, scope and
   independent **control-plane identity**; provision and register its
   credentials through the authorized platform workflow rather than
   cloning the production key.
2. Verify the tunnel-client artifact's provenance, version and
   compatibility with the **current** platform. Test the installed
   npm module in an isolated offline context (lifecycle scripts were
   deliberately skipped); keep audit retention and per-node policy.
3. Build a separate, least-privilege service unit with
   NoNewPrivileges=yes and explicit allowed paths and blocked commands.
   Confirm independent service identity and fail-closed recovery.
4. Make an explicit relay topology decision: a second MCP tunnel
   does not provide full availability if its fleet-exec relay still
   depends on x1. Use separately approved relay CA/device credential
   provisioning, target-address migration, single-leader constraints,
   signed audit continuity and rollback testing.
5. Stage a supervised **read-only** standby check with no simultaneous
   duplicate controller identity. Confirm primary/secondary split,
   no duplicate job claims and revocation paths before any switch.
6. Obtain approval for a bounded cutover/rollback; never install
   or start a second controller just because the code is present.

Until those gates pass, `research/fleet_commander_standby_readiness.py`
must report `standby_eligible_for_cutover=false` and must never read
or copy credentials, modify services or alter production routing.
13 isolated synthetic tests passed on its first local run; all four
candidate nodes were queried without privileged operations.

## Separate host OS HOLD

Even a properly registered standby would not repair x1's interrupted
Ubuntu 26.04 upgrade. Issue #124 requires owner-controlled physical
rescue media, independent console, an unmounted complete root+EFI
backup and independently boot-proven restore, then clone rehearsal
of glibc/GRUB/Shim/ROCm. The verified Ubuntu live ISO and 9 APT
candidate packages are stored on NAS, but no physical boot has been
tested. Do not disable Secure Boot or overwrite camera/media USB.

Tracking: https://github.com/scottjoyner/auto-router/issues/124
Research PR: https://github.com/scottjoyner/auto-router/pull/125
Agent auth P0: https://github.com/scottjoyner/auto-assist/issues/501
