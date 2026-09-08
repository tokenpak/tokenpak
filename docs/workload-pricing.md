# Workload pricing

Token prices can depend on input length, service tier, modality, region and cache
lifetime. `PricingContext` and `RateBand` represent those conditions. A band uses
USD per million tokens, an inclusive lower input bound and an exclusive upper
bound. Selection requires a uniquely most-specific match; overlapping conditions
that cannot be ordered raise an error.

## Verified quotes

Use `tokenpak.models.quote_workload` for decisions that require verified rates.
It requires the exact provider and model, measured input count, input/output
modalities, service tier, region, cache lifetime, timezone-aware observation time
and an explicit maximum price age. It returns an immutable quote with the full
input/output/cache-read/cache-write tuple, source metadata, expiry and a digest
binding those values to the workload.

The function raises `PricingQuoteUnavailable` for unsupported conditions,
inferred models, aliases, ambiguous bands, incomplete cache prices, missing
provenance, future verification or expired prices. It does not select a scalar
fallback. Callers must obtain workload facts from their actual request and use
their current observation time; a quote does not establish cache-hit eligibility,
token-count accuracy, account access or non-token tool charges.

The bundled verified bands cover direct, global, standard text requests for the
listed Claude models at 300- and 3,600-second cache lifetimes, and GPT-5.6 Sol at
1,800 seconds. Sol's full rate tuple changes above 272,000 input tokens. Its
promotional bands conservatively expire at `2026-11-21T00:00:00Z` unless refreshed.
Other regions, service tiers, modalities and model identifiers require their own
verified bands. Sources: [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing),
[Sol pricing](https://developers.openai.com/api/docs/models/gpt-5.6-sol) and
[OpenAI cache lifetime](https://developers.openai.com/api/docs/guides/prompt-caching).

## Telemetry compatibility

Existing scalar registry lookups retain their compatibility behavior. Contextual
registry and telemetry lookups can fall back to scalar rates when no band matches;
use `quote_workload` when fallback is unacceptable.

`CostEngine` keeps its public `Pricing` and `add_pricing` rates in **USD per
thousand tokens**, converting selected bands explicitly. `calculate` resolves
baseline and final input lengths independently from one versioned database row,
including output prices when compression crosses a threshold. Its cache-read and
cache-write arguments describe disjoint subsets of the total final input.
Known cache rates are charged; an absent cache rate uses the ordinary input rate.
That compatibility estimate does not establish an unknown cache discount or write
surcharge. Negative counts are clamped and cache subsets cannot exceed final input.

The additive database columns preserve existing price rows. New databases receive
the bundled bands. Existing databases receive them only through an explicit
versioned seed-refresh plan; initialization does not replace their pricing history.
Plans and receipts hash the bands, cache rates and schema. A change invalidates
stale plans. Lookups observe committed changes from other processes without a
restart. Reprocessing honors an explicitly selected pricing version.

[Request workload observations](request-workload-observations.md) can supply
supported facts from the proxy's actual final request and response. Observation
availability and quote availability are separate checks; neither supplies target
seed measurement or recommendation policy.
