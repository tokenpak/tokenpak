# TokenPak Makefile
# Requires: Python 3.10+, pip
# Usage: make dev && make test

.DEFAULT_GOAL := help

# ── Configuration ─────────────────────────────────────────────────────────────
PYTHON      := python3
VENV        := .venv
VENV_BIN    := $(VENV)/bin
PIP         := $(VENV_BIN)/pip
PYTEST      := $(VENV_BIN)/pytest
RUFF        := $(VENV_BIN)/ruff
BUILD       := $(VENV_BIN)/python3 -m build
MKDOCS      := $(VENV_BIN)/mkdocs

# Detect OS for cross-platform compatibility
UNAME := $(shell uname -s)

# ── Phony targets ──────────────────────────────────────────────────────────────
.PHONY: help dev test lint format check build docs clean install hooks

# ── Help ──────────────────────────────────────────────────────────────────────
help:  ## Show this help message
	@echo "TokenPak — available make targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ── Development setup ─────────────────────────────────────────────────────────
dev: $(VENV)/bin/activate  ## Create venv and install tokenpak[dev] in editable mode

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install -e ".[dev]"
	@echo ""
	@echo "✅  Dev environment ready. Activate with: source $(VENV)/bin/activate"
	@echo "    Then run: make test"

install:  ## Install tokenpak (non-editable, no dev extras)
	$(PYTHON) -m pip install .

# ── Testing ────────────────────────────────────────────────────────────────────
test:  ## Run full test suite
	$(PYTEST) tests/ -q --tb=short

test-quick:  ## Run quick audit subset (<30s, no live proxy needed)
	$(PYTEST) -m quick -q --tb=short

test-fast:  ## Run tests, stop on first failure
	$(PYTEST) tests/ -q --tb=short -x

test-cov:  ## Run tests with coverage report
	$(PYTEST) tests/ -q --tb=short \
		--cov=tokenpak \
		--cov-report=term-missing \
		--cov-report=html:htmlcov
	@echo "Coverage report: htmlcov/index.html"

# ── Linting & formatting ───────────────────────────────────────────────────────
lint:  ## Run ruff linter
	$(RUFF) check tokenpak/ tests/

format:  ## Run ruff formatter (auto-fix)
	$(RUFF) format tokenpak/ tests/

format-check:  ## Check formatting without making changes
	$(RUFF) format --check tokenpak/ tests/

check: lint format-check test  ## Run lint + format check + tests (CI gate)

# ── Build ──────────────────────────────────────────────────────────────────────
build:  ## Build source distribution and wheel
	$(VENV_BIN)/python3 -m pip install --quiet build
	$(VENV_BIN)/python3 -m build
	@echo ""
	@ls -lh dist/*.whl dist/*.tar.gz 2>/dev/null || true
	@echo "✅  Build complete. Artifacts in dist/"

# ── Docs ───────────────────────────────────────────────────────────────────────
docs:  ## Build MkDocs documentation site
	@if [ ! -f mkdocs.yml ]; then \
		echo "⚠️  mkdocs.yml not found — run: pip install mkdocs mkdocs-material"; \
		exit 1; \
	fi
	$(PIP) install --quiet "mkdocs>=1.5.0" "mkdocs-material>=9.5.0"
	$(VENV_BIN)/mkdocs build
	@echo "✅  Docs built in site/"

docs-serve:  ## Serve MkDocs documentation locally
	$(VENV_BIN)/mkdocs serve

# ── Hooks ─────────────────────────────────────────────────────────────────────
hooks:  ## Install pre-commit hooks
	$(PIP) install --quiet pre-commit
	$(VENV_BIN)/pre-commit install
	@echo "✅  Pre-commit hooks installed"

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:  ## Remove build artifacts, caches, and dist/
	rm -rf dist/ build/ *.egg-info .eggs/
	rm -rf .pytest_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -not -path './$(VENV)/*' -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -not -path './$(VENV)/*' -delete 2>/dev/null || true
	find . -type f -name '*.pyo' -not -path './$(VENV)/*' -delete 2>/dev/null || true
	@echo "✅  Clean complete"

clean-all: clean  ## Remove everything including venv
	rm -rf $(VENV)
	@echo "✅  Full clean (including venv)"
