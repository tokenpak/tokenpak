# TokenPak Documentation

Welcome to the TokenPak docs. Start here, then dive into specifics.

> **This directory holds the documentation that ships with the source
> code.** It is built with the `mkdocs.yml` at the repo root and checked in CI
> (`mkdocs build --strict`, link/orphan-page audits, CLI-doc parity checks)
> alongside the code changes it describes. The public site,
> `docs.tokenpak.ai`, is built and deployed from the separate
> `tokenpak/docs` repository, whose pages are updated by pull requests at
> release time (see the release log under `release-log/` for the per-release
> convergence record). No workflow copies this directory into that repository,
> so a page here can differ from the published page, and a change to one needs
> its own edit to the other.

## Quick Start

| Guide | Description |
|-------|-------------|
| [Getting Started](getting-started.md) | Install and run in 5 minutes |
| [CLI Reference](cli-reference.md) | All commands and flags |
| [API Reference](API_REFERENCE.md) | Python API for programmatic use |

## Operations

| Guide | Description |
|-------|-------------|
| [Deployment](DEPLOYMENT.md) | Production deployment, systemd, Docker |
| [Troubleshooting](troubleshooting.md) | **Symptom-first problem solving — find your fix in 60 seconds** |
| [Error Codes](errors.md) | Full error code reference (TP-Exxx) |

## Trust & Editions

| Guide | Description |
|-------|-------------|
| [MultiPak / Pro boundary](multipak.md) | Current OSS surface and Pro daemon boundary |
| [Security architecture](guides/enterprise/security-architecture.md) | Deployment controls, credential handling, audit-log model |
| [Compliance mapping](guides/enterprise/compliance-mapping.md) | SOC 2, GDPR, and CCPA control mapping for report surfaces |
| [Known limitations](KNOWN_LIMITATIONS.md) | Current Pro and activation limitations |

## Architecture

| Guide | Description |
|-------|-------------|
| [Architecture](architecture.md) | System design and compression pipeline |
| [Compression](compression.md) | How compression works, modes, recipes |
| [Cache System](cache.md) | LRU cache, vault registry, change detection |
| [Telemetry](telemetry.md) | Cost tracking, privacy model, data retention |

## Full Index

See [index.md](index.md) for the complete documentation index.
