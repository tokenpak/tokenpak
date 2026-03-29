# TokenPak Documentation

This directory contains user-facing documentation for TokenPak.

## Quick Start

- **[Installation](./installation.md)** — Get TokenPak running in 5 minutes
- **[Configuration](./configuration.md)** — Set up providers, cache, and logging
- **[Usage Guide](./usage.md)** — Common commands and API examples

## Troubleshooting

**Something broken? [Start here →](./troubleshooting.md)**

Common issues:
- Connection refused → Check port binding
- 401 Unauthorized → Verify API key
- 502 Bad Gateway → Provider error
- 429 Rate Limited → Rate limit hit
- Config won't load → Syntax error
- Docker exits → Check mounts & env vars
- pip install fails → Python version
- High latency → Cache hit rate
- Cost data missing → Token counting
- Logs not showing → Log level

[See full troubleshooting guide](./troubleshooting.md) for all 10+ problem categories with step-by-step fixes.

## Reference

- **[API Reference](./api.md)** — HTTP endpoints and response formats
- **[Error Codes](./error-codes.md)** — Full list of error codes and what they mean

## Advanced

- **[Architecture](./architecture.md)** — How TokenPak works internally
- **[Performance Tuning](./performance.md)** — Cache optimization, rate limiting, compression
- **[Deployment](./deployment.md)** — Docker, systemd, cloud platforms

---

## Need Help?

If you encounter an issue:

1. **Check [Troubleshooting](./troubleshooting.md)** — Find your symptom and follow the fix steps
2. **Search [GitHub Issues](https://github.com/suewu/tokenpak/issues)** — Your problem might already have a solution
3. **File a new issue** — Include error message, config (sanitized), and reproduction steps

---

## Contributing

Found a docs typo or want to improve this guide? Contributions are welcome! 

Submit a pull request or open an issue on GitHub.
