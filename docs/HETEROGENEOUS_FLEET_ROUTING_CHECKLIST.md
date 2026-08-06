# Heterogeneous routing validation checklist

- [ ] The AssistX runtime projection signature and checksum validate.
- [ ] The projection contains only approved loaded runtimes and models.
- [ ] Signed `routing_roles` and `worker_mode` fields survive parsing.
- [ ] Signed task-family benchmark scores survive parsing.
- [ ] `auto/summarize` selects the summarization profile.
- [ ] `auto/compress` selects the compression profile.
- [ ] `auto/extract` selects the extraction profile.
- [ ] An auxiliary-only node is never selected for coding.
- [ ] A measured quality-floor pass ranks ahead of unmeasured candidates.
- [ ] An unmeasured eligible candidate ranks ahead of a measured quality-floor
      failure.
- [ ] Existing live load, LRU, health, and private access-path selection remain
      active after benchmark ordering.
- [ ] Observer-only Tailscale peers appear in context but not provider capacity.
- [ ] The context projection contains more than two tailnet nodes on the intended
      deployment.
- [ ] Stale claims and expired runtime projections still fail closed.
