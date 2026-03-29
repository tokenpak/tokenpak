# TokenPak Documentation Index

**Last updated:** 2026-03-28  
**Total docs:** 35 files (consolidated from 91)

---

## Getting Started

Start here if you're new to TokenPak.

1. **[README.md](README.md)** — Project overview, features, installation at a glance
2. **[getting-started.md](getting-started.md)** — Step-by-step setup guide (local development)
3. **[installation.md](installation.md)** — Installation methods & requirements
4. **[QUICKSTART](getting-started.md)** → consolidated into getting-started.md

---

## Core Concepts

Understand how TokenPak works.

- **[architecture.md](architecture.md)** — System design, components, request flow
- **[features.md](features.md)** — Feature overview & capabilities matrix
- **[COMPONENT-DIAGRAM.md](COMPONENT-DIAGRAM.md)** — Visual architecture diagram
- **[comparison.md](comparison.md)** — How TokenPak compares to alternatives

---

## Configuration & Setup

Configure TokenPak for your use case.

- **[configuration.md](configuration.md)** — Complete config reference (proxy settings, routes, auth)
  - _(Includes: PROXY-CONFIG, CLI_VALIDATE_CONFIG content)_
- **[DOCKER.md](DOCKER.md)** — Docker setup & deployment
- **[demo.md](demo.md)** — Quick demo with examples

---

## API Reference

Integrate TokenPak into your application.

- **[API.md](API.md)** — Complete API reference & SDK methods
  - _(Includes: api-reference stubs, API audit findings)_
- **[EXAMPLES.md](EXAMPLES.md)** — Code examples and patterns

---

## Integration & Compatibility

Integrate with your stack.

- **[COMPATIBILITY.md](COMPATIBILITY.md)** — Compatibility matrix & integrations
  - _(Includes: openai-compatible content)_
- **[adapters.md](adapters.md)** — Adapter ecosystem overview
  - adapters/google.md — Google AI adapter
  - adapters/langchain.md — LangChain integration
  - adapters/litellm.md — LiteLLM compatibility
  - adapters/openai-compat.md — OpenAI-compatible mode

---

## Advanced Topics

Deep dives for production use.

### Performance & Optimization
- **[PERFORMANCE.md](PERFORMANCE.md)** — Performance tuning, caching, benchmarks
  - _(Includes: performance-optimization, performance-index)_
- **[COMPRESSION_TUNING.md](COMPRESSION_TUNING.md)** — Request compression settings
- **[performance/memory-analysis.md](performance/memory-analysis.md)** — Memory profiling

### Reliability & Security
- **[SECURITY.md](SECURITY.md)** — Security model, authentication, best practices
- **[error-handling.md](error-handling.md)** — Error codes, debugging, troubleshooting
  - _(Includes: ERROR_CODES reference)_
- **[troubleshooting.md](troubleshooting.md)** — Common issues & solutions

### Operations & Monitoring
- **[observability.md](observability.md)** — Logging, metrics, tracing
- **[production-sla.md](production-sla.md)** — SLA, uptime guarantees, support
- **[TESTING.md](TESTING.md)** — Testing strategy, test suite, CI/CD
  - _(Includes: testing-integration)_

### Extensibility
- **[plugin-guide.md](plugin-guide.md)** — How to write plugins
- **[plugin-system-architecture.md](plugin-system-architecture.md)** — Plugin system internals

---

## Recipes & Patterns

Real-world use cases and configuration patterns.

- **[recipes/README.md](recipes/README.md)** — Recipe index
  - [Multi-provider fallback](recipes/01-multi-provider-fallback.md)
  - [Budget caps & limits](recipes/02-budget-caps.md)
  - [Per-user rate limiting](recipes/03-per-user-rate-limiting.md)
  - [Model routing by use case](recipes/04-model-routing-by-use-case.md)
  - [Cost monitoring](recipes/05-cost-monitoring.md)
  - [Streaming responses](recipes/06-streaming-responses.md)
  - [Local dev with mocks](recipes/07-local-development-mock.md)

---

## Strategy & Business

High-level strategy and business docs.

- **[SAVINGS.md](SAVINGS.md)** — ROI, cost savings, value prop
- **[OSS-PRO-BOUNDARY.md](OSS-PRO-BOUNDARY.md)** — Open source vs. Pro feature boundary
- **[FAQ.md](FAQ.md)** — Frequently asked questions

---

## Operations

Day-to-day operations and maintenance.

- **[release-checklist.md](release-checklist.md)** — Release process checklist
- ~~COVERAGE.md~~ → archived to `archive/internal/`
- ~~IMPORT_ERROR_INVENTORY.md~~ → archived to `archive/internal/`

---

## Reference Files

Standalone reference materials.

- **[openapi.yaml](openapi.yaml)** — OpenAPI schema
- **[assets/DIAGRAMS.md](assets/DIAGRAMS.md)** — Diagrams and visual assets

---

## Archived Materials

Documentation moved to `archive/` for historical reference:

- **archive/audits/** — Audit reports (adoptability, coverage, fuzzing, security)
- **archive/benchmarks/** — Performance traces and benchmarks
- **archive/planning/** — Planning docs, PRDs, specs, scaling analysis
- **archive/spikes/** — Technical spikes and experiments
- **archive/launch/** — Launch materials (blog posts, positioning)
- **archive/historical/** — Historical issues, old test gaps

See `archive/README.md` for details.

---

## Quick Reference

| Use Case | Start Here |
|----------|-----------|
| Install & try TokenPak | [getting-started.md](getting-started.md) |
| Configure for production | [configuration.md](configuration.md) |
| Integrate into app | [API.md](API.md) |
| Integrate with LangChain | [adapters/langchain.md](adapters/langchain.md) |
| Optimize performance | [PERFORMANCE.md](PERFORMANCE.md) |
| Debug issues | [troubleshooting.md](troubleshooting.md) |
| Understand security | [SECURITY.md](SECURITY.md) |
| Check compatibility | [COMPATIBILITY.md](COMPATIBILITY.md) |
| Run tests | [TESTING.md](TESTING.md) |

---

## Contributing

Want to improve the docs? See [CONTRIBUTING.md](../CONTRIBUTING.md) in the root.

---

**Questions?** Open an issue on [GitHub](https://github.com/kaywhy331/tokenpak).
