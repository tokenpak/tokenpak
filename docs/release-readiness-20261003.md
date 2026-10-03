# Release readiness and rollback: local candidate (2026-10-03)

Local, offline, source-level evidence only. Nothing here is deployed, published or pushed, and the deployed Worker revision is unknown.

## Verify

`python3 scripts/release_readiness_check.py --out <dir> --worktree-root <dir>` exports the pinned commits with `git archive`, builds wheels with the local setuptools backend (no network, no build isolation), installs them into a throwaway venv with `--no-index --no-deps`, and writes `candidate-manifest.json` (commits, archive and wheel SHA-256, Requires-Dist, transition log). It uses a throwaway HOME/TMPDIR, never imports the license server, and never creates keys.

## Pinned combination

| Part | Source | Artifact |
|---|---|---|
| OSS candidate | `910923215d` | `final-run/` role path `oss/tokenpak-1.30.1-py3-none-any.whl` sha256 `b2320622f7782468411938c7b2f6f81f3beadfebe94399864fecc2e5abb13693` |
| Paid candidate | `d5e7ceb62a` | `tokenpak_paid-0.5.2-py3-none-any.whl`, sha256 `f60a25dc65e79dec537644f9e999b30ed054c065c74f2b3e03d4736faff31710`, requires `tokenpak>=1.26.0,<=1.30.1` |
| Server candidate | `08def751d6` | not a wheel; deploy from source, separate environment |
| Prior OSS (local comparison rollback input, not evidence of what is released or deployed) | `562fef78ba` | `prior_oss/tokenpak-1.30.1-py3-none-any.whl` sha256 `941c8b1596e872a2e53f97074f8882f6a76e5f77360783d489b923d0f6c0cb56` |

Python >=3.10. Client and server cannot share one environment: server pins `httpx==0.25.2` and `uvicorn==0.24.0`; the client needs `httpx>=0.26` and `uvicorn>=0.27`.

## Stop gates (any one stops the release)

0. Offline dependency closure missing and `tokenpak --version` failing in the venv (`yaml`): release_ready stays false until a complete, locally retained dependency set passes `pip check` and the CLI.
1. Deployed Worker revision, contract and public-key trust alignment unverified.
2. Same-key reissue, account linkage and signer connection unresolved.
3. Private-index 401 unresolved; no package may be published.
4. Purchase/cancellation unverified. Automatic renewal is incomplete.
5. The candidate OSS wheel has the same version string as the released 1.30.1 but different bytes. Pip treats them as identical, and the paid pin `<=1.30.1` blocks simply bumping OSS. This needs a version decision (owner call) before any shipping.
   - Update: the local candidate identity is now OSS 1.30.2 with Pro 0.6.0, see `docs/release-log/v1.30.2.md`. The text above records the earlier state.

## Staged order (config untouched)

> Superseded for the OSS + paid pair: steps 3-4 below install one package at a time and are NOT a supported upgrade path; use the paired-upgrade gate section. They remain only for the OSS-only case and as history.

1. Back up `~/.tokenpak/license.json` (bytes and sha256) and the issuer trust file. Do not copy key material into logs or artifacts.
2. Server (separate environment): deploy the candidate only after gates 1-4 clear.
3. OSS client (OSS-only; not for the pair): `pip install --no-index --find-links <retained dir> --force-reinstall <exact wheel path>`.
4. Paid: (not supported as a separate step; see the paired-upgrade gate.)
5. Diagnostics: `tokenpak --version`, `tokenpak license status`. No issuance or trust rotation is automatic.

## Rollback and OSS fallback

- Remove paid: `pip uninstall -y tokenpak-paid` (proven: package removal, import and sentinel bytes unchanged; OSS runtime fallback after removal was proven on Windows and Linux in the 13-case matrix, but only in a complete full-dependency environment; an incomplete environment (an earlier fresh venv failed on missing yaml) does not prove it).
- Roll back OSS (when paid is not installed): reinstall the retained prior wheel by exact path with `--force-reinstall`. With paid installed, roll back the pair in one pip invocation and re-run the gate against a prior-pair manifest. Select by file hash, never by index version label.
- The script exercised prior -> candidate -> +paid -> -paid -> +paid -> prior -> candidate with a sentinel license file; the sentinel hash was unchanged at every step.
- Limits: rolling OSS back removes the activation-failure and grace-period fixes (`ecd2f76a29`, `910923215d`). Rollback cannot undo issued or revoked licenses or DB changes, and none of that was verified here.

## Proven versus inferred

Proven (this script): both wheels and the prior wheel build offline; metadata and pins; the paid pin admits 1.30.1; transitions preserve sentinel bytes; `import tokenpak, tokenpak_paid` works; no key-like files in the wheels.

Not proven: dependency resolution (no wheels available offline: `pip install --dry-run --no-index` fails on `aiohttp`); the CLI (`tokenpak --version` fails in the venv with `ModuleNotFoundError: yaml`, because deps are absent); signed-token runtime behaviour (covered by prior acceptance, not rerun).

## Guards and checks added after review

Wheels persist under role-specific paths (`oss/`, `paid/`, `prior_oss/`) and each copy's hash is asserted. Smoke processes run with a `sitecustomize` guard that blocks socket connects and asymmetric key generation. After each forced same-version install, the installed `.py` bytes are compared with the wheel. The wheel scan covers member names only, not contents.

## Report semantics

The script exits 0 when the packaging check completes. `candidate-manifest.json` carries `packaging_check_completed: true`, `release_ready: false` and `release_blockers`; automation must read `release_ready`, never the exit code. Wheel bytes are not deterministic across builds (timestamps): retain the exact artifacts and hashes; no reproducible-build claim.

## Server rollback sequence (not performed, source-level only)

Tested tuple: OSS `9109232`, paid `d5e7ceb`, server `08def751` (static pinned review plus earlier acceptance; no upgrade or deploy was run). To roll back the server: stop the service (owner action), restore the previous code checkout, environment file and config from backup, then restart. Do not touch issuer keys, trust files or the database. DB-schema rollback was not validated, and issued or revoked licenses cannot be reversed. Back up signed license bytes and public trust before any step; private key material is never copied into artifacts or logs, and there is no automatic rotation.

## Paired-upgrade gate (fail-closed)

`pip` does not prevent mixed `tokenpak` / `tokenpak-paid` pairs: both new-OSS + old-paid and old-OSS + new-paid install, `pip check` passes, and public version constraints cannot tell same-name/same-version builds apart. The staged one-at-a-time in-place steps above are therefore NOT a supported upgrade path. The only supported path is:

1. The release owner supplies a manifest `{"oss": {name, version, wheel_sha256}, "paid": {...}}` out of band. It must be the exact reviewed pair, never generated from whatever wheels are present; it is the trust root and the gate records its sha256.
2. Prepare in a fresh staging environment (new venv), not in the working one, so a failed or partial pip cannot damage the previous working environment. Run `python scripts/release/paired_upgrade_gate.py check-artifacts --manifest M --wheel-dir D --receipt R`: D must hold exactly the two pinned wheels (missing, single, unknown, extra, or hash-mismatched wheels exit 1). This never reports `ready: true`.
3. Install both exact wheels in ONE pip invocation from D into the staging environment, then run `check-installed` with that staging/target venv's own interpreter, immediately before cutover. It compares every wheel file byte-for-byte with the installed copy, so a same-version no-op or partial install exits 1. It also resolves each wheel's top-level imports (`find_spec`, no application code run) and rejects any that land outside the verified install, such as a `PYTHONPATH` or source-checkout cwd shadow. A distribution outside the interpreter's prefix (another env via `--site-packages`) is never ready. Only this step writes `ready: true`. The checker is read-only except for the receipt.
4. Quiescence is manual and must happen before switching to, or running, the pair against shared state (license/state files): old processes keep running old code. The gate never stops processes and cannot stop older direct writers. No process manager or live action is part of this gate.

Callers must require a SUCCESS exit code AND a current valid receipt (`ready: true`, expected manifest sha256, and `prefix`/`executable`/per-role `wheel_sha256` matching the target venv and approved pair; the receipt records them). Launch only from the verified venv: no `PYTHONPATH`, no source-checkout cwd, no unreviewed editable/`.pth` entries. Later out-of-band modification, including stale extra files, is not detected; stage freshly and make no manual writes.

The gate is available only from the OSS source checkout (a reviewed script artifact), NOT from either wheel. The operator must pin the reviewed gate source commit or file sha256 externally (the handoff supplies it; it is not embedded here). On Windows the license lock times out after 30 s; on POSIX `flock` blocks until released. Neither involves a gate change. The receipt is invalidated at the start of each run; unreadable manifest, IO errors and bad wheels exit 1 with a `ready: false` receipt, and if the receipt cannot be written the run exits 1 and no earlier receipt may be used. A read-only output path cannot be guaranteed overwritable; that case fails. This guard checks release identity only; it is not full release approval.

A failed run leaves the previous working environment and license bytes untouched. Recovery is to discard the staging environment, or `pip uninstall tokenpak-paid` (OSS fallback), or reinstall the previous verified pair in one invocation.

Remaining owner gates (unchanged): a public version decision/distributable pair so the two corrected builds are distinguishable by version, and the existing `release_ready=false` external gates. macOS is untested; Windows/Linux only. The paid README's upgrade paragraph now points at this gate instead of a fixed pairing.
