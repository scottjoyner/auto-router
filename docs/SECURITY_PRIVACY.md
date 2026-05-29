# Security and Privacy

## Principles

- Default sensitive or private work to local LM Studio endpoints.
- Do not use the router to evade provider quotas or terms.
- Treat provider API keys as environment-managed secrets only.
- Avoid logging prompts or payloads unless explicitly enabled.

## Operational rules

- Keep local-only routing for requests tagged `local_only` or matching private-data markers.
- Reserve quota before dispatch, but refund failed attempts that never produced a usable response.
- Prefer explicit configuration for providers, worker commands, and base URLs.
- Review any future networked agent worker carefully before allowing write or commit actions.
