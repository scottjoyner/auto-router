# Quota Strategy

## 1. Strategy summary

The router should intentionally consume free quota on valuable work before reset while protecting enough reserve for high-priority interactive tasks.

The goal is not simply lowest cost. The goal is:

```text
high-priority quality > useful quota burn-down > latency > local fallback cost
```

## 2. Quota classes

| Class | Meaning | Example providers |
|---|---|---|
| `premium_free` | Scarce but high-quality free quota | Gemini, Mistral, GitHub high-tier models |
| `fast_free` | Good for low-latency drafting/refinement | Groq, Cerebras |
| `edge_free` | Neuron/task budget; good for small jobs | Cloudflare Workers AI |
| `brokered_free` | Free model via aggregator | OpenRouter `:free` models |
| `local` | LM Studio fallback | r2d2, deathstar, mini-pc |

## 3. Burn-down policy

Daily free quota should be used as much as possible without starving urgent work.

### Reserve bands

```yaml
reserve_policy:
  critical_reserve_percent: 20
  interactive_reserve_percent: 10
  background_release_after_local_hour: 18
  aggressive_burn_after_local_hour: 21
```

Interpretation:

- Before 18:00, preserve reserve for critical/interactive work.
- After 18:00, begin spending surplus quota on queued useful work.
- After 21:00, aggressively use quota that would otherwise reset unused.

## 4. High-priority deliverables

High-priority deliverables may trigger multi-stage execution:

1. **Draft** with local LM Studio or low-cost/free fast model.
2. **Refine** with the strongest currently available free model.
3. **Judge** using a separate model/provider when quota allows.
4. **Repair** only if judge finds concrete issues and budget remains.

This spends high-value free quota on the highest leverage step rather than wasting it on first-pass boilerplate.

## 5. Quota dimensions

The quota manager must support:

```text
rpm  requests per minute
rpd  requests per day
tpm  tokens per minute
tph  tokens per hour
tpd  tokens per day
tpmth tokens per month
npd  neurons per day
conc concurrency
```

## 6. Reservation lifecycle

```text
1. Estimate quota cost.
2. Reserve every affected dimension atomically.
3. Dispatch request.
4. Parse provider usage and rate-limit headers.
5. Reconcile actual usage.
6. Release unused reservation.
7. Record usage event.
```

## 7. Provider header reconciliation

Providers such as Groq and Cerebras expose rate-limit headers. Adapters should parse headers when available and update local counters.

Examples of useful header families:

```text
x-ratelimit-limit-requests
x-ratelimit-remaining-requests
x-ratelimit-reset-requests
x-ratelimit-limit-tokens
x-ratelimit-remaining-tokens
x-ratelimit-reset-tokens
retry-after
```

## 8. Exhaustion behavior

When a provider is exhausted:

```text
same quality tier alternate provider
  -> lower quality free provider
  -> OpenRouter free fallback
  -> local LM Studio
```

When a provider returns repeated 429s, open a circuit breaker until the later of:

- `retry-after`;
- parsed reset header;
- configured cooldown.

## 9. Scheduler examples

### Example: high-priority code review

```text
local qwen-coder draft
  -> Cerebras/Groq/Mistral refine
  -> Gemini/GitHub judge if available
  -> local repair if quota exhausted
```

### Example: nightly transcript summarization

```text
if surplus free quota remains after reserve:
  use Groq/Cloudflare/Z.AI for low-risk summaries
else:
  queue for local LM Studio batch processing
```

### Example: private insurance document

```text
local_only=true
  -> LM Studio only
  -> no cloud attempt
```

## 10. Metrics

Track:

- quota remaining by provider/model/dimension;
- daily burn-down percentage;
- projected unused quota at reset;
- high-priority jobs refined/judged;
- fallback count by provider;
- local-only bypass count;
- 429 and circuit-breaker events.
