# TokenPak — Frequently Asked Questions

## General

### Is TokenPak production-ready?

Yes. TokenPak is used in production by multiple teams, with built-in failover, error recovery, streaming support, and comprehensive monitoring. We maintain 99.5% uptime SLAs on our public infrastructure, and self-hosted instances achieve similar reliability. All core features are stable; we don't mark major versions until they've been battle-tested.

### Is TokenPak free?

Yes, TokenPak is 100% open-source (MIT/Apache licensed) and free to use. The OSS version includes all core features: provider routing, failover, cost tracking, streaming, and caching. We offer a commercial Pro tier for advanced features (dashboards, team management, audit logs), but you never pay for basic functionality.

### What's the catch? Why is it free?

We believe building a better LLM infrastructure benefits everyone. The OSS version generates network effects and community trust; the Pro tier monetizes advanced use cases (enterprises, dashboards, audit trails) without restricting core functionality. This is how sustainable open-source projects work.

### What providers does TokenPak support?

**Fully supported:**
- Anthropic Claude (all models)
- OpenAI GPT-4, GPT-3.5
- Google Gemini
- Meta Llama (via Replicate or Hugging Face)
- Local Ollama

**Easy to add:** Any REST-compatible LLM API. TokenPak's adapter pattern makes adding custom providers straightforward—see the [adapters guide](adapters.md).

---

## How It Works

### How does TokenPak route requests to providers?

You define a routing strategy in `proxy.yaml`:

```yaml
routing:
  primary: anthropic    # Default provider
  fallback: openai      # Backup if primary fails
  strategies:
    - provider: anthropic
      models: ["claude-3-*"]
    - provider: openai
      models: ["gpt-4", "gpt-3.5-turbo"]
```

TokenPak matches the requested model to a provider and routes there. If the provider fails, it automatically tries fallbacks. No code changes needed.

### Does TokenPak support streaming?

Yes, completely. TokenPak proxies Server-Sent Events (SSE) from providers without buffering. Your streaming requests work exactly as if you called the provider directly—you get chunks in real-time with full backpressure handling.

### How does caching work? Will I get stale responses?

TokenPak caches responses based on request hashing (model + prompt). Cache hits have a configurable TTL (default 1 hour), and you can disable caching per-request via headers. It's useful for repeated queries or batch processing, but not suited for live/dynamic content. For chat conversations, disable caching or use short TTLs.

### What about token counting? Is it accurate?

TokenPak uses native token counters for each provider (Anthropic's `token-counter`, OpenAI's `tiktoken`). We don't approximate—you get exact counts. For unsupported providers, we use a fallback estimator (~4 chars per token), which you can override.

---

## Security & Privacy

### Is my data stored? Is it encrypted?

**Self-hosted version:** Your data never leaves your infrastructure. TokenPak runs on your machine or server and only talks to the provider's API. No external logging, no analytics, no data storage. Responses are only cached in-memory (configurable TTL).

**Pro SaaS (future):** Data is encrypted in transit (TLS) and at rest (AES-256). We don't train on user data, and you can request deletion anytime.

### How does rate limiting work?

TokenPak supports multiple rate-limiting strategies:

- **Per-provider:** Respects each provider's rate limits (e.g., Claude's RPM limits)
- **Per-key:** Limits by API key (useful for multi-tenant setups)
- **Per-user:** Limits by user ID (requires middleware integration)

Limits are configurable in `proxy.yaml`. You get clear error messages when limits are exceeded.

### Can I audit requests for compliance?

Yes. TokenPak logs all requests (model, prompt hash, response length, cost, latency) to local files and stdout. For the Pro tier, we offer centralized audit logs with filtering and export. Self-hosted users can integrate their own logging backend via webhooks.

---

## Performance & Operations

### What's the performance overhead?

**Proxy internals:** TokenPak adds **<2ms of latency** per request for routing, token counting, and cache lookup.

**End-to-end latency:** When measured against direct API calls, the proxy adds ~**280ms (50%) overhead** due to network round-trip, request serialization, and connection pooling differences. This is expected for any network proxy.

**Context:** The latency overhead is *acceptable* because:
- Token savings (10–40% cost reduction) dwarf the latency cost
- Cache hits (common in production) eliminate latency overhead entirely
- Compression batching improves throughput for batch/async workloads
- For interactive latency-sensitive apps, run the proxy on the same network/machine as your app

For applications where sub-millisecond response time is critical, either self-host TokenPak on the same machine as your client, or use the SDK mode (no network overhead) with a direct API key.

### Can I self-host TokenPak?

Yes, it's designed for self-hosting. You can run it via:
- **pip:** `pip install tokenpak && tokenpak serve`
- **Docker:** `docker run -p 8766:8766 tokenpak/tokenpak`
- **Kubernetes:** Helm charts and manifests are in the repo

See the [installation guide](installation.md) for deployment options.

### How do I monitor TokenPak?

TokenPak exposes Prometheus metrics (`/metrics` endpoint):
- Request count, latency, error rates
- Token usage by model and provider
- Cache hit/miss rates
- Provider health status

You can scrape this in Prometheus, Datadog, or any metrics platform. Logs are JSON-formatted for easy parsing.

### What if a provider goes down? How does failover work?

TokenPak automatically detects provider failures via health checks and circuit breakers. When a provider is unhealthy, it routes to the fallback provider (no user action needed). Once the primary provider recovers, routing resumes. You can also manually force a provider state via the API.

---

## Customization & Integration

### How do I add a custom LLM provider?

TokenPak uses an adapter pattern. See the [adapters guide](adapters.md) for a full guide, but the quick version:

1. Create an adapter class inheriting from `BaseAdapter`
2. Implement `send_request()` and `count_tokens()` methods
3. Register it in `config.yaml`

Full example with a local Ollama instance is in the docs.

### Can I use TokenPak with my favorite SDK (LangChain, LiteLLM, etc.)?

Yes. TokenPak is a drop-in replacement for the OpenAI API. Change your SDK's base URL to `http://localhost:8766/v1` and your API key to any value (it's not validated by the proxy—providers validate). Works with LangChain, LlamaIndex, Autogen, and any OpenAI-compatible SDK.

### Can I modify requests/responses in-flight?

Yes, via middleware. TokenPak supports request and response hooks:

```python
def log_request(request):
    print(f"Model: {request.model}, Tokens: {request.tokens}")
    return request

def log_response(response):
    print(f"Cost: ${response.cost}")
    return response
```

See the [error handling guide](error-handling.md) for examples of request/response hooks and custom logic.

---

## Pricing & Business

### Is there a SaaS/cloud version?

Not yet, but we're building one. For now, TokenPak is self-hosted only. If you'd prefer managed infrastructure, sign up for our [waitlist](https://forms.gle/tokenpak-saas).

### How does TokenPak calculate costs?

TokenPak tracks input and output tokens and multiplies by provider pricing. Pricing is updated daily from provider public pricing pages. You can also configure custom rates in `proxy.yaml` (useful for negotiated enterprise pricing). Costs are logged per request and rolled up hourly.

### Can I set a budget/cost limit?

Yes. You can configure hard cost limits in `proxy.yaml`:

```yaml
cost_control:
  daily_limit_usd: 100
  per_request_limit_usd: 10
```

Requests exceeding limits are rejected with clear error messages.

---

## Support & Community

### Where do I report bugs?

[GitHub Issues](https://github.com/kaywhy331/tokenpak/issues). Include your OS, Python version, TokenPak version, and reproduction steps. We prioritize crashes and regressions.

### How do I request features?

[GitHub Discussions](https://github.com/kaywhy331/tokenpak/discussions) for ideas, or [Issues](https://github.com/kaywhy331/tokenpak/issues) if you have a detailed spec. We review requests weekly and prioritize based on community interest and alignment with our roadmap.

### How do I contribute?

We welcome bug fixes, docs, adapters, and tests. No need to ask permission—fork, make your change, and open a PR. Good first issues are labeled [`good-first-issue`](https://github.com/tokenpak/tokenpak/labels/good-first-issue).

### Is there a Slack/Discord community?

We're using GitHub Discussions for now, which is lower-friction than chat. If the community asks for Slack, we'll set it up. Reach out in [Discussions](https://github.com/kaywhy331/tokenpak/discussions) if you'd like to chat!
