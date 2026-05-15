# TokenPak Beta — Onboarding Guide

Welcome to the TokenPak Beta. This guide walks you from a fresh
machine to a working install with the canonical roundtrip exercised
end-to-end. It assumes only:

- Python 3.10+ (`python --version`)
- `pip` available
- A terminal you're comfortable in

If anything in this guide doesn't work for you, please capture the
output and file a report. Beta means the rough edges are still
visible by design.

## Status

This guide targets the **internal smoke beta** of TokenPak Beta 1.
External / public beta is gated by release-governance items called out
in `KNOWN_LIMITATIONS.md` and the project blocker ledger. If you
received a link to this file from outside the internal tester pool,
the public beta announcement hasn't shipped yet and some features
described below are still being hardened.

---

## 1. Install

```bash
pip install tokenpak
```

This installs the OSS package. No license, no daemon, no network
calls beyond the LLM requests you'd already be making.

Verify the install:

```bash
tokenpak --version
```

Expected: a version like `1.5.6` (or newer).

---

## 2. Initialize your TokenPak home

TokenPak stores its config, license, templates, and Pak store under
a single home directory (Std 33). The canonical location is
`~/.tpk/`. Older installs may have used `~/.tokenpak/` — we'll cover
the migration path in step 3.

Inspect where TokenPak thinks home is:

```bash
tokenpak home path
```

Expected output (fresh install):

```
TokenPak home : /home/you/.tpk
Resolved by   : canonical   (or "default" if directory doesn't exist yet)
Canonical     : /home/you/.tpk          (present: False)
Legacy        : /home/you/.tokenpak     (present: False)
```

If you see `⚠️  Legacy ~/.tokenpak/ is in use`, jump to step 3
before continuing. Otherwise, create the home directory:

```bash
tokenpak home init
```

Expected output:

```
✅ Wrote starter config → /home/you/.tpk/config.json

Next steps:
  • tokenpak home explain  — see every config key
  • tokenpak doctor        — verify the install
```

Inspect what got written:

```bash
tokenpak home explain
```

You'll see every config key, its current value, and where the value
came from (file vs default vs env override).

---

## 3. Migrate from `~/.tokenpak/` (if applicable)

Only run this step if `tokenpak home path` told you the legacy
directory is in use. The migration is **backup-first** — your old
state is left in place untouched.

Dry-run first to see what would happen:

```bash
tokenpak home migrate --dry-run
```

Expected: a list of files that would be copied from `~/.tokenpak/`
to `~/.tpk/`. Review the list. If it looks right, do it:

```bash
tokenpak home migrate
```

The original `~/.tokenpak/` directory is intentionally left in place
as a safety backup. Remove it manually once you've verified the
canonical install works.

---

## 4. Run the doctor

```bash
tokenpak doctor
```

This runs ~20 diagnostic checks across config, vault, cache, and
proxy. On a clean install you should see mostly `✅` with maybe a
couple of `⚠️` (e.g. "no API key configured yet" — expected).

For the protocol-level self-check (TIP integration protocol
conformance):

```bash
tokenpak doctor --conformance
```

Expected: 13 checks pass, verdict `pass`.

You can also run TIP conformance directly with more detail:

```bash
tokenpak tip conformance
```

---

## 5. TIP capability inspection

TokenPak Integration Protocol (TIP) is the open contract layer that
adapter providers and platform integrations declare against. List
every capability label this install knows about:

```bash
tokenpak tip inspect
```

Expected: ~20+ capability labels grouped by family (`tip.cache.*`,
`tip.compression.*`, `tip.pak.*`, etc.).

Validate a single capability label:

```bash
tokenpak tip validate tip.compression.v1
```

Expected: `✅ tip.compression.v1 — valid TIP capability label`.

Validate a JSON file against a TIP schema:

```bash
echo '["tip.compression.v1"]' > /tmp/caps.json
tokenpak tip validate /tmp/caps.json --schema tip-capabilities.v1
```

Expected: `✅ caps.json conforms to tip-capabilities.v1.json`.

---

## 6. PAK roundtrip

A PAK (Portable AI Knowledge) is a portable, JSON-on-disk knowledge
bundle. In Beta 1 OSS, the full `create → inspect → export → import`
roundtrip works without any Pro features.

Create a sample directory and package it as a Pak:

```bash
mkdir -p /tmp/my-context
echo "Project notes" > /tmp/my-context/notes.md
echo "Decisions log" > /tmp/my-context/decisions.md

tokenpak pak create /tmp/my-context \
    --output /tmp/my.pak.json \
    --title "My context" \
    --objective "Onboarding demo"
```

Expected output:

```
✅ Created Pak pak:abc123… → /tmp/my.pak.json
   anchors: 2  skipped: 0  checksum: sha256:abc123…
```

Inspect what you created:

```bash
tokenpak pak inspect /tmp/my.pak.json
```

You should see the title, objective, anchor count, token estimate,
and checksum.

Install the Pak into your local store:

```bash
tokenpak pak import /tmp/my.pak.json
```

Expected: `✅ Imported Pak pak:abc123… → /home/you/.tpk/paks/...`

After import you can inspect by Pak id (no file path needed):

```bash
tokenpak pak inspect pak:abc123…
```

Export back to a fresh directory to verify the roundtrip:

```bash
tokenpak pak export /tmp/my.pak.json --output /tmp/restored
ls /tmp/restored/
# notes.md  decisions.md  pak.json
diff /tmp/my-context/notes.md /tmp/restored/notes.md
# (no output — files are byte-identical)
```

---

## 7. License & features

TokenPak ships as a single binary; the Free tier covers the full
OSS feature set. Pro/Team/Enterprise tiers unlock additional
capabilities via license activation. List what you have today:

```bash
tokenpak plan
tokenpak features
```

The `features` table shows every gated feature with its required
tier and current state (active / locked / etc.).

To activate a paid license:

```bash
tokenpak activate <your-license-key>
```

**Beta 1 note on license validation:** the Beta 1 OSS activate
command performs input validation (rejecting empty, too-short, or
non-printable keys) and stores valid-looking keys with status
`pending_validation`. The full Pro daemon with cryptographic
license verification ships in a follow-on slice. Until then, **any
plausibly-shaped license key stores but does not unlock Pro
features** — entitlements remain Free. This is the intended
fail-safe behavior.

To remove a stored license:

```bash
tokenpak deactivate
```

---

## 8. Routing test

If you've configured routing rules, verify them with:

```bash
tokenpak route test
```

This is useful when you have a mixed-provider setup and want to
confirm requests are landing where you expect.

---

## 9. PAKPlan preview

Once the recall foundation is populated (Pro daemon adds the
capture pipeline; OSS Beta 1 ships the foundation tables), preview
what a PAKPlan would surface:

```bash
tokenpak pakplan preview
tokenpak pakplan report
```

On a fresh install with no captured Paks, expect a friendly "no
recall db on disk yet" message. The foundation is OSS; the scorer
and ranking pipeline are Pro.

---

## 10. Status & report

```bash
tokenpak pak status      # MultiPak readiness summary
tokenpak report          # Savings + usage summary (last 24h)
tokenpak status          # Proxy health
```

`pak status` is designed to be fast (<2 seconds) on a fresh
install even when no daemon is running. If it ever hangs more than
a couple of seconds without a daemon, that's a bug — please report
it.

---

## 11. Troubleshooting

**`pak status` hangs.** It shouldn't — the command is engineered
to fast-fail when no daemon is reachable. If you see >5s of wait,
hit `Ctrl+C` and file a report with your environment details.

**`pak import` says "checksum mismatch".** The Pak file was
modified after creation (or partially downloaded). Re-create or
re-download. The mismatch is deliberate tamper detection.

**`doctor --conformance` reports a FAIL.** That's a real
regression — please file a report with the full output. Beta 1
ships with 13 passing checks; any FAIL is a bug.

**`activate` rejects my key.** The Beta 1 OSS activate validates
input shape. Common rejections:
- Empty / whitespace-only key
- Key shorter than 16 characters
- Key contains non-printable or unusual characters (allowed:
  `A-Z a-z 0-9 . _ / + = -`)
- Placeholder string like `test`, `demo`, `tbd`

**`home migrate` refuses to run.** If `~/.tpk/` already exists,
the command refuses to merge automatically (it's not safe to
overwrite an existing canonical home). Inspect both directories
and either remove `~/.tpk/` (if you didn't want it) or rerun with
`--force` to overlay legacy on top.

**TIP conformance fails on a fresh install.** Run `tokenpak tip
doctor` for the verbose envelope. If schemas are missing, your
install is incomplete — `pip install --force-reinstall tokenpak`
typically resolves it.

---

## 12. What's not in Beta 1

The following features exist in the codebase but are deliberately
not part of the Beta 1 tester surface:

- The Pro daemon (`tokenpak-paid`) with capture pipeline, ranked
  recall, encryption-at-rest, and PAKPlan automation. The OSS-side
  preview / explain / report commands work without it; the Pro
  daemon ships separately.
- Hard-stop Spend Guard enforcement (Beta 1 ships warning-level
  only; hard stops are Pro Local).
- Continuum continuation-PAK automation (roadmap; not shipped).
- Multi-language SDK expansion beyond Python.
- A marketplace / plugin ecosystem.

Don't worry if you don't see these — they're Beta 2+ surface.

---

## 13. Reporting bugs

Please include:

1. `tokenpak --version` output.
2. Your platform (`uname -a` on Linux/macOS).
3. The exact command you ran + the full output.
4. `tokenpak home path` output (so we know which home is active).
5. `tokenpak doctor --conformance --json` output (if relevant).

Beta is the time to surface rough edges — your reports are gold.
