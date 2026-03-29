# TokenPak Coverage Policy & Metrics

## Overview

TokenPak uses code coverage tracking to maintain quality and prevent regressions. This document outlines our targets, current baseline, and Phase 2 improvements.

## Baseline (Pre-Phase2)

| Component | Coverage | Status |
|-----------|----------|--------|
| **Overall** | 39% | Current baseline |
| **Core modules** (adapters, routing, cache) | 78% | Tier-1 |
| **Validation** | 22% | Gap — Phase 2 target |
| **Error handling** | 18% | Gap — Phase 2 target |
| **Cache management** | 25% | Gap — Phase 2 target |
| **CLI** | 8% | Known limitation |

## Post-Phase2 Targets

Phase 2 adds ~28 new tests to close critical gaps in validation, error handling, and cache management.

| Component | Target | Scope |
|-----------|--------|-------|
| **Tier-1 (validation + error + cache)** | 50%+ | Core improvements |
| **Core modules** | 80%+ | Maintain + improve |
| **Overall** | 45%+ | Raise baseline |
| **CLI** | 10%+ | Stretch goal (may defer) |

## Coverage by Module

### Tier-1 (Phase 2 Focus)

#### Validation (`tokenpak.validation`)
- **Current:** 22%
- **Phase 2 target:** 50%+
- **New tests:** Schema validation, edge cases, type checking
- **Critical paths:** Input validation on API calls, config parsing

#### Error Handling (`tokenpak.error_handling`)
- **Current:** 18%
- **Phase 2 target:** 50%+
- **New tests:** Error recovery, logging, context propagation
- **Critical paths:** Fallback logic, retry mechanisms

#### Cache Management (`tokenpak.cache`)
- **Current:** 25%
- **Phase 2 target:** 50%+
- **New tests:** Cache hit/miss, TTL expiry, concurrency
- **Critical paths:** Cache invalidation, LRU eviction

### Core Modules (Tier-2)

#### Adapters (Anthropic, OpenAI, etc.)
- **Current:** 80%+ 
- **Target:** Maintain 80%+ (no regression)
- **Status:** Stable, high coverage

#### Routing (`tokenpak.routing`)
- **Current:** 78%
- **Target:** 85%+
- **Status:** Good coverage, minor gaps in edge cases

#### Compression (`tokenpak.compression`)
- **Current:** 65%
- **Target:** 75%+
- **Status:** Functional, but fallback paths untested

### CLI & Utilities
- **Current:** 8%
- **Target:** 10%+ (Phase 3)
- **Status:** Lower priority; focus on core library coverage first

## How to Run Coverage Locally

### Run all tests with coverage
```bash
pytest tests/ -v --cov=tokenpak --cov-report=html
open htmlcov/index.html
```

### Run Phase 2 tests only (Tier-1)
```bash
pytest tests/test_validation*.py tests/test_error*.py tests/test_cache*.py \
  --cov=tokenpak.validation \
  --cov=tokenpak.error_handling \
  --cov=tokenpak.cache \
  --cov-report=term-missing
```

### Check coverage for a specific module
```bash
pytest tests/test_validation.py --cov=tokenpak.validation --cov-report=term-missing
```

### Generate HTML report
```bash
pytest tests/ -v --cov=tokenpak --cov-report=html
# Open: htmlcov/index.html
```

## CI/CD Coverage Gates

### GitHub Actions (`.github/workflows/ci.yml`)

**On every PR:**
1. Run all tests (Python 3.10, 3.11, 3.12)
2. Run Phase 2 tests on Python 3.11
3. **Enforce Tier-1 gate:** Fail if validation+error+cache < 50%
4. **Monitor overall gate:** Warn if overall < 45%
5. **Report coverage delta** in PR comment

### Pre-commit Hook (`.pre-commit-config.yaml`)

**On every commit:**
1. Run Phase 2 tests locally
2. **Warn** if coverage < 45%
3. **Fail** if coverage < 40%
4. Override: `git commit --no-verify`

## Maintenance Policy

### Preventing Regression

1. **Always run Phase 2 tests before committing**
   ```bash
   pre-commit run coverage-check --all-files
   ```

2. **Never disable or skip coverage tests**
   - If a test is flaky, fix it — don't skip it
   - Use `@pytest.mark.xfail` if test is known to fail

3. **Code review: Check coverage impact**
   - Review coverage report in CI before merging
   - If coverage drops, ask PR author to add tests
   - Tier-1 modules require 50%+ coverage for merge

4. **Periodic audits**
   - Monthly: Check if coverage targets are still realistic
   - Quarterly: Review untested code paths for risk

### Adding New Tests

When adding a new feature:
1. Write tests **before** submitting PR
2. Ensure new code has 80%+ coverage
3. Run `pytest ... --cov-report=html` to visualize
4. Reference the coverage report in PR description

## Coverage Thresholds Rationale

| Threshold | Reason |
|-----------|--------|
| **50% on Tier-1** | Covers happy path + major edge cases |
| **80% on core** | Adapter layer is critical; high reliability needed |
| **45% overall** | Raise from 39% baseline without over-constraining CLI |
| **40% fail gate** | Prevents catastrophic regressions |

## Tools & Visibility

- **Tool:** pytest-cov (via `coverage` library)
- **Reporter:** Codecov (integration with GitHub)
- **Frequency:** Every PR (CI) + every commit (pre-commit)
- **Archive:** Coverage reports stored in GitHub artifacts (30 days)

## Questions / Feedback

- Coverage not updating? Check `.coveragerc` or run with `--cov-report=term-missing`
- Pre-commit hook failing? Run `bash scripts/coverage-check.sh --verbose`
- Targets unrealistic? Open an issue or contact the team

---

**Last updated:** 2026-03-26 (Phase 2 integration)
