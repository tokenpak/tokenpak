# TokenPak Roadmap

This document outlines the planned development of TokenPak.

## Current Release: v0.1.0

**Shipped:**
- Deterministic compression engine
- Multi-mode compression (hybrid, aggressive, minimal)
- Vault context injection (BM25)
- CANON deduplication
- Prompt caching integration
- CLI tools (`tokenpak compress`, `tokenpak cost`, `tokenpak doctor`)
- Local telemetry and monitoring
- HTTP proxy server
- systemd service support
- Apache-2.0 licensed

---

## Q2 2026: v0.2.0

- [ ] Performance optimization (10x faster compression)
- [ ] Plugin system for custom compressors
- [ ] WebSocket support
- [ ] OpenTelemetry export
- [ ] Docker official image
- [ ] Cloud dashboard
- [ ] Usage analytics
- [ ] Cost tracking across projects

---

## Q3 2026: v1.0.0

- [ ] Stable API (no breaking changes)
- [ ] Multi-provider support (Anthropic, OpenAI, Google, Mistral)
- [ ] Streaming compression
- [ ] Batch processing mode
- [ ] Comprehensive test suite
- [ ] Managed proxy service
- [ ] API key management

---

## Q4 2026

- [ ] Multi-user workspaces
- [ ] Role-based access control
- [ ] Shared dashboards
- [ ] Audit logs
- [ ] Slack/Discord integrations

---

## 2027

- [ ] On-premises deployment
- [ ] SSO/SAML authentication
- [ ] Custom integrations
- [ ] Compliance documentation (SOC2, HIPAA)
- [ ] Dedicated support
- [ ] SLA guarantees

---

## Feature Requests

Have an idea? Open an issue on GitHub with the `feature-request` label.

### Under Consideration
- GraphQL support
- gRPC compression
- Browser extension
- VS Code extension
- Prometheus metrics exporter
- Kubernetes operator

### Not Planned
- Model hosting (out of scope)
- Fine-tuning (out of scope)
- Prompt engineering (separate tool)

---

## Release Cadence

- Monthly releases (patch), quarterly features (minor)

## Versioning

We follow [Semantic Versioning](https://semver.org/):
- MAJOR: Breaking API changes
- MINOR: New features, backward compatible
- PATCH: Bug fixes, backward compatible

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for how to get involved in development.
