# Contributing to TokenPak

Thank you for your interest in contributing! TokenPak is a small, focused project and community contributions matter a lot. Here's everything you need to get started.

---

## Ways to Contribute

- 🐛 **Report a bug** — [Open a bug report](https://github.com/kaywhy331/tokenpak/issues/new?template=bug_report.md)
- 💡 **Request a feature** — [Open a feature request](https://github.com/kaywhy331/tokenpak/issues/new?template=feature_request.md)
- 💬 **Ask a question** — Use [GitHub Discussions](https://github.com/kaywhy331/tokenpak/discussions), not issues
- 📝 **Improve docs** — PRs for typos, outdated examples, and clarifications always welcome
- 🔧 **Submit code** — See PR workflow below

---

## Quick Start

```bash
git clone https://github.com/kaywhy331/tokenpak.git
cd tokenpak
make dev      # create .venv + install tokenpak[dev] in editable mode
make check    # lint + format check + full test suite
```

That's it. See `make help` for all available targets.

## Development Setup

### Prerequisites

- Python 3.10+
- pip
- Git
- GNU Make (pre-installed on macOS and Linux)

### Clone and Install

```bash
# One-command setup (recommended)
make dev

# Or manually:
git clone https://github.com/kaywhy331/tokenpak.git
cd tokenpak
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
tokenpak --version
```

### Makefile Targets

| Target | Description |
|---|---|
| `make dev` | Create `.venv` and install in editable mode |
| `make test` | Run full pytest suite |
| `make test-fast` | Run tests, stop on first failure (`-x`) |
| `make test-cov` | Run tests with HTML coverage report |
| `make lint` | Run ruff linter |
| `make format` | Auto-fix formatting with ruff |
| `make check` | Lint + format check + tests (CI gate) |
| `make build` | Build wheel and sdist into `dist/` |
| `make docs` | Build MkDocs site |
| `make hooks` | Install pre-commit hooks |
| `make clean` | Remove build artifacts and caches |
| `make clean-all` | Remove everything including `.venv` |

### Pre-commit Hooks

```bash
make hooks   # install hooks into .git/hooks/
# Hooks: ruff lint+format, trailing-whitespace, end-of-file-fixer,
#        check-yaml, check-toml, check-json, detect-secrets
```

### Project Structure

```
tokenpak/
├── tokenpak/
│   ├── agent/           # Agent-mode proxy (agentic workflows)
│   ├── core/            # Core compression logic
│   ├── telemetry/       # Usage tracking and analytics
│   └── cli.py           # Command-line interface
├── packages/            # Sub-packages (tokenpak-local, etc.)
├── tests/               # Top-level integration tests
├── docs/                # Documentation
└── recipes/             # Compression recipes
```

---

## Running Tests

```bash
# Quick CI/audit subset — <30 seconds, no live proxy or network required
pytest -m quick

# Fast (local package tests)
pytest packages/tokenpak-local/tests/ -q

# Full suite including slow tests
pytest packages/tokenpak-local/tests/ -q --slow

# With coverage
pytest --cov=tokenpak

# Run a specific file
pytest tests/test_compression.py

# Run tests matching a pattern
pytest -k "test_cache"

# Type check
python3 -m mypy tokenpak/ --ignore-missing-imports
```

### Test Marker Split

| Marker | Purpose | When to use |
|---|---|---|
| *(none)* | Full suite | PRs, release gates |
| `quick` | Fast audit checks (<30s) | Pre-commit, CI fast gate, automated audits |
| `slow` | Long-running or network-dependent | Nightly CI only |
| `integration` | Requires live proxy or external services | Integration testing |
| `chaos` | Fault injection | Stability testing |

All tests must pass before submitting a PR.

---

## Code Style

We use:

- **Black** for formatting
- **Ruff** for linting
- **Type hints** on all public functions
- **Google-style docstrings** for classes and public methods

```bash
# Format
black tokenpak/

# Lint
ruff check tokenpak/

# Check formatting without writing
black --check .
```

---

## Submitting Work for QA (Agent Workflow)

> **Internal agent protocol.** External contributors skip this section.

Before setting task status to `review`, ensure your code is accessible to Sue's QA machine:

1. `cd ~/Projects/tokenpak`
2. `git push shared main` ← **REQUIRED** — Sue's QA cannot see local-only commits
3. `git push origin main` ← push to GitHub too
4. In the task file: add commit hash from `git log --oneline -1`
5. Set `status: review` in the vault task file and push vault

**Why this matters:** The `shared` remote (internal bare repo) is the QA verification path. Commits that only exist locally are invisible during QA review and will cause rejection.

---

## Submitting Changes

1. **Branch from `master`**: `git checkout -b fix/your-fix`
2. **Make focused changes** — one PR per concern
3. **Write tests** for new behavior when practical
4. **Run the full test suite** and confirm it passes
5. **Update [CHANGELOG.md](CHANGELOG.md)** — add your change under `## [Unreleased]` in the correct section (Added / Changed / Fixed / Security). Link to your PR: `[#123](https://github.com/kaywhy331/tokenpak/pull/123)`
6. **Open a PR** with a clear description of what and why
7. **Include** `git log --oneline -1` in your PR description

### Commit Message Format

```
type: brief description

Longer explanation if needed.

Fixes #123
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`

Examples:
- `feat: add Google Gemini failover support`
- `fix: handle empty response in SSE stream`
- `docs: update compression modes documentation`

---

## Response Times

We aim to:
- Acknowledge all issues and PRs within **48 hours**
- Review PRs within **1 week**

---

## Getting Help

- Open a [GitHub Discussion](https://github.com/kaywhy331/tokenpak/discussions)
- Tag @kaywhy331 for blocking issues
- Read the docs under `/docs`

---

## Document Classification

All content in this repository must be **PUBLIC**. Use this table to determine where a file belongs:

| Classification | Where It Lives | Examples |
|----------------|---------------|----------|
| **PUBLIC** | This GitHub repo | README, API docs, CONTRIBUTING, CHANGELOG, source code, tests |
| **INTERNAL** | Private vault / internal repos only | Security audits, coverage gap analysis, launch checklists, team processes, internal architecture docs |
| **SENSITIVE** | Encrypted / `.secrets/` / env vars only | API keys, tokens, credentials, private keys |

### What NOT to commit here

- Files with `AUDIT`, `SECURITY_AUDIT`, `COVERAGE_GAPS`, or `LAUNCH_CHECKLIST` in the name
- Hardcoded internal IP addresses (e.g., `192.168.x.x`, Tailscale IPs)
- Internal hostnames or SSH connection strings
- Security assessment results or vulnerability reports
- Team structure, agent names, or operational details

**Rule of thumb:** If you're not sure whether something is PUBLIC, treat it as INTERNAL.

A CI workflow (`repo-hygiene.yml`) automatically checks for these patterns on every push and PR. An optional pre-commit hook is available at `.github/hooks/pre-commit` — see [Pre-commit Hooks](#pre-commit-hooks) for installation.

---

## License

By contributing, you agree your contributions will be licensed under the [MIT License](LICENSE).

---

## Writing a Plugin

TokenPak supports custom compressor plugins that run before the built-in compaction pipeline.

### The Interface

Subclass `CompressorPlugin` from `tokenpak.plugins.base`:

```python
from tokenpak.plugins.base import CompressorPlugin

class MyPlugin(CompressorPlugin):
    name = "my_plugin"          # unique identifier — required

    def compress(self, text: str, context: dict) -> dict:
        """Transform text and return a result dict.

        Args:
            text:    The message content to compress.
            context: Runtime metadata — keys include ``mode``, ``input_tokens``,
                     ``request_id``.

        Returns:
            dict with at minimum ``{"text": str, "metadata": dict}``.
        """
        compressed = text.replace("verbose phrase", "short")
        return {
            "text": compressed,
            "metadata": {"plugin": self.name, "bytes_saved": len(text) - len(compressed)},
        }

    def priority(self) -> int:
        """Higher number runs first.  Default: 50."""
        return 75
```

The `compress()` method **must** return a dict with:
- `text` (str) — the (possibly modified) output text
- `metadata` (dict) — arbitrary info about what the plugin did

### Registering a Plugin

**Option 1 — Environment variable** (recommended for quick testing):

```bash
export TOKENPAK_PLUGINS=my_package.my_module.MyPlugin
```

Multiple plugins are comma-separated:

```bash
export TOKENPAK_PLUGINS=my_pkg.pluginA.PluginA,my_pkg.pluginB.PluginB
```

**Option 2 — Config file** (`tokenpak.config.json` in the working directory):

```json
{
  "plugins": [
    "my_package.my_module.MyPlugin"
  ]
}
```

Both sources are loaded at startup; env var plugins are registered first.

### Plugin Execution Order

Plugins with a **higher** `priority()` value run first.  The default priority is 50.  Built-in compaction runs after all plugins have completed.

### Error Handling

If a plugin raises an exception, TokenPak logs a warning and continues — the original message text is preserved and the next plugin (or built-in compactor) runs normally.

### Example Plugin

See [`tokenpak/plugins/examples/passthrough.py`](tokenpak/plugins/examples/passthrough.py) for a minimal no-op example you can use as a starting template.
