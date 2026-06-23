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
.PHONY: help dev test lint format check build docs clean install hooks benchmark-headline

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

test-chaos:  ## Run chaos & resilience tests (fault injection / failure-recovery)
	$(PYTEST) tests/chaos/ -m chaos -q --tb=short

benchmark-headline:  ## Run headline 30-50% claim benchmark (standard 21 §9.8 blocking)
	$(PYTEST) tests/benchmarks/test_headline_claim.py -v -s

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

# ── Release-Gate Trust Contract (Std 30, ratified 2026-05-09) ────────────────
.PHONY: api-snapshot api-snapshot-check api-snapshot-diff workflow-steps-snapshot \
        workflow-steps-check telemetry-snapshot telemetry-check taxonomy-check \
        deps-audit migration-multihop release-gate-snapshots release-gate-check \
        release-leak-check

api-snapshot:  ## Std 30 §7 (R7) — regenerate tokenpak/_snapshots/public-api.json
	$(PYTHON) scripts/release_gate/gen_api_snapshot.py

api-snapshot-check:  ## Std 30 §7 — fail if public-api.json drifts from current source
	$(PYTHON) scripts/release_gate/gen_api_snapshot.py --check

api-snapshot-diff:  ## Std 30 §6 (R6) + Std 21 §11 — diff snapshots between BASE and HEAD
	$(PYTHON) scripts/release_gate/api_snapshot_diff.py $(BASE) $(HEAD)

workflow-steps-snapshot:  ## Std 30 §13.3 (R11) + Std 21 §12 — regenerate workflow-steps.json
	$(PYTHON) scripts/release_gate/gen_workflow_steps.py

workflow-steps-check:  ## Std 30 §13.3 — fail if workflow-steps.json drifts
	$(PYTHON) scripts/release_gate/gen_workflow_steps.py --check

telemetry-snapshot:  ## Std 30 §7 — regenerate tokenpak/_snapshots/telemetry-schema.json
	$(PYTHON) scripts/release_gate/gen_telemetry_schema.py

telemetry-check:  ## Std 30 §7 — fail if telemetry-schema.json drifts
	$(PYTHON) scripts/release_gate/gen_telemetry_schema.py --check

taxonomy-check:  ## Std 02 §13 + Std 30 §5 (R5) — every test has exactly one taxonomy marker
	$(PYTHON) scripts/release_gate/taxonomy_check.py

deps-audit:  ## Std 02 §14 + Std 30 §13.2 (R17) — uv lock --check + pip-audit + yanked-package scan
	@command -v uv >/dev/null && uv lock --check || echo "uv not installed; skipping uv lock --check (install uv to enable)"
	$(PYTHON) -m pip install --quiet pip-audit
	$(PYTHON) -m pip_audit --strict --skip-editable

migration-multihop:  ## Std 30 §14.1 (R16) + Std 10 §E9 — run migrations from each of last 6 minor versions
	$(PYTHON) scripts/release_gate/migration_multihop.py

release-gate-snapshots: api-snapshot workflow-steps-snapshot telemetry-snapshot  ## Regenerate ALL release-gate snapshots
	@echo "✅  All release-gate snapshots regenerated"

release-gate-check: api-snapshot-check workflow-steps-check telemetry-check taxonomy-check  ## Validate ALL release-gate snapshots
	@echo "✅  All release-gate checks passed"

release-leak-check:  ## Full-tree public-leak scan of the built sdist + wheel (release gate)
	$(PYTHON) -m pip install --quiet build
	$(PYTHON) -m build
	$(PYTHON) scripts/release_gate/check_release_leaks.py --dist dist/
