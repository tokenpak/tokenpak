# Security Policy

## Reporting a Vulnerability
If you discover a security issue, please email **security@tokenpak.dev**. Do not open a public issue.

## Responsible Disclosure
We follow a 90‑day disclosure timeline:
1. Report received
2. Acknowledge within 48 hours
3. Patch targeted within 30 days
4. Public disclosure 90 days after report

## Supported Versions
- v1.0.x — security updates
- v0.9.x — critical fixes only
- Earlier — unsupported

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

