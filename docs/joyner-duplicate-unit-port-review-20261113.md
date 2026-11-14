# Joyner duplicate-unit port collision — read-only evidence contract

November 13, 2026. Related tracker:
https://github.com/scottjoyner/auto-router/issues/176

## Scope and finding

Two distinct, enabled user systemd unit families on Joyner specify the same
llama-server listen ports:

- Canonical fleet-joyner-gpu and legacy joyner-lane-1234 both declare 1234.
- Canonical fleet-joyner-ling and legacy joyner-lane-1236 both declare 1236.

At 19:46 ET, the canonical process owned TCP 0.0.0.0:1234, while the
legacy 1234 unit was in auto-restart with ExecMainStatus=1 and more than
390 restarts. The source-definition conflict is confirmed. Exact journal
error was not available to this observer; do not claim a proven EADDRINUSE
failure without a sanitized last-error witness. The canonical Ling workload
was active even though its differently named legacy unit was inactive.

## Witness

scripts/joyner_unit_port_collision.py is a standalone read-only observer,
which only acquires live data on hostname joyner. It reads four allowlisted
systemd unit property sets, associated unit bytes for SHA-256 and numeric
bind-port verification, and an ss -H -ltnp socket census. It does not print
ExecStart, model filenames, full unit paths, environment variables,
credentials, prompts, process command lines, journal entries or raw
stdout/stderr. It makes no HTTP request.

The result is one JSON object, schema:
auto-router.joyner-unit-port-collision.v1
A numeric MainPID matching the socket PID indicates port ownership at one
instant; it does not prove an exclusive GPU lease or readiness. The tool
always exits 78 (HOLD). On wrong host, inaccessible files, duplicate port
declarations, unavailable census or ambiguous identities it fails closed.
The tool cannot change a service and grants no disable/restart authority.

Offline tests cover synthetic duplicate listeners, failed legacy service,
API-key redaction, ambiguous port parsing, service allowlists, denied
observer input and fail-closed behavior. Hosted CI tests the exact PR
head with pytest only, never accessing Joyner or any secret.

## Optional read-only usage

On a machine with existing approved Fleet Commander/SSH access, feed
the script to a remote Python interpreter via stdin; do not install it
on Joyner:

  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes joyner python3 - \
    < scripts/joyner_unit_port_collision.py

Expect one JSON line and exit code 78. Do not infer a restart action from
this receipt. Forward only JSON fields from the defined schema.

## Release/repair gates

1. Identify versioned source and owning agent for all canonical and
   legacy unit files, including proxies/clients and protected ports.
2. Obtain bounded, sanitized startup error, independent socket-owner
   evidence, and a recorded operator decision naming the redundant legacy
   unit. Preserve canonical MiniCPM, Ling, LFM3 and Tailnet routes.
3. Only when authorized, create a separate minimal deployment action with
   explicit rollback and post-change tests: stable service owners, no restart
   loop, preserved 1234/1236/19175 inference health, authenticated routing
   and signed custody receipts.
4. Until then: HOLD. Do not disable, stop, mask, restart, reconfigure,
   redirect traffic, alter credentials or allocate newly visible GPU RAM.

## Privacy refinement before merge review

The observer necessarily reads the bounded contents of the four approved
unit fragments to parse numeric ports and compute hashes. Those bytes may
include sensitive launch arguments, so the receipt MUST NOT claim that
no sensitive definition text was read. The published receipt instead states
that unit definitions were inspected, credential values were not emitted,
and external environment files were not opened.

The fragment reader rejects nonstandard paths, symlinks and any fragment
outside the current user's standard .config/systemd/user directory. This
prevents a crafted FragmentPath from turning the observer into a file reader
for an unrelated credential or model path. The returned JSON includes only
allowlisted unit status fields and fragment digests.
