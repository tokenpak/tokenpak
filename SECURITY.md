# Security Policy

## Reporting a Vulnerability

Please report vulnerabilities privately — never through a public issue, discussion, or pull request.

- **Preferred:** [GitHub private vulnerability reporting](https://github.com/tokenpak/tokenpak/security/advisories/new)
- **Email fallback:** **security@tokenpak.ai**

Include the TokenPak version (`tokenpak --version`), reproduction steps, and your assessment of the impact. We aim to acknowledge reports within **3 calendar days**.

## Scope

**In scope:** the TokenPak code and published artifacts — the `tokenpak` package on PyPI, the local proxy, CLI, and companion components in this repository.

**Out of scope:** third-party model providers and their APIs, your own host and network configuration, and social engineering.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.11.x (latest minor) | ✅ Security fixes |
| < 1.11 | ❌ Unsupported — please upgrade |

TokenPak is in beta: security fixes target the **latest public minor release line** unless a security advisory explicitly extends support to an earlier line. This table is checked at each release.

## Coordinated Disclosure

The timelines below are **targets we work to, not absolute guarantees**:

1. **Acknowledge** within 3 calendar days of receipt.
2. **Triage** — we assign a CVSS v3.1 severity (Critical / High / Medium / Low).
3. **Remediation targets by severity:**
   - **Critical:** mitigation, advisory, or patched release targeted within **7 calendar days**.
   - **High:** remediation targeted within **30 calendar days**.
   - **Medium / Low:** next scheduled release.
4. **Disclosure** — we coordinate a disclosure date with the reporter; the default embargo is **90 days**, or until a fix or mitigation is available, whichever is agreed.

Fixes for Medium+ issues are published as GitHub Security Advisories with a CVE requested, and reporters are credited unless they decline. Security fixes are never patched silently.

## Researcher Safe Harbor

We welcome good-faith security research and will not pursue legal action against researchers who:

- test only against their own TokenPak installation;
- do not run denial-of-service, mass-targeting, or data-destruction tests;
- do not access, modify, or exfiltrate data that isn't theirs;
- report promptly through the channels above and allow reasonable remediation time before public disclosure.

## Best Practices

### For Users
- Keep TokenPak updated
- Treat prompts as sensitive data
- Avoid logging raw prompts or compressed blocks
- Use separate keys for dev/prod

### For Contributors
- Never commit secrets or API keys
- Validate all user inputs
- Use parameterized database access
- Keep dependencies current
