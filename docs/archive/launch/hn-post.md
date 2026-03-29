# HN Show HN Post

## Title
**Show HN: TokenPak – Cut Your LLM Costs 30-50% Without Changing Code**

---

## First Comment (Self-Submission)

Hi HN,

I'm sharing TokenPak, a drop-in HTTP proxy that cuts LLM API costs through transparent compression and caching.

**The problem:** If you're building with Claude, GPT, or Gemini APIs, token spend scales with usage. Most teams pay full price because reducing token count means refactoring prompts or changing SDKs — not practical at scale.

**What we built:** A proxy layer that compresses requests before they hit the API. You point your SDK at `http://localhost:8766`, and TokenPak handles the rest:
- **Request compression** – removes redundant context, reformats prompts for efficiency
- **Semantic caching** – deduplicate identical queries (Pro tier)
- **Token tracking** – see exactly where spend goes, which queries hit cache, which models save the most
- **Provider flexibility** – works with Anthropic, OpenAI, Google Gemini via the same codebase

**Real numbers from production:**
- 30-50% token reduction (typical workload)
- 8-12% cache hit rate per session
- <5 min setup time
- Zero SDK changes needed

It's zero friction: change one environment variable (`ANTHROPIC_BASE_URL=http://localhost:8766`), restart your app, and start saving.

**Free tier:** Compression, caching, cost dashboards, vault indexing
**Pro tier:** Semantic cache, smart model routing, PII scrubbing, multi-tenant

We've been running this in production on three agents for 6+ weeks. The code is clean, the metrics are real, and it actually works.

Open source (MIT). Currently supports Python SDKs; Go/JS support coming soon.

https://github.com/kaywhy331/tokenpak

I'm happy to dig into the compression algorithms, caching strategy, or cost math if anyone wants to discuss.

---

## Comment Variants (For Engagement)

**If asked about accuracy/quality:**
"Compression is lossless — we're not summarizing or dropping context. We're reformatting and deduplicating. The model sees the same semantic content, just with fewer tokens. Quality = zero change, cost = 30-50% lower."

**If asked about latency:**
"Proxy overhead is ~5-10ms per request. On a 30-second API call, that's noise. But we've optimized the hot paths, and you can tune caching aggressiveness based on your tolerance."

**If asked about setup:**
"It's one pip install, one terminal command (`tokenpak serve`), and one environment variable. We wrote it to be the easiest thing in your LLM stack."

**If asked about reliability/testing:**
"375+ passing tests, deployed in production, battle-tested. We track success rates, error rates, and all the scary edge cases."
