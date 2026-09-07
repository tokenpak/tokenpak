# Measurement methodology

TokenPak reports observed token and cost data alongside estimates derived from
that data. This page states the baseline, counting method, aggregation, and
main failure mode for each savings metric. It complements the
[token-counting method reconciliation](token-counting-methods.md), which maps
the counting implementations used across the package.

## Metric definitions

The reporting window is selected by the command or API. Unless a surface says
otherwise, these metrics are totals or ratios of totals across that window;
they are not medians or maxima.

| Metric | Baseline | Counting or pricing method | Aggregation | Known failure mode |
|---|---|---|---|---|
| Tokens saved | Input tokens before a product-managed compression step, compared with the tokens sent after that step. | The store uses the count recorded by the request path. Depending on the path, that may be provider-reported usage, `tiktoken`, or a disclosed character/byte estimate. | Sum of non-negative per-request token differences. | Stored request rows contain counts, not the original text, so they cannot be independently recounted later. Mixed estimators or a missing before/after count can make the aggregate incomparable or unavailable. |
| Dollars saved | List-price cost for the recorded baseline tokens, compared with recorded or calculated actual cost after compression and cache use. | The telemetry cost store applies the model rate active for the event. The legacy monitor-backed `savings` view estimates its baseline from observed cost per token and stored compressed/cache-read counts. | Sum of per-request baseline minus actual cost; the monitor-backed view derives one aggregate estimate. Neither is a median or maximum. | A stale model price, unknown model, missing usage, or a mixed-model aggregate rate can make the estimate wrong. Historical cost rows keep their original pricing version rather than being silently recomputed. |
| Cache-attributed savings | Full input price for tokens that would otherwise be sent uncached, compared with the provider's cached-input price. | Cache-token counts come from provider usage fields when present. Cost may use explicit model rates, provider-level multipliers, or fallback estimates. Provider/platform cache savings remain labelled separately and are not credited to TokenPak. | Sum by attribution source; cache hit rate is cache-read tokens divided by total input plus cache-read tokens. | Missing cache usage makes attribution incomplete; it does not establish zero cache activity. Some paths use family/provider/default rates, and the catalog cost path treats an absent cache rate as zero. Availability flags are surface-specific: a numeric estimate does not establish verified model-specific cache pricing. |
| Would-have-saved | Original input-token count compared with the input tokens sent after the request-path optimization. | `would_have_saved` is stored in **tokens**, calculated as the non-negative input-token reduction. Dollar views convert it using the current registry input rate for the recorded model. | Sum of the stored token field; converted dollar values are estimates, not medians or maxima. | Missing before/after counts, a legacy row with uncertain units, or an unknown/stale model rate can invalidate the conversion. The raw token unit must remain visible. |

## What `savings --verify` verifies

`tokenpak savings --verify` does not change the savings figures. The request
stores intentionally do not retain the source prompt text, so a genuine
recount of historical requests is impossible from those stores alone.

The flag instead runs a deterministic pipeline check on a packaged fixture
corpus containing prose, structured data, code, and multilingual text. It
prints:

- the count from TokenPak's UTF-8-bytes-divided-by-four estimator;
- an independent `tiktoken` `cl100k_base` count;
- the absolute difference; and
- the difference relative to the TokenPak estimate.

This comparison detects drift between two counting methods. It is not a claim
that `cl100k_base` matches every provider's tokenizer, and it does not validate
the stored request totals. JSON output includes the same fields under
`verification`, together with a machine-readable scope and a scope note.

The independent tokenizer is optional. If it is unavailable, the command
returns a specific unavailable result and the installation instruction:

```bash
pip install 'tokenpak[tokens]'
```

If the tokenizer is installed but cannot initialize `cl100k_base`, verification
is also reported as unavailable, with a cache/dependency recovery hint rather
than raw exception details. Existing savings output remains available. Offline
verification requires the tokenizer's encoding data to be available in a
readable cache; installing the optional package alone does not establish that
the encoding data is ready.

## Interpreting results

A non-zero divergence is evidence that the methods count the fixture corpus
differently; it is not itself evidence that stored savings are wrong. Review
the request path's recorded counting provenance before comparing it with the
fixture result. Changes to savings math require separate evidence and are not
made by the verification command.
