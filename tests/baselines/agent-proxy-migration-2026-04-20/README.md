# agent/proxy → proxy/* migration baseline (2026-04-20)

## Purpose

Frozen pre-migration invariant snapshot. Phase D (P-AP-08) diffs post-migration
state against this baseline. Any diff > 0 is the merge-gate failure signal.

## Constraints at capture

P-AP-01 called for live-traffic byte-fidelity scenarios
(`request.bin`/`response.bin`/`headers.json` per scenario) against a running
`tokenpak serve`. Live provider credentials and an `examples/benchmarks/`
harness were not available in the capture session, so the baseline was
narrowed to three structural invariants that a **pure-relocation migration**
must preserve bit-for-bit:

1. Every public symbol exported by every module under `tokenpak.agent.proxy.*`
   (post-migration, the canonical home is `tokenpak.proxy.*`, but the
   legacy re-export shims under `tokenpak.agent.proxy.*` must re-surface the
   identical symbol set).
2. `pytest --co` collection output (test identity set).
3. `scripts/tip_conformance_check.py` stdout (self-conformance verdict).
4. `python3 -m tokenpak --version`.

If any of the four artifacts diffs post-migration, the relocation was not
pure.

## Capture command

    python3 scripts/capture_agent_proxy_baseline.py

## Artifacts

- `agent_proxy_public_symbols.json` — {module: sorted_public_names}
- `pytest_collect_stdout.txt` + `_returncode.txt` — test identity set
- `tip_conformance_stdout.txt` + `_returncode.txt` — conformance verdict
- `tokenpak_version.txt`

## Capture metadata

- Date: 2026-04-20
- Branch at capture: `feat/agent-proxy-consolidation`
- Parent branch: `feat/tip-1.0-phase-2-scaffold`
- Modules inventoried: 29 (`tokenpak.agent.proxy` + submodules)
- `pytest --co` exit: 0
- `tip-check` exit: 0

## Phase D diff (P-AP-08)

Phase D re-runs `capture_agent_proxy_baseline.py` against post-migration
`tokenpak.agent.proxy.*` (which by then is a re-export shim over the
canonical `tokenpak.proxy.*`) and produces byte-level diffs of all four
artifact types. Zero-diff is the merge gate.
