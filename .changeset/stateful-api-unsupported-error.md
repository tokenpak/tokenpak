---
---

Proxy: add a typed unsupported-stateful-provider-API error surface.

Adds the `stateful_api_unsupported` error code, status constant, registry-link
constant, and payload builder to the public API snapshot. These are additive
symbols for callers that return structured remediation when TokenPak explicitly
does not support a provider-managed stateful API surface. Route handlers retain
responsibility for selecting the HTTP status.

No public symbol is removed.
