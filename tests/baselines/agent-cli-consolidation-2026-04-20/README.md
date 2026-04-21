# agent/cli consolidation baseline (2026-04-20)

## Purpose

Frozen pre-migration invariant snapshot for Initiative `2026-04-20-tokenpak-agent-cli-consolidation`. Phase D (P-AC-08) diffs post-migration state against this baseline; zero-byte delta on the invariant set is the merge gate.

## Artifacts

- `public_symbols.json` — public-symbol sets for `tokenpak.agent.cli.*` + `tokenpak.cli.*` (41 modules total)
- `version.txt` — `tokenpak --version` output
- `pytest_collect_stdout.txt` + `_returncode.txt` — full test-set identity
- `tip_conformance_stdout.txt` + `_returncode.txt` — self-conformance verdict
- `help/_root_help.json` — `python3 -m tokenpak --help`
- `help/_help_all.json` — `python3 -m tokenpak help --all` (full command list)
- `help/subcommands.json` — `tokenpak <cmd> --help` for each of 20 discovered subcommands

## Invariants checked in Phase D

1. Public-symbol sets: normalized-filter diff = 0 (shim re-exports preserve canonical surface)
2. pytest collection: post is superset of baseline (new deprecation tests OK; removals block)
3. tip-conformance: byte-identical
4. Help-text per subcommand: byte-identical
5. Version string: byte-identical

## Capture command

    python3 scripts/capture_agent_cli_baseline.py
