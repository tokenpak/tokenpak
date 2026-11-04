---
title: Media estimates and context approvals
rung: 2
audience: Developers using Spend Guard in coding agent sessions
updated: 2026-11-04
status: current
---

# Media estimates and context approvals

For developers using long coding agent sessions, Spend Guard estimates images
from their pixel dimensions and uses the previous
completed request's provider usage as an input floor. This reduces false blocks
from large image encodings while keeping the context hard stop in place.

## Images and documents

Images count wherever they occur: as top-level content, inside Anthropic
`tool_result` content, or as Responses `input_image` blocks. Repeated images
count each time. Encoded byte length never determines an image's token count.

The proxy reads bounded PNG, JPEG, GIF and WebP headers without decoding pixels.
It does not fetch URLs or file IDs. If dimensions are unavailable, it uses the
selected model and detail level's maximum image estimate.

The formulas below follow provider documentation retrieved on November 3, 2026:

- [Anthropic vision](https://platform.claude.com/docs/en/build-with-claude/vision)
  and [vision coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates):
  `ceil(width / 28) * ceil(height / 28)` after aspect-preserving resizing.
  Models before Claude 4.7 use a 1,568-pixel edge limit and 1,568-token cap.
  Claude 4.7 and later use a 2,576-pixel edge limit and 4,784-token cap.
  Unrecognized Claude versions use the larger cap.
- [OpenAI images and vision](https://developers.openai.com/api/docs/guides/images-vision):
  patch models use 32-pixel patches, the documented model/detail resize budget,
  and a model multiplier rounded up to whole tokens. For `gpt-6-astra`, `high`
  uses at most 2,500 patches, a 65,535-pixel edge limit, and a 1.2 multiplier:
  at most 3,000 tokens. `auto` and `original` use the documented 30,000-patch
  acceptance ceiling, or 36,000 tokens. `low` fits within 512 pixels.
  Other supported families use their published limits in `media.py`.
  Tile models resize within 2,048 pixels and then a 768-pixel short edge,
  without enlarging smaller images; cost is base tokens plus the number of
  512-pixel tiles times tile tokens. The largest supported tile cap is 48,169
  tokens (`gpt-4o-mini`). Unlisted OpenAI models use that conservative constant.

If patch rounding degenerates for an extremely narrow image, the estimate uses
the selected cap. The caps bound an estimate; they do not certify that an image
meets a provider's upload or dimension requirements.

PDFs use 3,000 text tokens plus one maximum-cost page image per page. Anthropic's
[PDF guide](https://platform.claude.com/docs/en/build-with-claude/pdf-support)
describes typical text costs of 1,500–3,000 tokens per page, plus image tokens.
This is a conservative estimate, not an exact PDF tokenizer.

The built-in PDF reader recognizes page-tree counts in complete, unencrypted
PDFs with uncompressed objects, within a 32 MiB decode limit. It does not render
pages or parse compressed object streams. Unreadable PDFs, URLs and file IDs
use a documented local fallback of 600 pages, the largest page limit in the
Anthropic guide. That fallback can block a request until its document is
available in a supported form. Plain-text document sources count as text.

Thinking and redacted-thinking content also count. Other text, including code
and tool JSON, uses a character heuristic and may undercount without a usage
anchor. Estimates are not a substitute for measured provider usage or bills.

## Observed usage anchors

For an explicit session ID and the same model, the next projection is:

```text
max(structural input estimate,
    previous provider total input + structural estimate of appended content)
```

Anthropic total input includes input, cache-read and cache-creation tokens.
OpenAI input totals already include cached tokens. Only successful, complete
responses establish anchors; failed responses and incomplete streams do not.

The process keeps at most 256 anchors containing token totals, history length,
a history digest, overhead count and request start time. It retains no request
text or media in this cache. A delayed completion cannot replace a newer
request's anchor. Restarting the proxy clears the cache; it warms on subsequent
completed requests. Existing held-request storage remains separate.

Matching history prefixes identify appended content without storing history.
Cache-control markers and their placement do not affect that match. Moving a
marker never adds the full history to the previous usage again.
If history is rewritten or compacted, the conservative fallback adds the whole
current structural estimate to the last observed input. It can overestimate
until another completed response establishes a new anchor. Responses requests
using `previous_response_id` add their supplied input as the suffix.

## Approvals during a hold

Every approval, including a bare `yes`, re-checks the request against current
limits before sending. A one-request approval lifts the context-percent soft
block, the token soft band used when the model's window is unknown, and the
opt-in `block_cost_usd` soft band. Context and token hard stops,
`hard_block_cost_usd`, the session cap and rolling caps remain binding. These
unliftable blocks return `requires_approval: false`, offer no approval prompt
and store no hold. An estimator error blocks the request before provider send.

Put a TIP directive at the start of the first text segment in the **latest user
turn**. This applies to Anthropic messages and Responses input.

- `yes` approves one unexpired held request. The proxy replays its original
  bytes. It does not create a grant for subsequent requests.
- `[TIP: allow=once]` also replays one unexpired held request. If a different
  approval scope cannot release a soft band, the original hold remains;
  another hold is not added.
- A turn containing only tool results, `<task-notification>` or
  `<system-reminder>` content repeats the original block message and keeps the
  hold. It does not count as a human approval or an ambiguous reply.
- After the hold expires, a positive reply can approve the current request
  when its projected input is no more than the expired projection plus 10% of
  the model's window. Expired request bytes are never replayed. Larger growth
  creates a new hold explaining that the earlier hold expired. Retrying the
  rejected late reply cannot approve that replacement hold.
- `[TIP: allow=15m]` grants context-percent approval for 15 minutes.
  `[TIP: allow=session]` grants it for the explicit session ID. These grants
  persist in the existing guard database and are scoped to session, model,
  fleet and agent. They bypass only the context-percent soft block; hard stops,
  other token bands, dollar limits, session limits and rolling caps still apply.
- Optional `max=Nk_tokens` or `max=$X` limits constrain a persistent grant. The
  dollar allowance is reduced by estimated cost when the grant is used.
  `[TIP: cancel]` or a negative response to a hold revokes the scoped grant.

Session grants require a distinct session ID for each new conversation. A
grant survives a proxy restart and ends when revoked or its limit is exhausted;
the proxy cannot infer when a client has closed a conversation.

Holds belong to their session, model, fleet and agent scope. A hold in another
scope does not block the current request. After expiry, new text supersedes
the hold and receives a fresh evaluation; `no` or `[TIP: cancel]` clears it.
Late approvals still obey the growth boundary and all current caps. Background
turns in the same scope continue to re-show the held response.
