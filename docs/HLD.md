# High-Level Design: auto-router

## 1. Purpose

`auto-router` is a local containerized LLM routing layer that presents a standard OpenAI/LM Studio-compatible API while scheduling work across free cloud LLM quotas and local LM Studio endpoints.

The primary optimization target is:

> Maximize useful consumption of legitimate free daily/monthly LLM quota for high-priority deliverables while preserving quality, privacy, reliability, and local fallback.

## 2. Non-goals

- No evasion of provider limits.
- No account/key farming.
- No unapproved cloud processing of sensitive data.
- No dependency on a single remote provider.
- No provider-specific client lock-in.

## 3. Core routing pattern

```text
Client request
  -> OpenAI-compatible API facade
  -> request normalizer
  -> privacy classifier
  -> priority/task classifier
  -> policy engine
  -> quota reservation
  -> provider adapter dispatch
  -> response normalizer
  -> usage ledger + dashboard metrics
```

## 4. Quality-optimized high-priority flow

For high-priority deliverables, the router should support multi-stage execution:

```text
Stage 1: local/cheap draft
  -> LM Studio small model or cheap free model

Stage 2: stronger free refine
  -> Gemini / Mistral / Cerebras / Groq / Z.AI / GitHub Models / OpenRouter free model

Stage 3: judge/repair when enabled
  -> different provider/model if quota allows

Stage 4: final response
  -> return best accepted result with provenance metadata
```

This lets cheap local models produce initial structure while scarce free cloud quota is spent on refinement, code review, test generation, architectural critique, or final answer polish.

## 5. Request priority classes

| Class | Meaning | Routing behavior |
|---|---|---|
| `critical` | Important operator-facing deliverable | Use best allowed model, allow refine + judge, reserve premium free quota first |
| `interactive` | Normal chat/coding work | Use free quota if available, otherwise local |
| `batch` | Offline summarization or enrichment | Schedule around quota burn-down windows and low-priority buckets |
| `background` | Non-urgent maintenance | Run locally unless surplus quota would expire soon |
| `local_only` | Sensitive/private | LM Studio only |

## 6. Provider classes

### Free cloud providers

- Gemini for multimodal, long-context, high-quality reasoning where allowed.
- Groq for low-latency fast draft/refine passes.
- Cerebras for fast larger-model inference with token bucket limits.
- Mistral for chat, code, and structured outputs where configured.
- GitHub Models for prototyping and model comparison.
- Cloudflare Workers AI for small edge-style tasks and neuron-budget use.
- Z.AI / Zhipu for GLM flash/free lanes.
- OpenRouter as a comparison/fallback layer for `:free` models.

### Local providers

- LM Studio endpoints on homelab/Tailscale nodes.
- Optional future vLLM/llama.cpp endpoints if they expose an OpenAI-compatible API.

## 7. Quota as a scheduling resource

The router tracks quota by dimension:

- requests per minute
- requests per day
- tokens per minute
- tokens per day
- tokens per month
- neurons per day
- concurrency
- model-specific context and output caps

Quota reservations happen before dispatch to prevent oversubscription under concurrency.

## 8. Dashboard overview

The dashboard should show:

- remaining quota by provider/model/dimension;
- estimated time to reset;
- today’s burn-down progress;
- high-priority deliverables that used refine/judge passes;
- provider health and open circuit breakers;
- LM Studio fallback usage;
- latency/error trends;
- cloud-vs-local split.

## 9. Deployment model

MVP deployment:

```text
llm-router  FastAPI app
redis       atomic reservation counters
sqlite      durable usage ledger
```

Later deployment:

```text
llm-router
redis
postgres
prometheus
grafana
optional worker/scheduler
```

## 10. Trust boundaries

- Secrets remain in `.env`, Docker secrets, or a future secret manager.
- Sensitive prompts can be forced local.
- Full prompt logging is disabled by default.
- Usage metadata is logged, prompt bodies are redacted unless explicitly enabled.

## 11. Success criteria

- Clients can use `auto-router` exactly like an LM Studio endpoint.
- Daily free quota is visibly consumed on meaningful work.
- High-priority jobs receive higher-quality multi-stage treatment.
- Exhausted/unhealthy providers do not break workflows.
- Local LM Studio endpoints always remain available as fallback.
