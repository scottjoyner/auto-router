# Physical synthetic worker output fencing — October 19, 2026

**Research only / NO DEPLOY / GLM admission HOLD / Lenovo auto-failover HOLD.**

This is stacked after Auto-Router draft #75 (physical x1/xwing shared
PostgreSQL primary), #74 (signed terminal receipts), #73 (durable
attempts), #72 (owner binding), and AssistX #413 (Task owner producer).

## Exact property demonstrated

The preceding two-host experiment showed that PostgreSQL rejects a
revoked worker's late terminal receipt. That is NOT proof that a running
worker stops sending its output. This follow-up adds a synthetic
resource-side accepted-chunk fence. A chunk is only accepted when the
same database transaction:
- Locks scope and execution rows with SELECT FOR UPDATE.
- Confirms active, nonexpired epoch and matching owner, claim and
  projection generation, with a still-reserved execution.
- Confirms strictly monotonic chunk sequence for the execution.
- Persists a SHA256 digest of accepted synthetic chunk data under a
  unique request/sequence key, before the test worker releases output.

Revocation, mismatched epoch, unavailability or refused SQL all suppress
the next synthetic output and trigger the test worker's cancellation
callback exactly once. This callback is NOT real llama.cpp, LM Studio,
vLLM, GPU or process cancellation.

## Physical topology and evidence

- x1-370 hosted a resource-bounded disposable postgres:17-alpine
  instance. It ran the research schema.sql plus a separate
  worker_effect_schema.sql extension.
- PostgreSQL was only exposed on x1 loopback 127.0.0.1:55439. A
  temporary pinned-host-key SSH reverse tunnel made it available only
  on xwing loopback 127.0.0.1:55539.
- A genuine xwing Python process executed synthetic streaming-worker
  code. Nothing was installed or saved persistently on xwing.
- First test: xwing emitted the first PostgreSQL-accepted synthetic
  chunk; x1 revoked the epoch before allowing the second chunk;
  the second transaction failed and the worker invoked one cancel
  callback. Exactly one accepted DB chunk existed.
- Second test: xwing emitted one accepted synthetic chunk; x1
  removed the authority tunnel; xwing's second transaction could not
  reach PostgreSQL and the worker invoked one cancel callback.
  Exactly one accepted DB chunk existed for this request.
- Prior physical racing checks also passed: two grants raced with
  one winner; eight contending attempts with budget two admitted
  exactly two; old epoch receipt and token reuse were rejected.

Recorded decision:
PASS_PHYSICAL_SYNTHETIC_WORKER_OUTPUT_FENCE_NOT_PRODUCTION

Evidence file:
research/fenced_authority/evidence/physical_worker_chunk_fencing_20261019.json

The final physical trial took approximately seven seconds. All
temporary Docker containers, bridge networks, reverse SSH tunnel and
both listener ports were verified gone. No production data, real
credentials, model loads, inference routing or live services changed.

## NO-GO limitations

1. This tested a cooperative two-chunk synthetic Python worker,
   NOT a real GPU/CPU model runtime or genuine inference abort.
2. PostgreSQL acceptance is atomic with its epoch check, but data
   accepted before revocation could be delivered from socket buffers
   after revocation. **Client delivery is not linearized or proved.**
3. A malicious worker could bypass this demonstration. SQL function
   arguments are not independently authenticated proof. Production
   needs a separate trusted broker checking signed Task ownership,
   live AssistX claim, epoch, projection, model/runtime and result.
4. The actual Auto-Router streaming wrapper remains unchanged:
   src/auto_router/stream_lifecycle.py currently forwards chunks
   after recording telemetry; no per-chunk authoritative effect gate
   or proven bounded upstream model cancellation has been wired in.
5. No replicated authority, independent administrative/cryptographic
   key custody, anti-rollback witness or cross-datacenter consensus.
   PostgreSQL remained a single x1-controlled primary. Docker's
   temporary bridge was not internal-only (Docker prevented its
   localhost publishing), but no external database port was mapped.
6. No real two-user gateway authentication, independent signer,
   production-scale task compatibility or 72-hour serving soak.

Next gate: a reviewed, default-off real inference-stream adapter with
trusted broker admission per output chunk, actual provider abort/drain
on denial, post-abort process proof and real per-owner signed terminal
receipts. Test a real limited inference canary on a qualified node
with revocation and partition injection, independent witness custody,
rollback and 72-hour soak. Do not enable live auto-failover now.

## Focused and broader regression evidence

Physical streamed revocation/partition trial PASS with native, disposable
PostgreSQL 17 and real xwing Python process, as recorded in the JSON
evidence file. Hermetic synthetic stream, transport, schema, owner,
signed attempt, terminal-receipt and claim-fence tests: **144 passed**.

Full Auto-Router non-integration suite: **530 passed, 9 skipped,
1 failed**. The sole failure was an existing x1-370 provider registry
drift test: the registered alias toolcall-v5-3b-combined-r2 was absent
from the runtime's served model catalog. That check previously failed
before this branch and remains unchanged. The branch is NOT safe to
merge or deploy based on incomplete broader acceptance.
