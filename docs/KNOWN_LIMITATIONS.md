# TokenPak — Known Limitations & When NOT to Use

An honest list of what is not yet at production quality in the open-source
release, and where TokenPak is not the right fit today. Current as of the
**v1.17.0 release candidate**; publication remains a separate gate. Each entry
names a **retirement condition** — the concrete signal that removes it.

## Activation does not unlock Pro features yet

- **What this affects:** `tokenpak activate <key>` and any Pro-gated feature.
- **Intentional vs. bug:** intentional fail-safe.
- **Current behavior:** `activate` validates the *shape* of a license key and stores it with status `pending_validation`; cryptographic signature verification ships with the separate Pro package, so until that runs with a production key, Pro features stay locked and entitlements remain Free. The CLI never claims Pro access on a key it cannot verify.
- **Workaround:** none needed for open-source use — every OSS feature works without activation.
- **Retirement condition:** the Pro signature verifier ships with a production key and `activate` reports a verified entitlement.

## Spend Guard requires explicit configuration and an eligible request

- **What this affects:** budget / spend caps on routes that do not reach the
  configured guard or do not provide enough request information to estimate.
- **Intentional vs. bug:** intentional fail-safe boundary; unavailable guard
  state must not be presented as an enforced block.
- **Current behavior:** the open-source Spend Guard can block configured
  runaway requests before provider send. When the guard is unavailable or a
  route is ineligible, it reports that state rather than fabricating a block.
- **Workaround:** verify the local guard with `tokenpak doctor`, configure the
  applicable cap, then retry an eligible request through the local proxy.
- **Retirement condition:** every supported route has explicit, verified guard
  coverage or its unsupported state is removed from the supported surface.

## `tokenpak setup` has rough edges — prefer `tokenpak home init`

- **What this affects:** first-run setup.
- **Intentional vs. bug:** known rough edge (convergence in progress).
- **Current behavior:** the `setup` flow has more than one code path and can behave inconsistently in some edge cases.
- **Workaround:** use `tokenpak home init` — the cleaner path for establishing the canonical home directory.
- **Retirement condition:** the setup flow converges to a single implementation and `setup` is the recommended entry point.

## Some `doctor` messages still print the legacy path string

- **What this affects:** cosmetic diagnostic output in `tokenpak doctor`.
- **Intentional vs. bug:** cosmetic bug (display only).
- **Current behavior:** several diagnostic messages still print the legacy `~/.tokenpak/` path even when the canonical `~/.tpk/` home is in use. Actual path *resolution* honors the canonical home — only the printed strings lag.
- **Workaround:** trust `tokenpak home path` for the active home; a legacy string in a message does not change behavior.
- **Retirement condition:** the diagnostic strings are migrated to the canonical home path.

## Pro Cloud, marketplace, and additional-language SDKs are not shipped

- **What this affects:** expectations about roadmap surface.
- **Intentional vs. bug:** intentional (roadmap, not current scope).
- **Current behavior:** these are roadmap items, not present in the current release.
- **Workaround:** use the shipped Python package and local proxy.
- **Retirement condition:** each ships and is documented as available.

## When NOT to use TokenPak

TokenPak earns trust by being honest about non-fit. It is **not** the right choice today when:

- **There is nothing to measure or reuse** — a pure byte-pass path (no repeated context to pack, no eligible savings) won't show savings; TokenPak reports "unknown" rather than a fabricated percentage.
- **You need universal spend enforcement across unsupported routes** — the OSS
  Spend Guard can block configured, eligible requests before provider send. It
  cannot fabricate a block when the guard is unavailable or a route is
  ineligible, so verify coverage for the route you need to enforce.
- **You need at-rest encryption of local stores** — the open-source build does not encrypt local cache or telemetry at rest, and plugins run in-process (it is not a sandbox).
- **You want a managed cloud service** — TokenPak runs locally on your machine, loopback-only; there is no hosted offering in this release.

## Reporting issues

Please include:

1. `tokenpak --version`
2. Platform (`uname -a` on Linux/macOS)
3. The command and its full output
4. `tokenpak home path` (shows which home is active)
5. `tokenpak doctor --conformance --json` (if conformance-related)
