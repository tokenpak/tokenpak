# Contributing to TokenPak

Thank you for your interest in contributing! This guide covers how to get set up, our development workflow, and the branch protection rules that keep the codebase stable.

---

## Prerequisites

- Python 3.10, 3.11, or 3.12
- `git`
- A GitHub account with fork access to `kaywhy331/tokenpak`

---

## Local Setup

```bash
git clone https://github.com/kaywhy331/tokenpak.git
cd tokenpak
pip install -e ".[dev]"
```

---

## Development Workflow

1. **Fork** the repository (first-time contributors)
2. **Branch** off `main` or `oss-launch`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
3. **Make changes** and write/update tests
4. **Run CI checks locally** before pushing:
   ```bash
   ruff check tokenpak/ tests/        # lint
   mypy tokenpak/ --ignore-missing-imports  # type check
   pytest tests/ --cov=tokenpak --cov-fail-under=70  # tests + coverage
   ```
5. **Push** your branch and open a Pull Request against `main` or `oss-launch`

---

## Branch Protection Rules

The following rules are enforced on `main` and `oss-launch`:

| Rule | Detail |
|------|--------|
| **CI must pass** | All CI jobs (Lint, Type Check, Test matrix) must be green before merge is allowed |
| **No force-push** | Direct force-pushes to `main` and `oss-launch` are blocked |
| **No direct commits** | All changes must go through a PR (no bypassing for regular contributors) |
| **Coverage threshold** | Test coverage must be ≥ 70% — PRs that drop below this will fail CI |

> **PRs require CI green before merge.** If any job in the matrix fails — lint error, type error, test failure, or coverage drop — the merge button will be blocked until the issue is resolved.

---

## CI Overview

CI runs automatically on every push and pull request targeting `main` or `oss-launch`. The pipeline:

1. **Lint** — `ruff` checks all source and test files (zero-tolerance, exits non-zero on any error)
2. **Type Check** — `mypy` validates type annotations in the `tokenpak/` package
3. **Test Matrix** — `pytest` runs across Python 3.10, 3.11, and 3.12 with `--cov-fail-under=70`
4. **Coverage Upload** — Coverage report uploaded to Codecov from the Python 3.12 run

CI badge: [![CI](https://github.com/kaywhy331/tokenpak/actions/workflows/ci.yml/badge.svg)](https://github.com/kaywhy331/tokenpak/actions)

---

## Code Standards

- **Formatting:** `black` (auto-format before committing)
- **Linting:** `ruff` (enforced in CI)
- **Type hints:** Required for all public functions and methods
- **Tests:** All new features must have corresponding test coverage

---

## Opening a Pull Request

- Give your PR a clear title describing *what* changed
- Reference any related issues (`Closes #123`)
- Fill out the PR description template if provided
- Wait for CI to pass before requesting review
- Address all review comments before merge

---

## Questions?

Open an issue or start a discussion on GitHub.
