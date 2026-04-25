# TokenPak 1.1.0 — TIP-1.0 protocol + canonical layout + MCP companion

*Released 2026-04-21*

TokenPak 1.1.0 is a foundational release that establishes the **TokenPak Integration Protocol (TIP-1.0)** — a local-first semantic protocol for LLM compression, caching, routing, and telemetry — and lands the package-layout + pipeline-backbone work that TIP-1.0 is a reference implementation of.

## Highlights

### TIP-1.0 protocol (new)

Canonical wire headers, manifest schemas, telemetry event schema, profiles, and conformance suite. The `tokenpak` proxy is the reference implementation. End users don't need anything extra — `pip install tokenpak` still gives you the full proxy.

Third-party adopters (adapter / plugin / alternate proxy implementers): `pip install tokenpak-tip-validator==0.1.0` for the separate conformance-testing package.

Protocol docs: https://docs.tokenpak.ai/protocol/

### Proxy + CLI consolidation

The code tree now exactly matches the architecture docs: 18 canonical subsystems under `tokenpak/`, nothing scattered across `agent/`. Two atomic consolidations landed in this release:

- **agent/proxy/* → proxy/***: ~10,594 LOC, 25 files; byte-fidelity gate passed 6/6 artifacts
- **agent/cli/* → cli/***: 7,345 LOC, 38 files; byte-fidelity gate passed 7/7 artifacts including per-subcommand help-text identity

Every legacy import path is preserved via a DeprecationWarning re-export shim (removal target TIP-2.0). Upgrading from 1.0.3 is drop-in.

### Services backbone + MCP control plane

- `tokenpak.services` is now the shared execution backbone with a proper `PipelineContext` + `Stage` protocol
- `tokenpak.services.mcp_bridge` hosts all MCP state/protocol machinery; companion + SDK are clients of the bridge
- `tokenpak.sdk.mcp` gives you the MCP client + server-bridge with TIP-label validation

### 20+ new CLI commands

Try `tokenpak help --all` for the complete list, grouped by concern.

## Install

```bash
pip install tokenpak==1.1.0
tokenpak start
```

## Upgrade from 1.0.3

Drop-in. DeprecationWarning shims preserve every legacy import path through TIP-2.0. See [`CHANGELOG.md`](https://github.com/tokenpak/tokenpak/blob/main/CHANGELOG.md#110---2026-04-21) for the full migration notes.

## Architecture + operational docs

- Protocol: https://docs.tokenpak.ai/protocol/
- Quickstart: https://docs.tokenpak.ai/quickstart/
- Canonical §1 subsystem layout: Architecture Standard §1 in the internal docs

## Acknowledgments

The TIP-1.0 scaffold work consolidated ~3 months of internal iteration into a single coherent release. Every legacy import path stays functional through TIP-2.0.
