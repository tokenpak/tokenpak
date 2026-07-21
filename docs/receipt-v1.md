# Receipt v1 — the request proof object

Receipt v1 is the canonical, inspectable **proof object** for a single TokenPak
request. It answers, for one request: where did it go, what did it cost, what
context was reused vs. dropped, what did the spend guard decide, what did
TokenPak optimize, and where is the debug trail — and it does so *honestly*:
every field is either proven or explicitly marked unavailable.

It lives in `tokenpak/proxy/spend_guard/receipt.py` and is a pure **projection**
over proof TokenPak already records — primarily the per-request `requests` row
surfaced by `tokenpak.cli.request_explorer`. It introduces no new store and
makes no network/LLM call.

## The honesty contract

Every datum is a `ProofField`:

```json
{ "available": true,  "value": 0.018 }          // proven
{ "available": false, "reason": "cost_not_recorded" }   // explicitly unavailable
```

A field the runtime cannot observe is rendered as `available: false` with a
machine `reason` — **never** a missing key and **never** a fabricated `0`. A
genuine `0.0` that was actually recorded stays `available: true, value: 0.0`,
distinct from "we don't know".

## Schema (`schema_version: "receipt.v1"`)

| Block | Fields | Proves |
|---|---|---|
| _identity_ | `receipt_id`, `schema_version`, `created_at`, `request_id`, `status` | stable, addressable receipt |
| `route` | `provider`, `model`, `endpoint`, `request_type` | C10 — where the request went |
| `cost` | `input_tokens`, `output_tokens`, `estimated_cost_usd` | C03 — what it cost |
| `context` | `cache_read_tokens`, `cache_creation_tokens`, `included`, `dropped` | C06 / C21 — reuse + include/drop |
| `spend_guard` | `decision`, `reason`, `requires_approval`, `threshold_hit` | the pre-send verdict |
| `optimization` | `would_have_saved_tokens`, `methods` | C11 — proof of optimization |
| `debug_pointer` | `present`, `trace_id`, `capture_mode`, (`path`) | C18 — where the debug trail is |
| `trail` | `session_id`, `agent_id`, `cycle_id`, `dispatch_job_id` | C10 / C21 — who/what produced it |

`receipt_id` is stable: derived as `rcpt_<request_id>` so the same request always
yields the same receipt id.

### Redaction

`render_receipt(receipt, redact=True)` (the default) is support-safe by
construction: the receipt never embeds request/response plaintext. The debug
`path` (which reveals the OS user's home directory) is dropped under redaction;
`trace_id` + `capture_mode` are retained so support can still locate the
encrypted/hash-only capture without leaking the filesystem layout.

## Building a receipt

```python
from tokenpak.proxy.spend_guard.receipt import build_request_receipt, render_receipt
from tokenpak.cli.request_explorer import get_request_by_id

row = get_request_by_id("42")              # a monitor requests row (or None)
receipt = build_request_receipt(row)       # projection — no fabrication
print(render_receipt(receipt))             # redaction-safe JSON
```

On the pre-send path (before any monitor row exists), pass the spend-guard
`PreflightDecision` instead — its attached `RiskEstimate` backfills the model and
projected cost, and its verdict populates the `spend_guard` block:

```python
receipt = build_request_receipt(None, decision=preflight_decision)
```

## Viewing a receipt

```bash
tokenpak debug receipt <request_id>        # redaction-safe receipt JSON
tokenpak debug receipt <request_id> --raw  # without redaction
```

The live `tokenpak debug receipt <id>` command renders a redaction-safe receipt
for a recorded request, or a support-bundle pointer when none is found. It is
backed by `tokenpak.cli.commands.debug._render_request_receipt(request_id)`. The
`request_id` is optional — invoking `tokenpak debug receipt` with no id prints
the support-bundle pointer.

## What this moves toward score 5

| Claim | Before | With Receipt v1 |
|---|---|---|
| C03 Unclear AI costs | cost scattered across logs | one object with tokens + `estimated_cost_usd`, or an explicit reason it's unknown |
| C06 Context overload | reuse invisible | observed cache reuse on every receipt; include/drop slot ready |
| C10 No clear request trail | no addressable record | stable `receipt_id` + route + session/agent/cycle/job trail |
| C11 No proof of optimization | savings asserted | `would_have_saved_tokens` carried per request, or explicit-unavailable |
| C18 Hard debugging | no pointer | redaction-safe `debug_pointer` to the encrypted/hash capture |
| C21 Hidden repetition | invisible across agents | `cache_read_tokens` + `agent_id` expose cross-agent reuse |

### Known gaps (honest, not score 5 yet)

- `context.included` / `context.dropped` are `context_selection_not_captured`
  until the context-selection (recall-preview) proof is threaded through — the
  slot exists and is populated when callers supply it.
- `optimization.methods` is `optimization_methods_not_recorded` until per-method
  attribution is wired.
