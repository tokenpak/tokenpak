# TokenPak Security Architecture

## Overview

TokenPak acts as a **secure proxy** between AI agents and LLM providers.
Every request flows through a controlled pipeline with authentication,
authorisation, auditing, and budget enforcement at each layer.

```
Agent → [Auth] → [RBAC] → [Budget] → [Router] → [Proxy] → [LLM Provider]
                                                      ↓
                                               [Audit Log]
```

---

## Authentication

| Layer | Mechanism | Details |
|-------|-----------|---------|
| Agent → Proxy | API key (Bearer token) | Per-agent keys, rotatable |
| Proxy → Provider | Provider API keys | Stored in env vars, never logged |
| Team Server | Invite token + API key | Scoped to team |
| SSO (optional) | SAML 2.0 / OIDC | Configured per deployment |

API keys are **never** stored in plaintext in the audit log or telemetry.

---

## Authorisation (RBAC)

| Role | Capabilities |
|------|-------------|
| Admin | Full access: user management, config, audit export |
| FinOps | Cost data, budget management, compliance reports |
| Engineer | Model routing, recipes, proxy config |
| Auditor | Read-only: audit log, compliance reports |
| ReadOnly | View-only: telemetry, usage stats |

Role enforcement is applied on all API endpoints before any business logic executes.

---

## Audit Logging

The enterprise audit log provides **immutable, tamper-evident** records:

- **Who**: `user_id`, `agent_id`, `source_ip`
- **What**: `action` (proxy_request, auth_failure, config_change, ...)
- **When**: `ts` (Unix epoch), `ts_iso` (UTC ISO-8601)
- **Which model**: `model`, `provider`
- **Which data class**: `data_class` (public / internal / confidential / restricted)

### Tamper Detection

Each row carries:
- `entry_hash` — SHA-256 of the row's key fields
- `prev_hash` — hash of the previous row (chain link)

To verify: `tokenpak audit verify`

### Storage

- SQLite with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=FULL`
- WAL mode ensures durability without blocking reads
- No UPDATE or DELETE except retention pruning

---

## Data in Transit

- All external endpoints served over **TLS 1.2+**
- Proxy-to-provider connections use HTTPS (enforced)
- Internal-only deployments can use HTTP within a trusted network segment

---

## Data at Rest

| Data | Storage | Encryption |
|------|---------|------------|
| Audit log | SQLite (WAL) | Filesystem-level encryption recommended |
| Telemetry | SQLite (WAL) | Filesystem-level encryption recommended |
| Provider API keys | Environment variables | Never persisted to disk by TokenPak |
| Recipes/config | YAML files | Filesystem permissions |

For regulated environments, use filesystem encryption (LUKS, dm-crypt, or cloud-native disk encryption).

---

## Network Security

### Recommended Firewall Rules

```
# Inbound
ALLOW  TCP 8766  from agent_subnet   # TokenPak proxy port
DENY   TCP 8766  from 0.0.0.0/0     # Block public access (put behind ingress/LB)

# Outbound (standard deployment)
ALLOW  TCP 443   to 0.0.0.0/0       # LLM provider HTTPS
DENY   ALL                            # Deny everything else

# Air-gapped deployment
DENY   ALL outbound                  # No internet
```

### Secrets Management

- Use environment variables or a secrets manager (Vault, AWS Secrets Manager, Kubernetes Secrets)
- Never commit API keys to git
- Rotate keys on a schedule: `tokenpak doctor` will warn if keys are stale

---

## Incident Response

1. **Detect** — Monitor audit log for auth_failure spikes, anomalous model usage
2. **Contain** — Revoke the compromised API key: remove from env, restart proxy
3. **Investigate** — `tokenpak audit list --since <date> --user <compromised_id>`
4. **Report** — `tokenpak compliance report --standard gdpr` for breach assessment
5. **Recover** — Rotate all keys, verify chain integrity, resume operations

---

## Vulnerability Management

- Run `tokenpak doctor --fix` for automated security checks
- Monitor upstream advisories for Python dependencies
- CI pipeline runs `safety check` and `bandit` on every commit

---

## Compliance Mapping

See [compliance-mapping.md](compliance-mapping.md) for a full control-to-feature mapping.
