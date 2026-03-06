# TokenPak Compliance Mapping

Maps enterprise features to compliance requirements across SOC 2, GDPR, and CCPA.

---

## SOC 2

| Control ID | Name | TokenPak Feature |
|------------|------|-----------------|
| CC1.1 | Integrity & Ethical Values | Audit log, access policies |
| CC2.1 | Internal Communication | Audit log captures all interactions |
| CC3.2 | Fraud Risk | Hash-chained tamper-evident audit log |
| CC5.2 | Control Activities | API key auth, budget enforcement |
| CC6.1 | Logical Access | API key + RBAC on all endpoints |
| CC6.2 | New Access Provisioning | Invite-token agent join, audit recorded |
| CC6.3 | Access Removal | User deactivation, key revocation |
| CC6.7 | Data Transmission | TLS 1.2+ enforced |
| CC7.1 | Vulnerability Management | `tokenpak doctor`, CI security scans |
| CC7.2 | Monitoring | Telemetry pipeline, anomaly detection |
| CC8.1 | Change Management | Git-based config, config_change audit event |
| CC9.1 | Vendor Management | Multi-provider failover, OSS codebase |

**Generate SOC 2 report:** `tokenpak compliance report --standard soc2`

---

## GDPR

| Article | Requirement | TokenPak Feature |
|---------|-------------|-----------------|
| Art. 5(1)(a) | Lawfulness, fairness, transparency | Data class tracking in audit entries |
| Art. 5(1)(b) | Purpose limitation | Routing policies restrict data use |
| Art. 5(1)(c) | Data minimisation | Prompt compression, PII stripping |
| Art. 5(1)(e) | Storage limitation | Configurable retention (default: 90 days) |
| Art. 5(1)(f) | Integrity & confidentiality | WAL + FULL sync, hash chain |
| Art. 12 | Data subject rights | Export by user_id, targeted deletion |
| Art. 25 | Privacy by design | Anonymous metrics mode by default |
| Art. 30 | Records of processing | Audit log is the processing record |
| Art. 32 | Security of processing | TLS, RBAC, encrypted storage |
| Art. 33 | Breach notification | Audit data for forensics; manual notification |

**Generate GDPR report:** `tokenpak compliance report --standard gdpr`

---

## CCPA

| Section | Right | TokenPak Feature |
|---------|-------|-----------------|
| 1798.100 | Right to Know (collection) | `tokenpak audit list --user <id>` |
| 1798.105 | Right to Delete | `tokenpak audit prune` + targeted delete |
| 1798.110 | Right to Know (categories) | `data_class` field in audit entries |
| 1798.115 | Right to Know (disclosure) | `provider` field shows who received data |
| 1798.120 | Right to Opt-Out | Anonymous metrics mode |
| 1798.125 | Non-Discrimination | No differential service for privacy choices |
| 1798.130 | Privacy Notice | `docs/enterprise/privacy-policy.md` template |
| 1798.140 | Personal Information | Audit log tracks all PI interactions |

**Generate CCPA report:** `tokenpak compliance report --standard ccpa`

---

## Quick Reference — CLI Commands

```bash
# Audit log operations
tokenpak audit list --since 2026-01-01 --user alice
tokenpak audit export audit.json --format json --since 2026-01-01
tokenpak audit export audit.csv  --format csv
tokenpak audit verify
tokenpak audit prune --days 90
tokenpak audit summary --since 2026-01-01

# Compliance reports
tokenpak compliance report --standard soc2
tokenpak compliance report --standard gdpr
tokenpak compliance report --standard ccpa
tokenpak compliance report --standard soc2 --output soc2-Q1-2026.json --format json
tokenpak compliance report --standard gdpr --org "Acme Corp" --since 2026-01-01
```

---

## Data Retention Schedule

| Data Type | Default Retention | Configuration |
|-----------|------------------|---------------|
| Audit log entries | 90 days | `audit.retention_days` in config |
| Telemetry events | 90 days | `tokenpak audit prune --days <N>` |
| Compliance reports | Indefinite (saved manually) | Save with `--output` flag |

---

## Evidence Collection for Auditors

```bash
# 1. Export full audit log for audit period
tokenpak audit export audit-period.json --since 2026-01-01 --until 2026-03-31

# 2. Verify tamper-evident chain
tokenpak audit verify

# 3. Generate compliance reports
tokenpak compliance report --standard soc2 --output soc2-2026-Q1.json --format json
tokenpak compliance report --standard gdpr --output gdpr-2026-Q1.json --format json

# 4. Collect system health
tokenpak doctor > system-health.txt
tokenpak audit summary --since 2026-01-01 >> system-health.txt
```
