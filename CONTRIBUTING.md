# Contributing to TokenPak

Thank you for your interest in contributing! We welcome contributions of all kinds.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/tokenpak.git`
3. Create a virtual environment: `python -m venv .venv && source .venv/bin/activate`
4. Install in development mode: `pip install -e ".[dev]"`
5. Run tests: `pytest tests/ -q`

## Development Setup

```bash
# Install with all development dependencies
pip install -e ".[dev,dashboard,enterprise]"

# Run the test suite
pytest tests/ -q

# Run type checking
mypy tokenpak/ --ignore-missing-imports

# Run the proxy locally
tokenpak serve --port 8766
```

## Making Changes

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make your changes
3. Add or update tests
4. Run the test suite: `pytest tests/ -q`
5. Commit with a clear message: `git commit -m "feat: add X"`
6. Push and open a PR

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `test:` — adding or updating tests
- `refactor:` — code change that neither fixes a bug nor adds a feature
- `perf:` — performance improvement
- `chore:` — build process or tooling changes

## Code Style

- Python 3.10+ with type hints
- Keep functions focused and small
- Write docstrings for public APIs
- No external dependencies without discussion

## Testing

- All new features must include tests
- Maintain or improve existing test coverage
- Tests should be fast (no network calls without mocking)

## Reporting Issues

- Use GitHub Issues
- Include: Python version, OS, steps to reproduce, expected vs actual behavior
- For security issues, see [SECURITY.md](docs/SECURITY.md)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
