# TokenPak Roadmap

> **Transparency:** This roadmap reflects current priorities and may shift. Timelines are estimates, not guarantees.  
> **Last updated:** 2026-03-06 (v1.0 launch day)  
> **Vote on features:** [GitHub Discussions →](https://github.com/kaywhy331/tokenpak/discussions)

---

## Philosophy
- Optimize for measurable token savings
- Keep compression auditable and deterministic
- Prioritize developer experience and documentation
- Community feedback shapes prioritization — features with 5+ votes go to v1.2 minimum

---

## v1.1 (April–May 2026)
**Theme: Security hardening + developer ergonomics**

### Security
- [ ] **Fix `query_preview`** — replace 200-char prompt snippet in routing ledger with short hash (privacy improvement)
- [ ] **DB permission enforcement** — new installs auto-create `.db` files at `0o600`

### Performance
- [ ] **20% faster compression** — profile + optimize hot path in `Compiler.compile()`
- [ ] **Compression benchmarks** — publish baseline + v1.1 comparison

### API Ergonomics
- [ ] **`tokenpak.compress()` shorthand** — one-liner API for 80% of use cases
- [ ] **`TokenPakSession` context manager** — simplify multi-turn conversation handling
- [ ] **Async support** — `compress_async()` and `Session.send_async()`

### Documentation
- [ ] **FAQ expansion** — 4 new entries: compression ratios, routing decisions, SDK migration, LangChain
- [ ] **Routing decisions guide** — explain complexity scoring formula transparently
- [ ] **Migration guide** — step-by-step from direct OpenAI SDK to TokenPak proxy

---

## v1.2 (June–July 2026)
**Theme: Multi-language + streaming + advanced compression**

### Multi-Language SDKs
- [ ] **TypeScript SDK** — `npm install tokenpak-js` — full API parity with Python
- [ ] **Go SDK** — `github.com/kaywhy331/tokenpak-go` — compress, route, cache

### Streaming
- [ ] **Streaming compression API** — real-time token reduction as content generates
- [ ] **Async streaming** — `async for chunk in session.stream("...")`

### Advanced Compression
- [ ] **Context-aware heuristics** — detect code vs prose vs conversation; apply optimal recipe
- [ ] **Near-duplicate block detection** — improve cache hit rate for similar (not identical) prompts
- [ ] **PII masking** — `redact_pii=True` flag; masks before compression, transparent on response

### Community-Driven (TBD — update 2026-03-13)
- [ ] *Feature from community with 5+ votes — TBD*
- [ ] *Feature from community with 5+ votes — TBD*
- [ ] *Bug fixes from community reports — TBD*

---

## v2.0 (Q4 2026)
**Theme: TokenPak as platform infrastructure**

- [ ] **Web dashboard** — GUI for routing policy and registry management
- [ ] **Distributed compression** — multi-node shared registry for team deployments
- [ ] **Enterprise auth** — OAuth2 / API key management for team server
- [ ] **PostgreSQL backend** — migrate from SQLite for multi-user scalability
- [ ] **GraphQL API** — only if registry query complexity warrants it

---

## Backlog (Community Requests)
- Rust SDK
- Offline registry replication
- Redaction rules (PII masking) ← moved to v1.2
- IDE plugins (VSCode/JetBrains)
- Optional prompt-level A/B testing

---

## Not Planned (For Now)
- Closed-source server-only offering
- Proprietary model-specific lock-in
- Storing user prompts on any remote server

---

## Roadmap Rules
- Items may move between releases
- Community feedback shapes priority — vote in [GitHub Discussions](https://github.com/kaywhy331/tokenpak/discussions)
- Monthly roadmap updates posted in Discussions
- Features with 5+ community votes get v1.2 minimum priority
- Security fixes ship in the next patch regardless of planned release

---

*See the [v1.1 planning doc](vault/Projects/tokenpak-oss/docs/ROADMAP-V2-PLAN.md) for full rationale.*
