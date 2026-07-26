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
| 1.15.x (latest minor) | ✅ Security fixes |
| < 1.15 | ❌ Unsupported — please upgrade |

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

## Advisories in Integrations You Install Yourself

TokenPak's dependency graph carries neither package below. This note exists because a path we
document still leads to one, and removing a disclosure whose subject still affects our users would
be concealment rather than cleanup.

### If you install `crewai` alongside TokenPak

TokenPak previously offered a `crewai` extra. It was removed because crewai requires
`chromadb~=1.1.0`, and every published chromadb 1.x is covered by CVE-2026-45829
(pre-authentication code injection) with **no fixed release available upstream**.

**Installing crewai yourself brings exactly the same package.** The advisory left TokenPak's
lockfile; it did not stop existing. What changed is that it is now your dependency and your choice,
made with this information rather than inherited silently from us.

What it does and does not mean:

- TokenPak itself never imports or runs chromadb, and the CrewAI adapter under
  `tokenpak/sdk/crewai/` is context and handoff wrappers with no vector-store code path.
- The exposure is inherent to running chromadb, typically as a server. If your crewai configuration
  does not run one, the advisory has no attack surface in your deployment.
- Nobody can currently pin around it, us included — the constraint is crewai's and there is no fixed
  version to move to.

We will update this note when a fixed chromadb is published and crewai's constraint permits it. Note
the advisory range is bounded at `<= 1.5.9`, so a release outside that range is not by itself
evidence of a fix.

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
