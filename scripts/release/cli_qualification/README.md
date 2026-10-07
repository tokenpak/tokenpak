# CLI qualification harness

Runs the real `tokenpak` CLI as subprocesses against an isolated home, with outbound sockets and key generation blocked, and records each exit code plus a before and after hash of synthetic license files. It contacts no service and creates no key.

## Set up

1. Choose a work directory and export it as `QUAL_WORK`.
2. Create the client environment `$QUAL_WORK/venv-client`. Install the pinned client closure, which is selected from `uv.lock` (official PyPI, wheel hashes):
   `pip install --require-hashes --only-binary=:all: --no-deps -r requirements-client.lock.txt`.
   Then install the wheels under test with `--no-deps --no-index`.
3. Copy `guard_sitecustomize.py` to `$QUAL_WORK/guard/sitecustomize.py`. The script puts that directory on `PYTHONPATH`, so every child process blocks outbound sockets and key generation.
4. Inside `$QUAL_WORK`, create the directories `home/.tokenpak` and `evidence`.

## Run

Run `bash run-matrix.sh A` and `bash run-matrix.sh B`. Each case writes `<case>.out` and `<case>.err` under `evidence/`, appends its exit code to `evidence/matrix.txt` (an exit code of 2 is flagged as a usage error, not a product outcome) and adds the license and binary sentinel hashes to `evidence/sentinels.txt`.

- Phase A: `--version`, `license --json`, `activate` with a synthetic key that passes the shape check, `plan` and `doctor`.
- Phase B: `license --json`, `--version`, `compress --help` and `activate` with a key that is too short.

## What to expect

`tokenpak activate <key>` with a key that passes the shape check stores and stages it: exit 0, status `pending_validation`. It rewrites a license file that is not a current paid license, so the license sentinel's hash changes after phase A. A key that fails the shape check (empty, fewer than 16 characters, characters outside the allowed set, or a placeholder string) exits 1 and leaves the file's bytes unchanged.

## Limits

The sentinel license is not a signed license, so unchanged bytes do not show that a valid signed license is preserved. The OSS CLI has no signature-failure path without the Pro daemon, and a staged activation is not one. The harness does not qualify any server environment.
