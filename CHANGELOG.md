# Changelog

All notable changes to TokenPak are documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **`tokenpak prune` command** — Top-level alias for `tokenpak audit prune`; accepts `--days` (retention window) and `--db` (audit DB path) flags
- **CLI surface consistency test** — `tests/cli/test_help_surface_consistency.py` asserts every command in `tokenpak --help` exits 0 on `<cmd> --help`
- **CrewAI adapter** (`tokenpak/adapters/crewai/`) — `TokenPakContext`, `TokenPakCrewAIHook`, `TokenPakCrew`, `TokenPakHandoff`; install with `pip install tokenpak[crewai]` (CALI-MTC-02)
- **AutoGen adapter** (`tokenpak/adapters/autogen/`) — `TokenPakConversationHook`, `TokenPakAssistant`, `TokenPakGroupChat`, `compress_messages`; install with `pip install tokenpak[autogen]` (CALI-MTC-02)
- **LlamaIndex adapter** (`tokenpak/adapters/llamaindex/`) — `TokenPakSynthesizer`, `TokenPakQueryEngine`, `TokenPakIndex`, `MultiIndexFusion`; install with `pip install tokenpak[llamaindex]` (CALI-MTC-02)
- `pyproject.toml` extras: `[crewai]`, `[autogen]`, `[llamaindex]` (CALI-MTC-02)

### Removed (with replacement)
<!-- CALI-MTC-01: CLI surface cleanup — 8 phantom commands resolved -->

| Removed phantom | Resolution | Canonical replacement |
|---|---|---|
| `tokenpak prune` | Implemented as top-level alias | `tokenpak audit prune` (same `--days`, `--db` flags) |
| `tokenpak list-models` | Removed from docs (was never in `--help`) | `tokenpak models` |
| `tokenpak provider-status` | Removed from docs (was never in `--help`) | `tokenpak status` or `tokenpak doctor` |
| `tokenpak provider-force-health` | Removed from docs (was never in `--help`) | `tokenpak doctor --fix` |
| `tokenpak rebuild-vault-index` | Removed from docs (was never in `--help`) | `tokenpak vault repair` |
| `tokenpak cache-stats` | Removed from docs (was never in `--help`) | `tokenpak stats` |
| `tokenpak list-keys` | Removed from docs (was never in `--help`) | No direct replacement — use provider dashboard |
| `tokenpak proxy --config` | Removed from docs (was never in `--help`) | `tokenpak start` with config at `~/.tokenpak/config.yaml` |

---

## [1.0.2] - 2026-03-25

### 🚀 OSS Launch
- Public OSS launch on GitHub with full CI pipeline.
- Migrated to pyproject.toml packaging standard.
- GitHub Actions CI with matrix testing (Python 3.10–3.13).

### Changed
- Version bumped from 1.0.1 to 1.0.2 for OSS launch.
- Updated packaging to pyproject.toml (replaces legacy setup.py).
- README badges updated to live CI status.

## [1.0.1] - 2026-03-18

### Changed
- Minor stability fixes and dependency updates.
- Improved error handling in fallback routing.

## [1.0.0] - 2026-03-10

### 🚀 Highlights
- Stable provider-agnostic routing across Anthropic, OpenAI, and compatibility paths.
- Layered fallback orchestration for improved reliability under model/provider failures.
- Compression and budgeting pipeline hardened for large-context workloads.

### Added
1. Added provider mirroring aliases for consistent model addressing.
2. Added interleaved fallback chains across provider groups.
3. Added routing controls for deterministic fallback behavior.
4. Added SDK Python recipe examples for common integration patterns.
5. Added expanded CLI surfaces for diagnostics and operator workflows.
6. Added capsule builder stages for compact context packaging.
7. Added Phase7 router integration for structured context handling.
8. Added budget controller hooks for token-limit enforcement.
9. Added dashboard support modules and monitoring endpoints.
10. Added improved integration test coverage for routing behavior.
11. Added docs for deployment, troubleshooting, and migration reference.
12. Added safer startup validation in service and proxy paths.

### Changed
13. Changed default operational model chains to prioritize resilient codex-compatible paths.
14. Changed fallback ordering to reduce hard-failure cascades.
15. Changed CLI behavior to surface richer diagnostics in failure modes.
16. Changed internal packaging flow to improve deterministic outputs.
17. Changed healthcheck behavior for clearer readiness signaling.
18. Changed docs structure to separate architecture, deployment, and operator runbooks.

### Fixed
19. Fixed double-proxy regressions introduced during self-repair iterations.
20. Fixed optional ValidationGate import handling to prevent startup breaks.
21. Fixed compression pipeline API mismatch (`compress()` -> `run()`).
22. Fixed service environment gaps that silently disabled router/capsule stages.
23. Fixed multiple edge-case path mappings for model alias resolution.
24. Fixed formatting/type consistency in newly added SDK examples.

### Performance
25. Improved large-context compaction efficiency in benchmarked runs.
26. Improved fallback recovery time under upstream rate-limit pressure.
27. Improved cache and dedup behavior in repeated prompt structures.

### Docs
28. Updated architecture docs for v1.0 routing and compression design.
29. Updated deployment checklist for current service wiring.
30. Updated troubleshooting docs for rate-limit and proxy-path diagnostics.

### Breaking Changes
- **Routing defaults updated:** custom wrappers that assumed legacy v0.x fallback order must update chain expectations.
- **Service env requirements tightened:** production startup now expects explicit router/capsule-related env toggles.
- **CLI output shape adjusted:** automation parsing CLI text output should use stable fields/options where available.

### Migration Notes (v0.x → v1.0)
- Review fallback chain configuration and map old aliases to new mirrored provider aliases.
- Ensure service environment defines router/capsule feature toggles explicitly.
- Re-run smoke tests for startup, health, routing, and fallback behavior.
- Validate any custom parsing around CLI output before deploying to production.

---

## [0.x] - Legacy
- Pre-1.0 development and experimental iterations.
- No stability guarantees.
- Historical changes retained in git history and prior release artifacts.
