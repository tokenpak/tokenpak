# Security Policy

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please email **security@tokenpak.dev** with:
- A clear description of the vulnerability
- Steps to reproduce it
- Affected versions
- Any suggested mitigations

We will acknowledge receipt within **48 hours** and aim to release a patch within **30 days**.

## Responsible Disclosure

We follow a **90-day disclosure timeline**:

1. Report received → acknowledged within 48 hours
2. We investigate and develop a fix (target: 30 days)
3. Patch released to affected versions
4. Public disclosure after 90 days from initial report (or after patch is widely adopted)

We credit researchers who report vulnerabilities responsibly.

## Supported Versions

| Version | Security Updates |
|---------|-----------------|
| 0.1.x   | ✅ Active support |
| < 0.1   | ❌ No support    |

## Known Issues & Mitigations

### shell=True in subprocess calls (HIGH – by design)

Several internal components execute user-defined shell commands via `subprocess.run(..., shell=True)`. This is intentional for macro/trigger execution, but means:

- **Do not run TokenPak with untrusted macro/trigger configurations**
- Macro steps and trigger commands execute with the **same permissions as the TokenPak process**
- Review all `.yaml` macro configs and trigger definitions before enabling them

Affected files: `agent/cli/trigger_cmd.py`, `agent/macros/engine.py`, `agent/macros/hooks.py`, `agent/macros/premade_macros.py`, `agent/triggers/daemon.py`, `cli.py`

### Flask debug=True in operational API

`tokenpak/telemetry/operational/api.py` has `debug=True` in the `__main__` block. This is **dev-only**. In production, always use gunicorn or another WSGI server:

```bash
gunicorn tokenpak.telemetry.operational.api:app -b 0.0.0.0:5001
```

Never start the operational API with `python -m tokenpak.telemetry.operational.api` in production.

### MD5 for cache key truncation

`tokenpak/telemetry/cache.py` uses MD5 for cache key truncation (not for security/cryptography). This is acceptable — MD5 is used purely for key shortening, not authentication.

## Best Practices

### For Users

- Keep TokenPak updated to the latest patch version
- **Never commit `.env` files** containing API keys
- Use environment variables for all secrets (see `.env.example`)
- Run TokenPak with the least-privileged user account possible
- Review macro/trigger configs before enabling them — they execute shell commands
- If using the portal, run behind a reverse proxy (nginx/caddy) with TLS

### For Contributors

- **Never commit secrets, API keys, or tokens** — not even test keys
- Use `git secret` or pre-commit hooks to catch accidental secret commits
- All external API calls should use environment variables for credentials
- Validate and sanitize all user-provided inputs before use in file paths or commands
- Error messages must not leak secrets, stack traces with sensitive data, or internal paths
- Use `hashlib.sha256` instead of MD5 for any new security-relevant hashing
- Prefer `subprocess.run(cmd_list, shell=False)` over `shell=True` when possible
- Run `bandit -r tokenpak/ -ll` before submitting PRs and address HIGH severity findings

## Security Tools

To run a local security audit:

```bash
# Dependency vulnerability audit
pip install pip-audit
pip-audit -r requirements.txt
pip-audit -r portal/requirements.txt

# Static security analysis
pip install bandit
bandit -r tokenpak/ -ll

# Secret scanning (if git-secrets installed)
git secrets --scan
```
