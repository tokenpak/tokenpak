# TokenPak Beta 1 — Known Limitations

Honest list of what's not yet at production quality. Read this before
making decisions based on Beta 1 behavior.

## Activation does not unlock Pro yet

`tokenpak activate <key>` validates the *shape* of a license key and
stores it, but the real ed25519 signature verifier ships separately
(via the Pro daemon `tokenpak-paid` package). Until that daemon is
running on your machine with a production public key embedded,
`activate` stores plausibly-shaped keys with status
`pending_validation` and **does not unlock Pro features**. Entitlements
remain Free.

This is intentional fail-safe behavior. The CLI never claims Pro
access on a key it cannot verify.

## External public beta is blocked

The current state is **internal smoke beta only**. External public
beta is gated by:

1. PR #162 / release-gate recovery (target 2026-05-22).
2. Explicit Kevin sign-off on the v1.6.0 MINOR version bump (no
   pre-authorization per `feedback_initiative_completion_versioning`).
3. Production ed25519 public-key rotation in the Pro daemon
   (currently a placeholder).
4. Final Packet J polish + integration smoke pass on the release artifact.

If you received a link to this codebase from outside the internal
tester pool, the public announcement hasn't happened and you should
not redistribute.

## Pro production key is a placeholder

The Pro daemon (`tokenpak-paid`) `license_verifier.py` ships with an
all-zeros placeholder public key. The daemon honestly reports this
via `/v1/health` (`license_key_is_placeholder: true`) and refuses to
treat *any* license as cryptographically verified while the
placeholder is in place. Real production keypair generation +
embedding is tracked as a follow-up packet.

## Spend Guard is warn-only in OSS

Beta 1 OSS Spend Guard surfaces warnings and reports. Hard-stop
enforcement (the "actually block the request" path) is Pro Local
only.

## Setup command is two implementations

`tokenpak setup` has two divergent implementations under the hood
(`cmd_setup` vs `run_setup_cmd`). Convergence is deferred to
post-staging polish to avoid parser-rename collisions during Beta 1.
For Beta 1 use `tokenpak home init` instead — it's the cleaner path
for the canonical home-directory boundary.

## Doctor still references legacy paths in some checks

Several diagnostic messages still print `~/.tokenpak/` even when the
canonical `~/.tpk/` is in use. The actual *resolution* honors the canonical home layout
(via `tokenpak._paths`); the cosmetic strings will migrate in a
follow-on polish pass.

## TokenPak Meeting is parked

The TokenPak Meeting initiative was parked 2026-05-15 and is not
part of Beta 1. Any references you find to it in code or vault
material are out of scope.

## Pro Cloud, marketplace, multi-language SDKs

These are roadmap items, not Beta 1 surface. Don't claim or assume
them.

## Reporting issues

Please include:

1. `tokenpak --version`
2. Platform (`uname -a` on Linux/macOS)
3. The command + full output
4. `tokenpak home path` (shows which home is active)
5. `tokenpak doctor --conformance --json` (if conformance-related)
