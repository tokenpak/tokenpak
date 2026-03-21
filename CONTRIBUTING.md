# Contributing to TokenPak

Thank you for your interest in contributing! TokenPak is a small, focused project and community contributions matter a lot. Here's everything you need to get started.

---

## Ways to Contribute

- 🐛 **Report a bug** — [Open a bug report](https://github.com/tokenpak/tokenpak/issues/new?template=bug_report.md)
- 💡 **Request a feature** — [Open a feature request](https://github.com/tokenpak/tokenpak/issues/new?template=feature_request.md)
- 💬 **Ask a question** — Use [GitHub Discussions](https://github.com/tokenpak/tokenpak/discussions), not issues
- 📝 **Improve docs** — PRs for typos, outdated examples, and clarifications always welcome
- 🔧 **Submit code** — See PR workflow below

---

## Development Setup

### Prerequisites

- Python 3.10+
- pip or uv (recommended)
- Git

### Clone and Install

```bash
# Clone the repo
git clone https://github.com/tokenpak/tokenpak.git
cd tokenpak

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install in development mode with dev dependencies
pip install -e ".[dev]"

# Verify setup
tokenpak --version
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

## Submitting Changes

1. **Branch from `master`**: `git checkout -b fix/your-fix`
2. **Make focused changes** — one PR per concern
3. **Write tests** for new behavior when practical
4. **Run the full test suite** and confirm it passes
5. **Update [CHANGELOG.md](CHANGELOG.md)** — add your change under `## [Unreleased]` in the correct section (Added / Changed / Fixed / Security). Link to your PR: `[#123](https://github.com/tokenpak/tokenpak/pull/123)`
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

- Open a [GitHub Discussion](https://github.com/tokenpak/tokenpak/discussions)
- Tag @kaywhy331 for blocking issues
- Read the docs under `/docs`

---

## License

By contributing, you agree your contributions will be licensed under the [MIT License](LICENSE).
