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

## Explicit dependent-proxy reconciliation (follow-up)

The original four-unit witness is now extended to the two explicitly named
enabled proxy units that depend on the legacy 1234 service:
joyner-lane-1234-lan-proxy and joyner-lane-1234-ts-proxy.

The observer reads only their allowlisted properties and matching user-unit
fragments (no EnvironmentFile follow-up, no credential logging). It reports
only their enabled/active status, SHA-256 of the unit fragments, exact
Requires/After dependency booleans for the allowlisted legacy service, and
boolean mentions of loopback and port 1234 in their static ExecStart lines.
It does not emit a full upstream URL or a command line; boolean references
alone are not proof of endpoint health, security or route ownership.

The live read-only Fleet Commander witness on November 13 found that both
proxies are enabled but currently inactive; both have static and runtime
Requires/After links to the failing legacy service. Both unit fragments
mention loopback and 1234. Thus neither idle status nor a healthy canonical
port 1234 permits deleting the legacy unit without reconciling those units.

New acceptance gates: gather the proxy owners, route consumers and desired
post-reboot behavior; approve the intended service-to-proxy ownership and a
separate rollback. Any missing proxy evidence adds a HOLD reason. The witness
always includes may_retire_legacy_unit=false,
dependent_proxy_owner_verified=false, and
dependent_proxy_contract_verified=false. No auto-fix is implemented.

This is source-only work, with synthetic no-secret-emission and wrong-host
tests. Running the diagnostic on Joyner over stdin is read-only and returns
exit 78 by design. The raw unit fragment may include confidential arguments;
it is inspected transiently but never emitted in the receipt. No model data,
key files, environment files or outbound inference requests are accessed.

## Observed bind-scope drift (read-only extension)

Current Joyner unit fragments were assessed without printing actual host
addresses, command lines, or credentials. The updated witness classifies an
explicit llama-server --host setting as wildcard, loopback, private-specific,
CGNAT/Tailnet-range or unverified. It never emits the literal IP.

Both canonical services (fleet-joyner-gpu on 1234; fleet-joyner-ling on 1236)
now declare wildcard bind scopes, while their legacy counterparts declare
loopback-only bind scopes. This is materially different from the archived
October 20 co-residency policy, which documented a Tailscale-address model
listener with separate LAN proxies. It is NOT a network-exposure/authentication
audit: actual firewall, listener reachability, credentials and authorized
clients were not tested. A wildcard bind does not alone prove internet access.

The witness emits canonical_wildcard_legacy_loopback and
bind_scope_collision_plausible for each matching port. The latter requires
the observed canonical PID to match the live socket census. It means a
bind-level conflict is plausible; it does not confirm the crashing process's
systemd journal error. The service retirement/traffic cutover gate remains
HOLD regardless of these booleans.

Current Auto-Assist repository main contains an expired joyner:1234
reservation, with an August 27, 2026 expiry. This is SOURCE-ONLY historical
evidence, not a live runtime router lease decision. Confirm effective runtime
reservation/admission before changing any service, proxy or model assignment.

## Wildcard listener and credential-free catalog evidence

The existing Joyner forward-auth auditor was incorrectly treating an exact
LAN/Tailnet interface as unbound when ss reported a wildcard IPv4 listener
on 0.0.0.0. The source fix recognizes a wildcard bind as local listener
evidence for each configured IPv4 endpoint, recording exact_ipv4 or
wildcard_ipv4 as listener_match_type. It never equates local binding
with remote reachability or a permitted route.

A bounded read-only live probe from Joyner (GET /v1/models only, 1.5-second
per-endpoint timeout, no API keys/tokens and no prompts) reported:

- 1234 and 1236 via the configured LAN/Tailnet endpoint addresses:
  HTTP 200 without credentials and wildcard_ipv4 listener match.
- Both guarded Tailnet canary endpoints (18320/18321):
  HTTP 401 without credentials and exact_ipv4 listener match.
- perimeter verdict PERIMETER_NO_GO, model catalog probes initiated only
  from Joyner, cross-host reachability not established, routing not approved,
  model generation not performed, service configuration untouched.

This means local unauthenticated catalog access at the nominated interface
addresses is demonstrated, NOT that an outside device or public internet
can reach those endpoints. Confirm network ACL/firewall/ingress and legitimate
consumer expectations independently before a cutover or exposing new clients.
Do not distribute the model catalog to unintended clients or use a
credential-free response as production admission evidence.

The corrected Joyner forward-auth offline GitHub workflow also pins and
asserts the exact pull-request source head. All changes are source-only and
must pass hosted CI; no service, firewall, proxy or router change was made.

## Reproducible read-only cross-host Tailnet authentication witness

The additional scripts/joyner_tailnet_crosshost_auth_witness.py produces a
source-only proof, running ONLY when observer hostname is x1-370.
It probes four preexisting pinned Joyner Tailnet catalog endpoints:
MiniCPM :1234, Ling :1236, guarded MiniCPM :18320 and guarded toolcall
:18321. It has no caller-supplied target or port override. The client uses
a fresh proxy-free HTTP opener, fixed GET /v1/models, no Authorization,
no redirects, no response body reads and a 2.5-second per-request timeout.

The schema auto-router.joyner-tailnet-crosshost-auth.v1 emits only allowed
endpoint labels, numeric ports and HTTP/status categories. The tool always
says PERIMETER_NO_GO and release_gate=HOLD, denies any service or router
authorization and exits 78. Wrong host invokes no network calls.

At 2026-11-13 ~20:39 ET the real bounded x1-370 observer returned:
  MiniCPM :1234 -> HTTP 200 without credentials
  Ling :1236 -> HTTP 200 without credentials
  Guarded MiniCPM :18320 -> HTTP 401 without credentials
  Guarded toolcall :18321 -> HTTP 401 without credentials

This establishes cross-host Tailnet reachability from one known authorized
fleet peer and no-key catalog access on two endpoints. It does NOT establish
unauthorized-peer access, public internet reachability, model generation
permission, the authoritative service owner, or permission to retire a
legacy unit. No remote service, secret, model or router was changed. No
response body, authorization header or model output was stored.

The new test harness is entirely offline, with faked statuses and synthetic
secrets to detect accidental data leakage. Exact-head GitHub CI runs
host-local and cross-host auditor tests without reaching any fleet node.
Do not run the real network probe in hosted CI. Its live run is allowed only
as a bounded read-only check from existing approved Fleet Commander access.

P1 authentication and ingress policy tracker:
https://github.com/scottjoyner/auto-router/issues/81

All rollout remains HOLD pending approved LAN/Tailnet principals, proxy
client and reboot/failover contracts, guarded alternative endpoints, a
signed operator decision and validated rollback.

## Canonical MiniCPM pinned source disk identity — November 13, 2026

An independent, narrowly allowlisted read-only source file witness now
exists as scripts/joyner_minicpm_source_pin_witness.py. It validates:

1. Running on hostname joyner; wrong host => zero process or file reads.
2. Exactly one same-owner llama-server process with argument --port 1234
   and the exact approved canonical MiniCPM model path.
3. The approved model file is a regular, non-symlink file owned by the
   observing user, has the expected 688065920 bytes, and retains identical
   inode/size/mtime/ctime throughout streaming SHA-256 validation.
4. Its SHA-256 equals the existing guarded MiniCPM canary reference
   81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa.
   Offline tests assert these two source constants cannot drift silently.

Live read-only result from Joyner ~21:07 EST:
- Source llama-server PID 1825 was uniquely bound by process arguments
  and owner identity to the canonical file.
- The source file size and full SHA-256 matched the guarded canary's pinned
  reference. No model contents were copied or emitted; the file was streamed
  locally to compute its digest.
- No credentials, environment files, HTTP requests or model generations
  were used. The executable was passed via SSH stdin, not installed.
- Receipt verdict HOLD, process verified true, disk SHA match true,
  guarded process weights verified false, resident loaded weights verified
  false, equivalent request behavior verified false, cutover authorized false.
  Exit code 78 is intentional.

This improves *canonical source-file custody*. It does not prove the
current guarded process is actually using the same model image loaded
into memory. In particular, a guarded listener may run under another
owner or use a different pathname/inode; a shared basename is not enough.
Do not read another user's key file or run a synthetic model-generation
canary without the separate protected authorization. Do not infer a lease
or service retirement approval from a model-file hash.

The existing exact-head offline Joyner unit-port workflow now also
runs synthetic tests for this new source pin (including wrong-host,
mismatched owner, duplicate process, missing or replaced file, symlink,
invalid digest and never-authorize-cutover properties). No CI job accesses
Joyner or downloads a real model.

## Guarded MiniCPM owner-bound, hash-only acceptance — November 14, 2026

The guarded MiniCPM service on port 18320 runs under a different non-root
account from the Fleet Commander SSH user. Read-only process metadata
identified that owner mismatch, and the guarded model file was not readable
from the SSH session. That is a protection boundary, **not** permission to
elevate privileges, change ownership/modes, or inspect the guarded key.

`scripts/joyner_guarded_minicpm_owner_digest.py` is the narrow, offline-tested
custody check intended for execution **only by the already-authorized
guarded service owner** through a recorded operator-approved execution
channel. It does not try to invoke sudo, su, Fleet Commander privilege
escalation, or a service action. With no approved channel, execution remains
blocked and the existing evidence remains HOLD.

The diagnostic verifies that the host is Joyner, the caller is non-root,
and exactly one caller-owned llama-server has port 18320, a single absolute
model-file argument and an api-key-file **flag**. It does **not** open the
api-key-file. It requires a model path under the restricted /home/joyner
namespace, rejects parent/path symlinks and non-regular files, requires
file ownership by the guarded owner, and hashes the disk file through an
O_NOFOLLOW file descriptor. Size, inode, timestamps, and process starttime
are checked before/after hashing. The expected file size and full SHA-256
are the same pinned values in the preexisting MiniCPM guarded canary:
688065920 bytes, SHA-256
81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa.

Even on exact success the printed JSON receipt contains only allowlisted
booleans and the guarded PID. It ALWAYS says HOLD, exits 78, and reports
resident GPU weights, request-equivalence, receipt signature, production
router approval and service-change approval as FALSE. A matching disk file
and owning process do **not** establish that the running GPU has the right
weights loaded, that aliases match, or that an authorized request behaves
the same as the existing unguarded lane.

Offline unit tests cover wrong host, root, owner mismatch, absent/duplicate
process, ambiguous flags, PID restart during hashing, changed digest, file
size, symlink, non-owner file, and hold-only semantics. The dedicated
read-only CI workflow pins and verifies the exact PR checkout. CI never
contacts Joyner or handles production credentials.

Release remains HOLD pending an approved guarded-owner execution receipt,
independent source/guarded alias and synthetic request-contract acceptance,
real downstream client/credential and LAN/Tailnet contracts, rollback, and
an operator-approved change. No running service should be stopped,
reconfigured, or allocated more GPU resources based on this document.
