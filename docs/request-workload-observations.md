# Request workload observations

The proxy can return workload facts for an explicitly named session when its
optional durable accounting is enabled. These facts describe the latest
forwarded request in the configured accounting window. They support later price
resolution; they do not supply a price, invoice amount, target-context token
measurement or reroute recommendation.

Send `POST /tpk/v1/sessions/workload-snapshot` with JSON
`{"session_id":"your-session-id"}` and the existing `X-TPK-Key` app key.
The route requires loopback access and a configured key, rejects browser Origin
requests, and returns non-cacheable JSON. It accepts no caller snapshot, clock,
query parameters or alternative accounting source. Disabled or unavailable
durable accounting returns HTTP 503. The existing guard-snapshot route and its
version 2 response remain compatible.

The response schema is `native-workload-snapshot/1`. It carries the same current
policy, accounting generation and ledger scope as the guard snapshot, plus
`workload_observation`:

| Field | Meaning |
|---|---|
| `available` | The selected request has complete supported facts and matching, eligible accounting. This is not pricing or recommendation eligibility. |
| `reason_codes` | Missing, unsupported, unresolved or contradictory facts. |
| `request_id_sha256` | Digest of the correlated request identifier, or null. |
| `started_at`, `completed_at` | Observed request lifecycle times, or null. |
| `workload` | A `native-request-workload/1` descriptor, or null. |

The descriptor binds a SHA-256 digest of the exact final request bytes to the
requested and response models, input/output modalities, cache lifetime, requested
and assigned service tier/region, and provider token counts. Input tokens include
the separate cache-read and cache-write subsets; these subsets must not be added
again. Missing counters remain missing. A cache directive does not prove a hit.

Initial classification supports direct Anthropic Messages with known text
blocks and local tools. Custom or partner gateways, other providers, beta modes,
unknown priced features, mixed cache lifetimes and incomplete streams remain
unavailable. Assigned tier and region come from response usage. Missing values
are not replaced with standard/global defaults, and a changed response model
does not inherit the requested model's price.

A retry, newer unresolved send, missing ledger correlation, or accounting write
during observation prevents usable evidence. Consumers must check availability,
the exact schema, matching accounting generations and their explicit age policy.
An older observation cannot authorize a later action.
Overlapping sends within the same session remain ambiguous after settlement;
complete budget accounting alone does not establish one current context.

Only bounded metadata is stored with the existing private accounting history.
No request/response body, credential, raw header, URL, filesystem path or another
session's identity is returned. Digests reveal equality and may permit guessing
known candidate values; they are not anonymization. Observation reads do not
create, migrate, prune or change store permissions. Existing history limits and
write-time cleanup apply, and durable accounting remains off by default.
