# Provider Matrix

Provider quotas change frequently. Treat this file as a starting point and verify active limits in each provider console before production use.

## 1. Google Gemini

- Strengths: multimodal, long-context, strong general reasoning.
- API style: native Gemini plus OpenAI-compatible compatibility endpoint.
- Quota mode: project-level limits, often varying by model and tier.
- Router use: high-priority multimodal, long-context refine/judge, final answer polish.
- Config note: exact active limits should be entered from Google AI Studio.

## 2. Groq

- Strengths: very low latency, useful free developer limits, OpenAI-compatible API.
- API style: OpenAI-compatible.
- Quota mode: RPM/RPD/TPM/TPD, model-specific, header-reconciled.
- Router use: fast draft, fast refine, JSON extraction, code helper passes.
- Config note: parse `x-ratelimit-*` and `retry-after`.

## 3. Cerebras

- Strengths: fast inference for larger models.
- API style: OpenAI-compatible.
- Quota mode: RPM/TPM/TPH/TPD token bucket limits.
- Router use: high-priority refinement, code review, reasoning passes when RPM budget is available.
- Config note: lower RPM can still be valuable for large single requests.

## 4. Mistral AI

- Strengths: chat/code generation, structured output support, strong European provider ecosystem.
- API style: OpenAI-compatible style SDK/API.
- Quota mode: RPS, tokens/minute, tokens/month; exact free limits should be read from console.
- Router use: code refinement, structured generation, medium/large deliverables.
- Config note: monthly quota should be smoothed across the month rather than burned in one day unless explicitly allowed.

## 5. GitHub Models

- Strengths: prototyping, model comparison, developer workflow alignment.
- API style: GitHub/Azure AI Inference style with OpenAI-like clients in many examples.
- Quota mode: model tier limits such as low/high/embedding/special, request and token caps.
- Router use: model comparison, judge pass, code deliverable critique.
- Config note: requires a GitHub token with model access.

## 6. Cloudflare Workers AI

- Strengths: edge-friendly, small tasks, daily free neuron allocation.
- API style: Cloudflare endpoint with OpenAI-compatible routes for selected tasks.
- Quota mode: neurons/day plus task rate limits.
- Router use: small classification, low-risk summaries, edge functions, surplus quota burn-down.
- Config note: estimate neurons, not just tokens.

## 7. Z.AI / Zhipu

- Strengths: GLM Flash models, free model lanes, OpenAI-compatible endpoint.
- API style: OpenAI-compatible.
- Quota mode: free model availability and provider-specific limits.
- Router use: free GLM route for chat/code/JSON where latency is acceptable.
- Config note: keep regional/account availability separate from model capability.

## 8. OpenRouter

- Strengths: model comparison, free `:free` model variants, fallback layer.
- API style: OpenAI-compatible.
- Quota mode: request-limited free usage, plus paid routing if enabled.
- Router use: last cloud fallback before local, or explicit model comparison.
- Config note: configure only allowed free models unless paid mode is explicitly enabled.

## 9. Local LM Studio

- Strengths: privacy, reliability, no external quota, homelab control.
- API style: OpenAI-compatible `/v1` server.
- Quota mode: local capacity, concurrency, latency, loaded model inventory.
- Router use: privacy-sensitive work, cheap first drafts, offline fallback, batch jobs.
- Config note: benchmark endpoints across Tailscale/LAN and keep loaded-model inventory current.

## 10. Capability tags

Use normalized capability tags for routing:

```yaml
capabilities:
  - chat
  - responses
  - completions
  - embeddings
  - code
  - reasoning
  - json
  - tool_calling
  - streaming
  - vision
  - audio
  - long_context
  - low_latency
  - local_only
```
