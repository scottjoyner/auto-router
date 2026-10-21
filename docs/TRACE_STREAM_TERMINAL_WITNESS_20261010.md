# Auto-router: truthful stream terminal evidence

2026-10-10 | Draft source-only correction. Nothing deployed.

## Problem and scope

Kipnerter device metadata, AssistX trace IDs and router route events cannot independently attest model/GPU execution or user ownership. Auto-router already has stream-lifecycle instrumentation, but its finalizer counted any clean iterator EOF as completed, even if an OpenAI-compatible provider sent only partial SSE content. That could misreport a lost provider stream as a successfully finished generation.

## Narrow implementation

- Completion requires an observed OpenAI SSE [DONE] or Responses API response.completed terminal event; split chunks remain supported.
- HTTP 200 plus EOF without a recognized terminal is router.stream.incomplete, acceptance_state possibly_accepted. This releases pending quota and does not count success. It does not invent token usage.
- Provider response.failed/response.incomplete marks the stream incomplete. A failure event cannot be overridden by a later DONE marker.
- Existing route event outbox gets one bounded terminal_evidence enum: openai_sse_done, responses_completed, responses_failed, responses_incomplete, eof_without_terminal, client_cancelled, or transport_exception.
- No raw prompt/response, identity, credentials, coordinates or headers are added; routing, stream contents, authentication, model loading, live listeners, and databases are unchanged.

## Verification and remaining gaps

Ten focused router lifecycle tests pass, covering missing, split, completed, failed and contradictory terminal markers. Broad tests and exact-SHA GitHub CI must still be reviewed.

This is evidence of protocol terminal markers *observed by the router*, not proof of physical model completion, GPU residency, client delivery or user authentication. Trusted ingress owner identities, admitted server-minted request roots, actual model-instance spans, bounded offline replay, cross-user privacy and a two-user real endpoint experiment remain distinct NO-GO gates for a claimed unified multi-user trace system.

Non-standard streaming protocols need explicit reviewed adapters; they must not silently be considered successful on bare EOF.
