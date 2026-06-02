# Security Policy

## Reporting a Vulnerability
If you discover a security issue, please email **hello@tokenpak.ai**. Do not open a public issue.

## Responsible Disclosure
We follow a 90‑day disclosure timeline:
1. Report received
2. Acknowledge within 48 hours
3. Patch targeted within 30 days
4. Public disclosure 90 days after report

## Supported Versions
TokenPak is in Beta. Security updates target the latest released version (currently the v1.7.x line). Earlier versions are unsupported — upgrade to the latest release.

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

