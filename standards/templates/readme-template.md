<!--
READMEs are a trust surface. Readers decide in the first five seconds whether TokenPak is worth their time. Follow this template; deviations pay interest forever.

Applies to: the repo root README.md, package READMEs inside subsystems (optional), satellite-repo READMEs, and example-directory READMEs.

Replace every <angle-bracket> placeholder. Delete this HTML comment before committing.
-->

# <ProjectOrPackage> — <one-line tagline>

[![PyPI version](https://img.shields.io/pypi/v/<package>.svg)](https://pypi.org/project/<package>/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/<package>.svg)](https://pypi.org/project/<package>/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

<One-paragraph elevator. Lead with what it does for the reader, not what it is. Use the canonical one-sentence identity from `00-product-constitution.md §2`. No more than 3 sentences.>

---

## 30-second demo

```bash
pip install <package>
<command-one>                          # <what happens>
<command-two>                          # <what happens>
```

```
<copy-paste the real output block the user will see>
```

Then verify:

```bash
<verification-command>
```

```
<real output block from the verification>
```

---

## Works with

**<Client A>** · **<Client B>** · **<Client C>** · …

<!-- Keep this line tight. Use the exact names from `tokenpak integrate`'s client list — that's the canonical spelling of each. -->

---

## Install

```bash
pip install <package>
```

See [docs/quickstart.md](docs/quickstart.md) for environment setup and per-client configuration.

Requirements: Python 3.10+. <One line on any notable runtime deps, or say "No external dependencies for core functionality.">

---

## What's included

<!-- Bullets. Every bullet is a concrete feature with a verifiable benefit. -->

- **<Feature>** — <one-line description with a number if possible>
- **<Feature>** — <one-line description>
- …

<Optional: one-line call to action with a doc link.>

---

## How it works

<!-- One short paragraph or one small diagram. Enough that a skimmer knows the mental model. Don't duplicate the architecture doc. -->

```text
<Tiny diagram if useful>
```

---

## Documentation

- [Quickstart](docs/quickstart.md) — zero to working in 5 minutes
- [API reference](docs/api-tpk-v1.md) — every public surface
- [Guides](docs/guides/) — how to do specific things
- [Architecture](docs/ARCHITECTURE.md) — why it's built this way
- [Troubleshooting](docs/troubleshooting/) — when something breaks

---

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

<!-- State the license in one sentence. Apache 2.0 is standard for TokenPak. -->

Apache 2.0. See [LICENSE](LICENSE).

<!--
Forbidden in a TokenPak README:
- "Revolutionary," "game-changing," "cutting-edge," etc.
- Emoji fireworks.
- "We're excited to announce…"
- Countdowns, teasers, "coming soon" features.
- Unrunnable code samples.
-->
