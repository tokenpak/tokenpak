# Why Zero Data Architecture

*The case for keeping your prompts out of extra cloud intermediaries.*

---

There's a design choice most LLM tooling makes silently: your prompts pass through their servers. Compression services, context managers, cost trackers — they sit in the middle of your conversations, reading what you write before forwarding it on.

TokenPak makes the opposite choice. We never see your prompts.

This post explains what TokenPak means by "zero data architecture," why it matters, and how we built a fully-featured compression proxy without TokenPak's servers touching your content.

---

## The Trust Problem with Cloud Intermediaries

When you use an LLM, you already trust your provider (Anthropic, OpenAI, etc.) with your prompts. That's a deliberate choice — you've evaluated their privacy policy, their data handling, their retention practices.

Adding a cloud-based compression layer means trusting a *second* party with the same data. Now two organizations have seen your code review, your legal draft, your internal architecture discussion. They may have different logging practices, different retention policies, different breach exposure.

Worse: the value proposition of a compression service often requires them to *understand* your content. They need to read your prompts to know what to remove. That understanding requires processing. Processing creates logs. Logs create exposure.

We didn't want to be in that position. And we didn't think our users should have to trust us.

---

## How Local Compression Changes the Equation

TokenPak is a local proxy. It runs on your machine, between your LLM client and the provider. When a request passes through:

1. Your client sends the request to `localhost:8766`
2. TokenPak compresses the prompt locally, using CPU-only algorithms and declarative YAML rules
3. The compressed request is forwarded to your provider with your credentials
4. The response comes back through the proxy (to record token counts locally)
5. No prompt content is sent to TokenPak's servers; only your configured provider receives the request you explicitly made

There's no TokenPak API call. No relay server. No content logged to a remote database. The compression runs in the same Python process as the proxy, on your hardware.

If TokenPak's servers went down tomorrow — or if we shut down entirely — your setup would continue working indefinitely. There's nothing to break.

---

## Zero Credentials: The Other Half of the Trust Model

Beyond content, there's credentials.

Cloud-based proxies often store or relay your API keys. At minimum, they see them in the `Authorization` header. Some store them to enable features like "route to the cheapest provider automatically."

TokenPak's proxy is entirely passthrough on credentials. Your API key arrives in the request header, gets forwarded to your provider unchanged, and is never read, parsed, stored, or logged by our code. It's opaque to us — intentionally.

This is enforced architecturally, not just by policy. The proxy's forwarding logic doesn't decode the `Authorization` header. It passes through a set of allowed headers verbatim. There's no code path that extracts or stores credentials, because we designed the system so that code couldn't exist.

---

## What "Zero Data" Actually Costs You

Nothing useful.

The assumption behind cloud-based compression services is that they need to *understand* content centrally to improve their models. More data → better compression → more value.

TokenPak doesn't use ML models for compression. Our pipeline is:

1. **Segmentize** — split content into typed blocks (code, prose, config, etc.)
2. **Match a recipe** — declarative YAML rules based on block type
3. **Apply operations** — regex, structural transformations, whitespace normalization
4. **Budget** — allocate tokens proportionally across blocks

None of these steps require central data. Recipe quality improves through community contributions (shared YAML files), not through processing your prompts. Our benchmark results come from testing on synthetic datasets, not user traffic.

The tradeoff we accept: TokenPak's compression is explainable and auditable, but it won't win a benchmark against a model trained on millions of real prompts. What you get in exchange: your prompts never train anyone's model, and you can read exactly what we're doing to your content in a YAML file.

---

## Local-First by Default

Zero data architecture isn't just about privacy. It's about resilience.

A local proxy has no rate limits (from us). No outages (from us). No pricing changes (from us). It works on an air-gapped machine. It works on a plane. It works after TokenPak stops existing.

Many TokenPak operations — status checks, vault searches, cost reports, route listings — cost zero tokens because they query local state directly. They don't touch the LLM API at all. This isn't a side effect; it's a design principle. Operations that don't need the LLM shouldn't use it.

The result: a tool that gets cheaper to run the more you use it (compression savings compound over time), that you can audit completely (every recipe is a readable YAML file), and that you own outright (downgrade or fork at any time).

---

## For the Skeptics

**"But what if the recipes are bad at compression?"**

Try them. Every recipe can be tested locally with `tokenpak recipe benchmark`. If a recipe is hurting more than helping, remove it. The recipe set is configurable — you have full control over what runs on your prompts.

**"What if I want centrally-managed compression policies for my team?"**

Deploy a TokenPak team server inside your infrastructure. One instance, your control, your network. Team members point their clients at your server, not ours.

**"Doesn't local compression limit what you can optimize?"**

For commodity patterns (Python comments, JSON whitespace, markdown formatting), deterministic local rules match or exceed what an ML model would do. For domain-specific content (legal contracts, medical records, financial filings), local rules let you tune precisely for your vocabulary without exposing sensitive content.

---

## The Principle

We built TokenPak around a conviction: the tool that handles your LLM traffic should make you *less* exposed, not more.

That means running locally by default. Passing through credentials opaquely. Never logging content. Making every operation inspectable. Giving you a way out (no lock-in, no required cloud subscription).

Zero data architecture isn't a privacy checkbox. It's the foundation that makes every other feature trustworthy.

---

*TokenPak is open-source (Apache-2.0). [Get started](../getting-started.md) or [read the code](https://github.com/tokenpak/tokenpak).*
