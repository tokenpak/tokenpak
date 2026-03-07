# Contributing to TokenPak

<<<<<<< HEAD
Thanks for your interest in TokenPak! We're a small team and community contributions matter a lot. Here's how to help.

---

## Ways to Contribute

### 🐛 Report a Bug
Found something broken? [Open a bug report](https://github.com/kaywhy331/tokenpak/issues/new?template=bug_report.md).

Include:
- Steps to reproduce
- Your environment (Python version, OS, TokenPak version)
- Full error output

### 💡 Request a Feature
Have an idea? [Open a feature request](https://github.com/kaywhy331/tokenpak/issues/new?template=feature_request.md).

Clear use cases get prioritized. Upvote existing requests with 👍 to signal demand.

### 💬 Ask a Question
Don't open an issue for questions — use [GitHub Discussions](https://github.com/kaywhy331/tokenpak/discussions) instead.

Categories:
- **General** — Q&A, how-to questions
- **Show and Tell** — Share what you've built with TokenPak
- **Feature Requests** — Discuss ideas before filing an issue
- **Help & Support** — Stuck? Ask here

### 📝 Improve Documentation
Spotted a typo, outdated example, or confusing section? PRs for docs are always welcome.

### 🔧 Submit a Pull Request
1. Fork the repo and create a branch: `git checkout -b fix/your-fix`
2. Make your change
3. Run tests: `pytest tests/`
4. **Update [CHANGELOG.md](CHANGELOG.md)** — add your change under `## [Unreleased]` in the correct section (Added / Changed / Fixed / Security). Link to your PR: `[#123](https://github.com/kaywhy331/tokenpak/pull/123)`
5. Open a PR with a clear description of what and why

---

## Code Style

- Python 3.10+
- Follow existing patterns in the codebase
- Keep changes focused — one PR per concern
- Add tests for new behavior when practical

---

## Response Times

We aim to:
- Acknowledge all issues and PRs within **48 hours**
- Respond to Discussions within **48 hours**
- Review PRs within **1 week**

---

## Community Guidelines

- Be respectful and constructive
- No spam or self-promotion
- Search before posting — your question may already be answered

---

## Feedback That Shapes the Roadmap

Popular feature requests and recurring pain points directly influence what we build next. We track themes monthly and share what we're hearing. Your feedback closes the loop.

**Thank you for making TokenPak better.**
=======
Thank you for your interest in contributing to TokenPak! This guide will help you get started.

## Code of Conduct

Be respectful, constructive, and patient. We're all here to build something great together.

## Getting Started

### Prerequisites

- Python 3.10+
- pip or uv (recommended)
- Git

### Development Setup

```bash
# Clone the repo
git clone https://github.com/kaywhy331/tokenpak.git
cd tokenpak

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install in development mode
pip install -e ".[dev]"

# Run tests to verify setup
pytest
```

### Project Structure

```
tokenpak/
├── tokenpak/
│   ├── agent/           # Agent-mode proxy (agentic workflows)
│   ├── core/            # Core compression logic
│   ├── telemetry/       # Usage tracking and analytics
│   └── cli.py           # Command-line interface
├── tests/               # Test suite
├── docs/                # Documentation
└── recipes/             # Compression recipes
```

## How to Contribute

### Reporting Issues

1. Search existing issues first
2. Use issue templates when available
3. Include: Python version, OS, full error traceback, minimal reproduction

### Suggesting Features

Open a GitHub Discussion or Issue with:
- Use case description
- Proposed solution (optional)
- Alternatives considered

### Pull Requests

1. **Fork** the repo and create a branch from `main`
2. **Write tests** for new functionality
3. **Run the test suite** before submitting:
   ```bash
   pytest
   black --check .
   ```
4. **Keep PRs focused** — one feature/fix per PR
5. **Write clear commit messages** (see below)

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

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=tokenpak

# Run specific test file
pytest tests/test_compression.py

# Run tests matching pattern
pytest -k "test_cache"
```

## Code Style

We use:
- **Black** for formatting
- **Type hints** for all public functions
- **Docstrings** (Google style) for classes and public methods

```bash
# Format code
black .

# Check formatting
black --check .
```

## Documentation

- Update relevant docs when changing behavior
- Add docstrings to new functions/classes
- Keep README.md examples up to date

## Release Process (Maintainers)

1. Update version in `pyproject.toml`
2. Update CHANGELOG.md
3. Create GitHub Release with tag `vX.Y.Z`
4. CI publishes to PyPI automatically

## Questions?

- Open a GitHub Discussion
- Check existing issues and discussions
- Read the docs at `/docs`

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
>>>>>>> 2a1287e92675787cd8cb17653be8891a1d32243b
