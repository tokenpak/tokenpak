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
.PHONY: help dev test test-release-core lint format check build docs clean install hooks bench benchmark-headline lint-imports

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

RELEASE_CORE_MARKERS := not integration and not chaos and not slow and not needs_fast_host

test-release-core:  ## Run blocking CI core partition; does not satisfy complete A1
	$(PYTEST) tests/ -m "$(RELEASE_CORE_MARKERS)" -q --tb=short

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

benchmark-headline:  ## Run the blocking headline 30-50% claim benchmark
	$(PYTEST) tests/benchmarks/test_headline_claim.py -v -s

bench:  ## Run the blocking Claude Code passthrough p50 regression gate
	$(VENV_BIN)/python3 scripts/benchmark_claude_passthrough.py

# ── Linting & formatting ───────────────────────────────────────────────────────
lint:  ## Run ruff linter
	$(RUFF) check tokenpak/ tests/

format:  ## Run ruff formatter (auto-fix)
	$(RUFF) format tokenpak/ tests/

format-check:  ## Check formatting without making changes
	$(RUFF) format --check tokenpak/ tests/

lint-imports:  ## Run the import-linter architecture-contract gate
	$(VENV_BIN)/lint-imports

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

taxonomy-check:  ## R5 — every test has exactly one taxonomy marker
	$(PYTHON) scripts/release_gate/taxonomy_check.py

deps-audit:  ## R17 — uv lock --check + pip-audit (third-party tree) + yanked-package scan
	@if command -v uv >/dev/null; then uv lock --check; else echo "uv not installed; skipping uv lock --check (install uv to enable)"; fi
	$(PYTHON) -m pip install --quiet pip-audit
	$(PYTHON) -m pip_audit --strict .
# Project mode (`.`) audits the pyproject-declared third-party dependency tree.
# `--skip-editable` was insufficient: under `--strict`, pip-audit escalates the
# skip of the editable local `tokenpak` dist to an error in the dev/CI venv
# (both `pip install -e ".[dev]"`). Project mode audits every third-party
# dependency at the same --strict severity floor while excluding only the
# unreleased local package (residual ruling decision 2).

migration-multihop:  ## R16 — run migrations from each of last 6 minor versions
	$(PYTHON) scripts/release_gate/migration_multihop.py

release-gate-snapshots: api-snapshot workflow-steps-snapshot telemetry-snapshot  ## Regenerate ALL release-gate snapshots
	@echo "✅  All release-gate snapshots regenerated"

release-gate-check: api-snapshot-check workflow-steps-check telemetry-check taxonomy-check  ## Validate ALL release-gate snapshots
	@echo "✅  All release-gate checks passed"

release-leak-check:  ## Full-tree public-leak scan of the built sdist + wheel (release gate)
	$(PYTHON) -m pip install --quiet build
	$(PYTHON) -m build
	$(PYTHON) scripts/release_gate/check_release_leaks.py --dist dist/

# Always-on baseline of cheap, deterministic, public-safety gates (maturity /
# license / leak / help-verbs / tokenpak-literal) that must pass on every
# release regardless of tier. Deliberately narrow: the A1-A7 / B1 /
# B3 / C5 / C6 umbrella is owned by separate packets and NOT claimed here.
.PHONY: release-check-baseline
release-check-baseline:  ## Five-gate always-on release baseline
	$(PYTHON) scripts/release_check/release_check.py

# ── Automated Audit + Release Umbrella ───────────────────────────────────────
# A3 accepted findings are external release-captain receipts. A nonzero mypy
# result is never downgraded in this public tree: the exact Python/mypy/command,
# counts, files, and transcript hashes must all match the supplied receipt.
# A3_PYTHON can pin that audited toolchain independently when the release-core
# VENV intentionally mirrors CI's narrower development install shape.
A3_ACCEPTED_FINDING ?=
A3_RELEASE_VERSION ?=
A3_PYTHON ?= $(VENV_BIN)/python3
RELEASE_BASE_REF ?= $(shell git rev-parse -q --verify github/main 2>/dev/null || git rev-parse -q --verify public/main 2>/dev/null)

.PHONY: ci-lint audit-mypy docs-check forbidden-phrases-check telemetry-audit \
        fresh-install-demo byte-fidelity-check release-docs-pattern-check audit \
        release-check

ci-lint:  ## A1/B1 — exact Ruff selection used by CI
	$(RUFF) check tokenpak/ tests/ --select=E,F,W,I --ignore=E501,E701,E702,E402,E741,F841

audit-mypy:  ## A3/B1 — unchanged six-root strict-mypy gate
	$(A3_PYTHON) scripts/release_audit.py mypy $(if $(A3_ACCEPTED_FINDING),--accepted-finding "$(A3_ACCEPTED_FINDING)") $(if $(A3_RELEASE_VERSION),--release-version "$(A3_RELEASE_VERSION)")

docs-check:  ## C5/B1 — strict links, navigation audit, and generated CLI-doc parity
	bash scripts/audit-docs.sh --release-gate
	$(MKDOCS) build --strict
	PATH="$(abspath $(VENV_BIN)):$$PATH" bash scripts/check-cli-docs.sh

forbidden-phrases-check:  ## B3/B1 — hard marketing-filler and qualifier scan
	$(VENV_BIN)/python3 scripts/release_audit.py forbidden

telemetry-audit:  ## B1 — code-created fixture and canonical summary SQL
	$(VENV_BIN)/python3 scripts/release_audit.py telemetry

fresh-install-demo:  ## A5 — clean local-candidate install reaches the offline demo under 60s
	$(VENV_BIN)/python3 scripts/fresh_install_demo.py --max-seconds 60

byte-fidelity-check:  ## A7 — dedicated byte-preserved pipeline regression suite
	$(PYTEST) tests/test_pipeline_integration.py -q --tb=short

release-docs-pattern-check:  ## C6 — no forbidden patterns in release-touched docs
	@test -n "$(RELEASE_BASE_REF)" || { echo "C6 FAIL: public baseline ref is unavailable"; exit 1; }
	$(VENV_BIN)/python3 scripts/release_audit.py docs-patterns --base-ref "$(RELEASE_BASE_REF)"

audit: ci-lint audit-mypy docs-check forbidden-phrases-check telemetry-audit  ## B1 — full automated audit
	@echo "B1 audit: PASS — lint, strict mypy/accepted evidence, links, phrases, telemetry SQL"

# One Make DAG deliberately shares audit prerequisites with the named A/B/C
# gates, so expensive strict-mypy and docs checks run exactly once per command.
# A1 runs the raw complete suite here. The exact CI core partition remains
# available as ``test-release-core`` and stays independently required in CI.
# The repository-wide formatter ratchet is deferred.
release-check: release-check-baseline test test-quick lint-imports fresh-install-demo bench byte-fidelity-check audit release-docs-pattern-check  ## A1-A7/B1/B3/C5/C6
	@echo "release-check: PASS — A1-A7/B1/B3/C5/C6 reached terminal success"
