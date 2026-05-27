# Dashboard Design

## 1. Purpose

The dashboard is the operator view for quota burn-down, provider health, high-priority deliverable routing, and LM Studio fallback usage.

The MVP should be intentionally small and server-rendered so it works inside the local container without a frontend build chain.

## 2. MVP pages

### `/dashboard`

Sections:

1. Provider health cards.
2. Quota remaining table.
3. Daily burn-down chart placeholder.
4. Recent high-priority jobs.
5. Fallback/circuit-breaker events.
6. Local LM Studio endpoint status.

### `/admin/quota`

JSON endpoint used by dashboard and scripts.

```json
{
  "providers": [
    {
      "provider": "groq",
      "model": "llama-3.1-8b-instant",
      "dimensions": {
        "rpm": {"limit": 30, "used": 4, "remaining": 26},
        "tpd": {"limit": 500000, "used": 12000, "remaining": 488000}
      },
      "reset_at": "2026-05-28T00:00:00Z",
      "health": "ok"
    }
  ]
}
```

### `/health`

Basic service and provider health.

### `/metrics`

Prometheus-compatible metrics in a later phase. MVP can return text counters.

## 3. Dashboard metrics

- `requests_total{provider,model,priority,status}`
- `tokens_total{provider,model,type}`
- `quota_remaining{provider,model,dimension}`
- `quota_burn_percent{provider,model,dimension}`
- `fallback_total{from_provider,to_provider,reason}`
- `circuit_open{provider,model}`
- `latency_ms_bucket{provider,model}`
- `local_only_total`
- `high_priority_refine_total`
- `high_priority_judge_total`

## 4. Visual priorities

The dashboard should quickly answer:

1. What free quota remains today?
2. What will reset soon and may go unused?
3. Which provider is failing or throttled?
4. Are high-priority deliverables getting better treatment?
5. How often are we falling back to LM Studio?
6. Which local endpoint is currently healthiest?

## 5. Future UI improvements

- Burn-down line chart.
- Provider-specific detail pages.
- Manual provider disable/enable.
- Manual quota override.
- LM Studio endpoint benchmark table.
- OpenCode provider config export.
- Queue view for background work waiting for surplus quota.
