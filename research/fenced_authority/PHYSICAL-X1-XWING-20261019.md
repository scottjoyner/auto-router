# Physical x1-370 / xwing PostgreSQL execution-fence qualification

Date: October 19, 2026. RESEARCH ONLY / HOLD / NO PROD DEPLOYMENT.

This continues Auto-Router draft #75 on shared PostgreSQL epochs,
revocation, signed attempt reservations and terminal receipts. It is
stacked on #74, #73, #72 and AssistX producer draft #413.

## Physical topology and custody boundary

- A disposable postgres:17-alpine container ran on x1-370, image
  SHA256 b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24.
- Reviewed SQL schema SHA256:
  cb306770d0f7560fd4ce3c8be10b4fdd80e8042c04d24b4d2e861e2e03c7280e.
- The only published PG port was x1 127.0.0.1:55439. A temporary,
  strictly host-key-checked SSH reverse tunnel bound xwing
  127.0.0.1:55539 and forwarded to that x1 loopback listener.
- Real Python 3 processes on xwing issued PostgreSQL v3 wire queries
  through that tunnel, from xwing itself. Separate psql clients ran
  on x1. No Python package or file was installed on xwing.
- Docker used a temporary user-defined bridge, NOT --internal:
  internal Docker networking blocked loopback port publishing here.
  The non-internal bridge could allow container egress. No production
  URI/credential, real user data, model call or persistent volume was
  provided to the disposable container. No external PG port was opened.
- Resource limits: 2 CPUs, 1GiB RAM, 128 PIDs. Temp bridge/container,
  SSH tunnel and both test listeners were removed and checked afterward.

## Actual physical acceptance — PASS

Evidence: research/fenced_authority/evidence/physical_x1_xwing_20261019.json

| Check | Outcome |
| --- | --- |
| Eight competing requests with shared max_attempts=2 | 2 admitted, 6 denied |
| Two different physical clients grant same fresh scope | One winner, one denied |
| xwing creates valid grant and attempt | Pass |
| x1 shares xwing-granted scope for second attempt | Pass |
| xwing followed by x1 delivers same terminal receipt | Recorded, then duplicate |
| x1 revokes current epoch; xwing late terminal | Denied |
| Unspent old token reused in new epoch | Denied |
| Replacement grant increments database epoch | 1 to 2, then 3 |
| SSH tunnel cut; xwing attempts to reach authority | Unreachable |
| After partition, x1 grants epoch 3 and reserves new attempt | Pass |
| Ephemeral resources after run | All removed |

Final trial elapsed about 6.14 seconds. All identities and receipts
were synthetic digest strings, without real inference.

## Important limits

Physical separation of CLIENT COMPUTATION is established, but not
independent administrative custody. Both clients used ONE x1-administered
PostgreSQL primary. There was no synchronous replica, independent
epoch-signing witness, rollback-resistant checkpoint, consensus or
recovery after primary loss.

A denied SQL request/terminal receipt does NOT stop a model worker
already producing output after revocation. Worker-side cancellation
and side-effect checks were not implemented or tested. SQL parameters
were synthetic and NOT authenticated claims from a real trusted
user/Task issuer. The PostgreSQL container used ephemeral trust
authentication only through loopback and SSH; never use this setup
as a production authority.

## Reproduction and remaining release gates

The explicit research harness:
  python3 research/fenced_authority/qualify_twohost.py

defaults to PLAN_ONLY. Execution requires --run, a pinned SSH host key
for xwing, preinstalled PostgreSQL image, and unused loopback ports.
Optionally add --output /absolute/new/result.json. The harness fails
on resource name/port collision and removes only its own resources.

Next: independent durable authority and replication, worker-side
effect fencing with cancellation under partition, two independently
authenticated users, source-signed terminal receipts under separate
key custody, safe rollback, and 72h realistic serving soak. Small
Lenovo LFM is not GLM-5.3-equivalent. Production admission HOLD.

## Regression record

Focused suites covering this physical two-host harness, PostgreSQL
research schema, terminal receipt custody, signed attempts, owner
fencing and executor authentication: **134 passed**.

Full Auto-Router non-integration test suite: **520 passed,
9 skipped, 1 failed**. The single failure was the existing physical
model-catalog drift assertion: x1-370 advertised model alias
toolcall-v5-3b-combined-r2, which was absent from two served-model
catalog samples. This failure was independently observed before this
change and was not suppressed or weakened. Therefore this PR is NOT
eligible to be promoted to a production release.
