# CLI qualification harness (local, offline at runtime)

Client closure is selected from `uv.lock` (official PyPI, wheel hashes) and installed with
`pip install --require-hashes --only-binary=:all: --no-deps -r requirements-client.lock.txt`.
Pinned wheels are installed with `--no-deps --no-index`. `run-matrix.sh A|B` runs real CLI
subprocesses with outbound sockets and key generation blocked (`guard_sitecustomize.py` as
`sitecustomize` on PYTHONPATH) and a synthetic license sentinel hashed before/after.
Paths in the script are the 2026-10-03 lane's; edit `W` to reuse.

Finding: `tokenpak activate <shape-valid unverifiable key>` is store-and-stage (exit 0,
status pending_validation) and replaces an existing license.json. Only shape failures
(empty, <16 chars, bad charset, placeholder) exit 1 and leave bytes unchanged.

Phase C runs `compress --verbose` on stdin (real offline OSS operation, paid absent). The harness now flags
exit 2 as a usage error. Limits: the JSON sentinel does not prove a valid signed license is preserved, and no
signature-failure path exists in the OSS CLI without the Pro daemon; staged activation is not a signature failure.
Server environment qualification was not run.
