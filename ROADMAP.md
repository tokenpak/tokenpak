# TokenPak Roadmap

This document outlines the planned development of TokenPak.

## Current Release: v0.1.0 (OSS)

✅ **Shipped:**
- Deterministic compression engine
- Multi-mode compression (hybrid, aggressive, minimal)
- Vault context injection (BM25)
- CANON deduplication
- Prompt caching integration
- CLI tools (`tokenpak compress`, `tokenpak cost`, `tokenpak doctor`)
- Local telemetry and monitoring
- HTTP proxy server
- systemd service support
- MIT licensed

---

## Q2 2026: v0.2.0 (OSS) + Pro Beta

### OSS Improvements
- [ ] Performance optimization (10x faster compression)
- [ ] Plugin system for custom compressors
- [ ] WebSocket support
- [ ] OpenTelemetry export
- [ ] Docker official image

### Pro Beta (Invite Only)
- [ ] Cloud dashboard (read-only)
- [ ] Usage analytics
- [ ] Cost tracking across projects
- [ ] Early adopter pricing ($49/mo)

---

## Q3 2026: v1.0.0 (OSS) + Pro GA

### OSS v1.0
- [ ] Stable API (no breaking changes)
- [ ] Multi-provider support (Anthropic, OpenAI, Google, Mistral)
- [ ] Streaming compression
- [ ] Batch processing mode
- [ ] Comprehensive test suite

### Pro General Availability
- [ ] Full cloud dashboard
- [ ] Managed proxy service
- [ ] API key management
- [ ] Team invites (preview)
- [ ] Stripe billing

---

## Q4 2026: Team Edition

### Team Features
- [ ] Multi-user workspaces
- [ ] Role-based access control
- [ ] Shared dashboards
- [ ] Team billing and invoicing
- [ ] Audit logs
- [ ] Slack/Discord integrations

---

## 2027: Enterprise Edition

### Enterprise Features
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

- **OSS:** Monthly releases (patch), quarterly features (minor)
- **Pro/Team:** Continuous deployment
- **Enterprise:** Quarterly releases with LTS option

## Versioning

We follow [Semantic Versioning](https://semver.org/):
- MAJOR: Breaking API changes
- MINOR: New features, backward compatible
- PATCH: Bug fixes, backward compatible

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for how to get involved in development.
