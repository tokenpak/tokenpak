# Phase D byte-fidelity diff — P-AP-08

**Verdict:** PASS (merge gate open)

## Artifact diffs

```
IDENTICAL         tokenpak_version.txt  sha256=0e48b5f6370efb00
IDENTICAL         pytest_collect_returncode.txt  sha256=9a271f2a916b0b6e
IDENTICAL         tip_conformance_stdout.txt  sha256=0afb626112e75c68
IDENTICAL         tip_conformance_returncode.txt  sha256=9a271f2a916b0b6e
IDENTICAL         agent_proxy_public_symbols.json  identical public-symbol sets (normalized filter)
IDENTICAL         pytest_collect_stdout.txt  superset-OK (added 30 tests, removed 0)
```

## Protocol

Phase A baseline was a structural snapshot (live-traffic byte capture
was infeasible in the capture session — see README.md in this
directory). A pure-relocation migration is required to preserve all
four artifact families bit-for-bit; any DRIFT line blocks merge.

