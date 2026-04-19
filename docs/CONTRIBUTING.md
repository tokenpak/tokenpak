# Contributing to TokenPak

Thank you for your interest in contributing to TokenPak! This guide will help you get started with development and testing.

## Running Tests

TokenPak includes comprehensive test coverage with multiple execution modes.

### Test Commands

```bash
# Run fast smoke subset (~200 core tests, < 60s) — use for dev loop and CI pre-flight
make test

# Run complete test suite (all tests including slow/integration)
make test-full

# Run tests with coverage report
make test-cov

# Run specific test file
cd packages/pypi && pytest ../tests/test_elo.py -q

# Run with coverage report (same as make test-cov)
cd packages/pypi && pytest ../tests/ tests/ -q --cov=tokenpak --cov-report=term-missing
```

### Test Execution Modes

**Fast Smoke Subset (Default)**
- `make test` runs ~200 core tests in under 60 seconds
- Recommended for rapid feedback during development
- Good for CI pre-flight checks
- Focuses on high-impact test cases

**Full Suite**
- `make test-full` runs the complete test suite including slow/integration tests
- Recommended before committing or opening PRs
- More comprehensive but takes longer
- Catches edge cases that smoke tests might miss

**With Coverage**
- `make test-cov` runs tests with coverage report
- Shows which lines are untested
- Helpful for improving code coverage
- Generates both terminal and HTML reports

### Individual Test Execution

You can always run individual test files directly:

```bash
# From the project root
cd packages/pypi && pytest ../tests/test_compression_directives.py -v

# With specific test class or function
cd packages/pypi && pytest ../tests/test_compression_directives.py::TestCompressionDirectives::test_directive_apply -v
```

### Coverage Reports

Generate a detailed coverage report:

```bash
cd packages/pypi
pytest ../tests/ --cov=tokenpak --cov-report=html
# Open htmlcov/index.html in your browser
```

## Development Workflow

1. **Fork and Clone** — Create your own fork and clone the repository
2. **Create a Branch** — Use descriptive branch names (e.g., `feature/compression-tuning`)
3. **Write Tests** — Add tests for any new functionality
4. **Run Tests** — Use `make test` to ensure all tests pass
5. **Commit and Push** — Write clear commit messages
6. **Open a Pull Request** — Reference any related issues

## Code Style

- Use Python 3.11+
- Follow PEP 8 conventions
- Use type hints for public functions
- Add docstrings to modules and public classes

## Testing Guidelines

- **Write unit tests** for new features and bug fixes
- **Test edge cases** — empty inputs, None values, extreme values
- **Mock external dependencies** — avoid network calls and real file I/O
- **Test error paths** — verify exception handling
- **Maintain coverage** — aim for >80% coverage on new code

## Questions?

If you have questions about development or testing, please:
- Check the [API Reference](./api-reference.md)
- Review the [Architecture Guide](./architecture.md)
- Open an issue on GitHub

Thank you for contributing! 🎉
