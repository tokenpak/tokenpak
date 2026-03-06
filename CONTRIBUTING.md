# Contributing to TokenPak

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
