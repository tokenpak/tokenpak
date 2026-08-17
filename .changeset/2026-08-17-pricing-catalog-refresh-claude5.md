---
---

Refresh the model pricing catalog for the current Anthropic model
generation: add priced rows and context windows for the newest model
family, and correct a stale legacy pricing row that had been seeded as a
byte-identical copy of an older model's rates. Add a catalog-integrity
test suite that makes this class of staleness CI-detectable going
forward — every shadow-mode target must resolve to explicit priced
catalog data with a known context window, every served model must carry
positive pricing, and every dateless Anthropic catalog entry must resolve
a context window — without hardcoding an enumeration of model names, so
the checks keep working as the catalog grows. Data and test changes only;
no rate-band or cost-estimation logic changed.
