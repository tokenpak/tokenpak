# SPDX-License-Identifier: Apache-2.0
"""Benchmarking for TokenPak: compression performance and latency."""

import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .processors import get_processor
from .registry import Block, BlockRegistry
from .tokens import cache_info, clear_cache, count_tokens, count_tokens_uncached
from .walker import walk_directory

# ---------------------------------------------------------------------------
# Built-in sample data for compression benchmark
# ---------------------------------------------------------------------------

BUILTIN_SAMPLES = [
    {
        "name": "python_module",
        "filename": "utils.py",
        "file_type": "code",
        "content": '''\
"""
Utility functions for the application.

This module provides helper functions used throughout the codebase.
"""

# Standard library imports
import os
import sys
import json
import logging
from typing import List, Dict, Optional, Any

# Configure module-level logger
logger = logging.getLogger(__name__)


def load_config(path: str) -> Dict[str, Any]:
    """
    Load configuration from a JSON file.

    Args:
        path: Absolute or relative path to the config file.

    Returns:
        Parsed configuration as a dictionary.

    Raises:
        FileNotFoundError: If the config file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    # Expand user home directory if present
    resolved = os.path.expanduser(path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Config file not found: {resolved}")

    with open(resolved, "r", encoding="utf-8") as f:
        data = json.load(f)

    logger.debug("Loaded config from %s (%d keys)", resolved, len(data))
    return data


def flatten_dict(d: Dict, prefix: str = "", sep: str = ".") -> Dict[str, Any]:
    """
    Flatten a nested dictionary into a single-level dict with dot-separated keys.

    Args:
        d: Input dictionary (may be nested).
        prefix: Key prefix for recursion (leave empty for top-level call).
        sep: Separator string between key levels.

    Returns:
        Flattened dictionary.
    """
    result = {}
    for key, value in d.items():
        full_key = f"{prefix}{sep}{key}" if prefix else key
        if isinstance(value, dict):
            # Recurse into nested dicts
            result.update(flatten_dict(value, prefix=full_key, sep=sep))
        else:
            result[full_key] = value
    return result


class RetryError(Exception):
    """Raised when all retry attempts are exhausted."""
    pass


def retry(fn, retries: int = 3, delay: float = 1.0):
    """
    Retry a callable up to `retries` times with a fixed delay.

    Args:
        fn: Zero-argument callable to invoke.
        retries: Maximum number of attempts (default 3).
        delay: Sleep time in seconds between attempts (default 1.0).

    Returns:
        The return value of `fn` on success.

    Raises:
        RetryError: If all attempts fail.
    """
    last_exc = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            logger.warning("Attempt %d/%d failed: %s", attempt + 1, retries, exc)
            time.sleep(delay)
    raise RetryError(f"All {retries} attempts failed") from last_exc
''',
    },
    {
        "name": "markdown_readme",
        "filename": "README.md",
        "file_type": "text",
        "content": """\
# MyProject

> A fast, lightweight task runner for modern pipelines.

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Contributing](#contributing)
- [License](#license)

---

## Installation

### Prerequisites

- Python 3.10 or later
- pip 22+

### Steps

```bash
# Clone the repository
git clone https://github.com/example/myproject.git
cd myproject

# Install dependencies
pip install -r requirements.txt

# Install in editable mode
pip install -e .
```

---

## Usage

Run the default task:

```bash
myproject run
```

Pass extra options:

```bash
myproject run --workers 4 --verbose
```

---

## Configuration

Create a `config.yaml` in your project root:

```yaml
# Main configuration
workers: 4
log_level: info
output_dir: ./dist
```

All keys are optional; defaults are used for any missing values.

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Commit your changes (`git commit -m "feat: add my feature"`)
4. Push to the branch (`git push origin feat/my-feature`)
5. Open a pull request

Please follow the existing code style and add tests for new features.

---

## License

Apache-2.0 License — see [LICENSE](LICENSE) for details.
""",
    },
    {
        "name": "json_config",
        "filename": "package.json",
        "file_type": "data",
        "content": """\
{
  "name": "myapp",
  "version": "1.2.3",
  "description": "A sample application for demonstration purposes",
  "main": "dist/index.js",
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "start": "node dist/index.js",
    "dev": "ts-node src/index.ts",
    "test": "jest --coverage",
    "lint": "eslint src --ext .ts,.tsx",
    "format": "prettier --write 'src/**/*.ts'"
  },
  "keywords": ["demo", "typescript", "nodejs"],
  "author": "Jane Doe <jane@example.com>",
  "license": "Apache-2.0",
  "dependencies": {
    "express": "^4.18.2",
    "lodash": "^4.17.21",
    "axios": "^1.4.0",
    "zod": "^3.21.4"
  },
  "devDependencies": {
    "typescript": "^5.1.6",
    "@types/node": "^20.4.1",
    "@types/express": "^4.17.17",
    "jest": "^29.6.1",
    "ts-jest": "^29.1.1",
    "eslint": "^8.44.0",
    "prettier": "^3.0.0"
  }
}
""",
    },
    {
        "name": "javascript_class",
        "filename": "ApiClient.js",
        "file_type": "code",
        "content": """\
/**
 * ApiClient — a simple HTTP client wrapper around fetch.
 *
 * Provides GET, POST, PUT, and DELETE helpers with automatic
 * JSON serialisation and error handling.
 */

// Default request timeout in milliseconds
const DEFAULT_TIMEOUT_MS = 10_000;

/**
 * Custom error class for API failures.
 */
class ApiError extends Error {
  /**
   * @param {number} status  HTTP status code
   * @param {string} message Human-readable description
   */
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * A lightweight API client.
 */
class ApiClient {
  /**
   * @param {string} baseUrl   Base URL for all requests (e.g. "https://api.example.com")
   * @param {object} [options] Additional options
   * @param {number} [options.timeout=10000] Request timeout in ms
   * @param {object} [options.headers={}]    Default headers to include
   */
  constructor(baseUrl, { timeout = DEFAULT_TIMEOUT_MS, headers = {} } = {}) {
    this.baseUrl = baseUrl.replace(/\\/$/, "");
    this.timeout = timeout;
    this.defaultHeaders = { "Content-Type": "application/json", ...headers };
  }

  /**
   * Build a fully-qualified URL from a relative path.
   * @param {string} path  e.g. "/users/42"
   * @returns {string}
   */
  _url(path) {
    return `${this.baseUrl}${path.startsWith("/") ? path : "/" + path}`;
  }

  /**
   * Perform a fetch request with timeout support.
   * @param {string} url
   * @param {RequestInit} options
   * @returns {Promise<any>}
   */
  async _request(url, options) {
    const controller = new AbortController();
    const timerId = setTimeout(() => controller.abort(), this.timeout);

    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      clearTimeout(timerId);

      if (!response.ok) {
        // Parse error body if available
        let msg = response.statusText;
        try {
          const body = await response.json();
          msg = body.message || body.error || msg;
        } catch (_) {
          // Ignore parse errors — use status text
        }
        throw new ApiError(response.status, msg);
      }

      // Return parsed JSON or null for empty responses
      const text = await response.text();
      return text ? JSON.parse(text) : null;
    } finally {
      clearTimeout(timerId);
    }
  }

  /** GET request */
  get(path) {
    return this._request(this._url(path), { method: "GET", headers: this.defaultHeaders });
  }

  /** POST request */
  post(path, body) {
    return this._request(this._url(path), {
      method: "POST",
      headers: this.defaultHeaders,
      body: JSON.stringify(body),
    });
  }

  /** PUT request */
  put(path, body) {
    return this._request(this._url(path), {
      method: "PUT",
      headers: this.defaultHeaders,
      body: JSON.stringify(body),
    });
  }

  /** DELETE request */
  delete(path) {
    return this._request(this._url(path), { method: "DELETE", headers: this.defaultHeaders });
  }
}

module.exports = { ApiClient, ApiError };
""",
    },
    {
        "name": "yaml_config",
        "filename": "docker-compose.yml",
        "file_type": "data",
        "content": """\
# Docker Compose configuration for the application stack
# Includes web, worker, database, and cache services

version: "3.9"

services:
  # ── Web server ──────────────────────────────────────────────────────────────
  web:
    # Build from the local Dockerfile
    build:
      context: .
      dockerfile: Dockerfile
    # Map container port 8000 to host port 8000
    ports:
      - "8000:8000"
    # Mount source code for hot-reload in development
    volumes:
      - .:/app
    # Inject environment variables
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/myapp
      - REDIS_URL=redis://cache:6379/0
      - SECRET_KEY=supersecretkey
      - DEBUG=true
    # Wait for dependencies before starting
    depends_on:
      - db
      - cache
    # Restart policy
    restart: unless-stopped

  # ── Background worker ───────────────────────────────────────────────────────
  worker:
    build:
      context: .
      dockerfile: Dockerfile
    command: python manage.py runworker
    volumes:
      - .:/app
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/myapp
      - REDIS_URL=redis://cache:6379/0
    depends_on:
      - db
      - cache
    restart: unless-stopped

  # ── PostgreSQL database ─────────────────────────────────────────────────────
  db:
    image: postgres:15-alpine
    volumes:
      - postgres_data:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
      - POSTGRES_DB=myapp
    ports:
      - "5432:5432"
    restart: unless-stopped

  # ── Redis cache ─────────────────────────────────────────────────────────────
  cache:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    restart: unless-stopped

volumes:
  postgres_data:
""",
    },
    {
        "name": "plain_text_prose",
        "filename": "notes.txt",
        "file_type": "text",
        "content": """\
Meeting Notes — Q3 Planning Session
Date: 2026-03-05
Attendees: Alice, Bob, Carol, Dave

Summary
-------
The team met to review Q3 priorities and finalize the roadmap. The main topics discussed
were feature delivery timelines, resource allocation, and cross-team dependencies.

Action Items
------------
1. Alice will update the project tracker with new milestones by end of week.
2. Bob is responsible for coordinating with the design team on the new dashboard mockups.
3. Carol will draft the API migration guide and share it for review by Friday.
4. Dave will investigate the performance regression reported in the staging environment.

Key Decisions
-------------
- The team agreed to push the v2.0 release to mid-Q3 to allow additional testing time.
- A new on-call rotation will be introduced starting next sprint.
- Budget for external tooling will be reviewed at the next steering committee meeting.

Open Questions
--------------
- What is the expected timeline for the third-party integration? (Owner: Alice)
- Do we need additional capacity for the load testing phase? (Owner: Bob)
- How will we handle backward compatibility for deprecated API endpoints? (Owner: Carol)

Next Meeting
------------
Date: 2026-03-12 at 10:00 AM PST
Agenda: Review action items, finalize API deprecation plan, Q3 retrospective prep.
""",
    },
    {
        "name": "typescript_interface",
        "filename": "types.ts",
        "file_type": "code",
        "content": """\
/**
 * Core domain types for the application.
 *
 * These interfaces define the shape of data flowing through the system.
 * All external API responses should be validated against these types.
 */

// ---------------------------------------------------------------------------
// User types
// ---------------------------------------------------------------------------

/**
 * Represents an authenticated user of the system.
 */
export interface User {
  /** Unique user identifier (UUID v4) */
  id: string;
  /** Display name (may differ from email username) */
  displayName: string;
  /** Primary email address — must be unique across all users */
  email: string;
  /** ISO 8601 timestamp of account creation */
  createdAt: string;
  /** ISO 8601 timestamp of last login, or null if never logged in */
  lastLoginAt: string | null;
  /** Role-based access level */
  role: UserRole;
  /** Optional metadata bag for extension without schema changes */
  metadata?: Record<string, unknown>;
}

/**
 * Allowed roles for user accounts.
 *
 * - admin: Full system access, can manage users and billing.
 * - editor: Can create and modify content, cannot manage users.
 * - viewer: Read-only access to published content.
 */
export type UserRole = "admin" | "editor" | "viewer";

// ---------------------------------------------------------------------------
// Pagination types
// ---------------------------------------------------------------------------

/**
 * Generic paginated response wrapper used by list endpoints.
 *
 * @template T  The item type contained in the page.
 */
export interface PaginatedResponse<T> {
  /** Items in the current page */
  items: T[];
  /** Total number of items across all pages */
  total: number;
  /** Current page number (1-indexed) */
  page: number;
  /** Maximum number of items per page */
  pageSize: number;
  /** Whether a next page exists */
  hasNextPage: boolean;
}

// ---------------------------------------------------------------------------
// API error types
// ---------------------------------------------------------------------------

/**
 * Standard error response returned by the API on failure.
 */
export interface ApiErrorResponse {
  /** Machine-readable error code (e.g. "NOT_FOUND", "VALIDATION_ERROR") */
  code: string;
  /** Human-readable error message */
  message: string;
  /** Optional field-level validation errors */
  details?: ValidationError[];
  /** Request trace ID for log correlation */
  traceId?: string;
}

/**
 * A single field-level validation error.
 */
export interface ValidationError {
  /** JSON path to the invalid field (e.g. "user.email") */
  field: string;
  /** Description of the validation failure */
  message: string;
}
""",
    },
    {
        "name": "python_test_file",
        "filename": "test_utils.py",
        "file_type": "code",
        "content": '''\
"""
Unit tests for utils module.

Tests cover load_config, flatten_dict, and retry functions.
Each test case is documented with its expected behaviour.
"""

import json
import os
import tempfile
import pytest

from myproject.utils import load_config, flatten_dict, retry, RetryError


# ─── load_config ─────────────────────────────────────────────────────────────

class TestLoadConfig:
    """Tests for the load_config helper."""

    def test_loads_valid_json(self, tmp_path):
        """load_config should return a dict for a valid JSON file."""
        cfg = {"key": "value", "number": 42}
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(cfg))

        result = load_config(str(cfg_file))

        assert result == cfg

    def test_raises_file_not_found(self):
        """load_config should raise FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config("/nonexistent/path/config.json")

    def test_raises_json_decode_error(self, tmp_path):
        """load_config should raise json.JSONDecodeError for malformed JSON."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{ this is not valid json }")

        with pytest.raises(json.JSONDecodeError):
            load_config(str(bad_file))

    def test_expands_home_directory(self, tmp_path, monkeypatch):
        """load_config should expand ~ in the path."""
        cfg = {"home": True}
        cfg_file = tmp_path / "home_config.json"
        cfg_file.write_text(json.dumps(cfg))

        # Patch expanduser so ~/config.json resolves to our temp file
        monkeypatch.setenv("HOME", str(tmp_path))
        result = load_config("~/home_config.json")

        assert result["home"] is True


# ─── flatten_dict ─────────────────────────────────────────────────────────────

class TestFlattenDict:
    """Tests for the flatten_dict helper."""

    def test_flat_dict_unchanged(self):
        """A non-nested dict should be returned as-is."""
        d = {"a": 1, "b": 2}
        assert flatten_dict(d) == {"a": 1, "b": 2}

    def test_nested_dict_flattened(self):
        """Nested keys should be joined with the separator."""
        d = {"a": {"b": {"c": 1}}}
        assert flatten_dict(d) == {"a.b.c": 1}

    def test_custom_separator(self):
        """Custom separator should be used between key levels."""
        d = {"x": {"y": 99}}
        assert flatten_dict(d, sep="/") == {"x/y": 99}

    def test_mixed_nesting(self):
        """Flat and nested keys should coexist correctly."""
        d = {"a": 1, "b": {"c": 2, "d": {"e": 3}}}
        result = flatten_dict(d)
        assert result == {"a": 1, "b.c": 2, "b.d.e": 3}


# ─── retry ───────────────────────────────────────────────────────────────────

class TestRetry:
    """Tests for the retry helper."""

    def test_succeeds_first_try(self):
        """retry should return immediately when fn succeeds."""
        result = retry(lambda: 42)
        assert result == 42

    def test_succeeds_after_failures(self):
        """retry should return when fn eventually succeeds."""
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Not ready")
            return "ok"

        # Use delay=0 to avoid slowing down the test suite
        result = retry(flaky, retries=3, delay=0.0)
        assert result == "ok"
        assert call_count == 3

    def test_raises_after_exhausted(self):
        """retry should raise RetryError when all attempts fail."""
        with pytest.raises(RetryError, match="All 3 attempts failed"):
            retry(lambda: (_ for _ in ()).throw(RuntimeError("boom")), retries=3, delay=0.0)
''',
    },
    {
        "name": "shell_script",
        "filename": "deploy.sh",
        "file_type": "code",
        "content": """\
#!/usr/bin/env bash
# deploy.sh — Deploy the application to production
#
# Usage:
#   ./deploy.sh [--env <environment>] [--tag <docker-tag>]
#
# Options:
#   --env   Target environment: staging or production (default: staging)
#   --tag   Docker image tag to deploy (default: latest)
#
# Requirements:
#   - kubectl configured with the target cluster context
#   - Docker image already pushed to the registry
#   - KUBECONFIG set to the appropriate cluster config

set -euo pipefail  # Exit on error, undefined vars, pipe failures

# ── Defaults ──────────────────────────────────────────────────────────────────
ENV="staging"
TAG="latest"
REGISTRY="registry.example.com/myapp"
NAMESPACE="myapp"

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --env)
      ENV="$2"
      shift 2
      ;;
    --tag)
      TAG="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# ── Validation ────────────────────────────────────────────────────────────────
if [[ "$ENV" != "staging" && "$ENV" != "production" ]]; then
  echo "Error: --env must be 'staging' or 'production'" >&2
  exit 1
fi

IMAGE="${REGISTRY}:${TAG}"
echo "Deploying ${IMAGE} to ${ENV}..."

# ── Pre-flight checks ─────────────────────────────────────────────────────────
echo "Running pre-flight checks..."

# Verify kubectl is available
if ! command -v kubectl &>/dev/null; then
  echo "Error: kubectl not found in PATH" >&2
  exit 1
fi

# Confirm the image exists in the registry
if ! docker manifest inspect "${IMAGE}" &>/dev/null; then
  echo "Error: Image not found in registry: ${IMAGE}" >&2
  exit 1
fi

echo "Pre-flight checks passed."

# ── Deploy ────────────────────────────────────────────────────────────────────
echo "Updating deployment in namespace ${NAMESPACE}..."
kubectl set image deployment/web web="${IMAGE}" --namespace="${NAMESPACE}"

# Wait for the rollout to complete (up to 5 minutes)
echo "Waiting for rollout to complete..."
kubectl rollout status deployment/web --namespace="${NAMESPACE}" --timeout=300s

echo "Deployment complete: ${IMAGE} → ${ENV}"
""",
    },
    {
        "name": "ci_yaml",
        "filename": ".github/workflows/ci.yml",
        "file_type": "data",
        "content": """\
# GitHub Actions CI workflow
# Runs on every push and pull request to the main branch

name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  # ── Lint and type-check ─────────────────────────────────────────────────────
  lint:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements-dev.txt

      - name: Run linter
        run: ruff check src tests

      - name: Run type checker
        run: mypy src

  # ── Unit tests ──────────────────────────────────────────────────────────────
  test:
    name: Unit Tests
    runs-on: ubuntu-latest
    needs: lint
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: "pip"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt -r requirements-dev.txt

      - name: Run tests with coverage
        run: |
          pytest tests/ --cov=src --cov-report=xml --cov-report=term-missing

      - name: Upload coverage report
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml
          fail_ci_if_error: false
""",
    },
]


# ---------------------------------------------------------------------------
# Compression benchmark implementation
# ---------------------------------------------------------------------------


def _run_single_compression_test(
    name: str,
    filename: str,
    file_type: str,
    content: str,
) -> dict[str, Any]:
    """
    Run a single compression test case and return detailed results.

    Returns a dict with:
      - name, filename, file_type
      - tokens_before, tokens_after, tokens_saved, compression_ratio_pct
      - time_ms
      - recipe_hits (list of recipe names that matched)
    """
    from .agent.compression.recipes import get_oss_engine

    engine = get_oss_engine()

    # Which OSS recipes match this filename?
    matched_recipes = engine.recipes_for_file(filename)
    recipe_hits = [r.name for r in matched_recipes]

    # Compress via the appropriate processor
    t0 = time.perf_counter()
    processor = get_processor(file_type)
    if processor:
        compressed = processor.process(content, filename)
    else:
        compressed = content
    elapsed_ms = (time.perf_counter() - t0) * 1000

    tokens_before = count_tokens(content)
    tokens_after = count_tokens(compressed)
    tokens_saved = tokens_before - tokens_after
    ratio_pct = round((tokens_saved / max(tokens_before, 1)) * 100, 1)

    return {
        "name": name,
        "filename": filename,
        "file_type": file_type,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "tokens_saved": tokens_saved,
        "compression_ratio_pct": ratio_pct,
        "time_ms": round(elapsed_ms, 3),
        "recipe_hits": recipe_hits,
    }


def run_compression_benchmark(
    file: Optional[str] = None,
    use_samples: bool = False,
    as_json: bool = False,
) -> None:
    """
    Run the compression benchmark suite.

    Args:
        file:        Path to a specific file to benchmark. If None, uses built-in samples.
        use_samples: Force use of built-in samples even if file is provided.
        as_json:     Print results as JSON instead of human-readable table.
    """
    results = []

    if file and not use_samples:
        # Single-file mode
        path = Path(file)
        if not path.exists():
            print(f"Error: file not found: {file}")
            return
        content = path.read_text(encoding="utf-8", errors="ignore")
        if not content.strip():
            print(f"Error: file is empty: {file}")
            return

        # Infer file type from extension
        from .walker import FILE_TYPES

        suffix = path.suffix.lower()
        file_type = FILE_TYPES.get(suffix, "text")

        result = _run_single_compression_test(
            name=path.name,
            filename=str(path),
            file_type=file_type,
            content=content,
        )
        results.append(result)
    else:
        # Built-in samples mode
        for sample in BUILTIN_SAMPLES:
            result = _run_single_compression_test(
                name=sample["name"],
                filename=sample["filename"],
                file_type=sample["file_type"],
                content=sample["content"],
            )
            results.append(result)

    if as_json:
        # Compute aggregate stats
        total_before = sum(r["tokens_before"] for r in results)
        total_after = sum(r["tokens_after"] for r in results)
        total_saved = sum(r["tokens_saved"] for r in results)
        overall_ratio = round((total_saved / max(total_before, 1)) * 100, 1)
        avg_time = round(statistics.mean(r["time_ms"] for r in results), 3)

        output = {
            "tests": results,
            "summary": {
                "total_tests": len(results),
                "tokens_before": total_before,
                "tokens_after": total_after,
                "tokens_saved": total_saved,
                "overall_compression_pct": overall_ratio,
                "avg_time_ms": avg_time,
            },
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable output
    header = f"{'TEST':<25} {'TYPE':<6} {'BEFORE':>7} {'AFTER':>7} {'SAVED':>6} {'RATIO':>7} {'TIME':>8}  RECIPES"
    sep = "─" * 100
    print()
    print("TokenPak Compression Benchmark")
    print(sep)
    print(header)
    print(sep)

    for r in results:
        recipe_str = ", ".join(r["recipe_hits"][:3]) if r["recipe_hits"] else "—"
        if len(r["recipe_hits"]) > 3:
            recipe_str += f" (+{len(r['recipe_hits']) - 3})"
        print(
            f"{r['name']:<25} {r['file_type']:<6} "
            f"{r['tokens_before']:>7,} {r['tokens_after']:>7,} "
            f"{r['tokens_saved']:>6,} {r['compression_ratio_pct']:>6.1f}%"
            f"{r['time_ms']:>7.1f}ms  {recipe_str}"
        )

    print(sep)

    # Summary row
    total_before = sum(r["tokens_before"] for r in results)
    total_after = sum(r["tokens_after"] for r in results)
    total_saved = sum(r["tokens_saved"] for r in results)
    overall_ratio = round((total_saved / max(total_before, 1)) * 100, 1)
    avg_time = statistics.mean(r["time_ms"] for r in results)
    total_recipe_hits = sum(len(r["recipe_hits"]) for r in results)

    print(
        f"{'TOTAL':<25} {'':6} "
        f"{total_before:>7,} {total_after:>7,} "
        f"{total_saved:>6,} {overall_ratio:>6.1f}%"
        f"{avg_time:>7.1f}ms  {total_recipe_hits} recipe hits"
    )
    print()
    print(f"  Tests run        : {len(results)}")
    print(f"  Total tokens in  : {total_before:,}")
    print(f"  Total tokens out : {total_after:,}")
    print(f"  Tokens saved     : {total_saved:,}  ({overall_ratio}% reduction)")
    print(f"  Avg process time : {avg_time:.1f}ms/file")
    print(f"  Recipe hits      : {total_recipe_hits}")
    print()


def benchmark_tokenization(texts: List[str], iterations: int = 3) -> dict[str, Any]:
    """Benchmark token counting with and without cache."""
    results: dict[str, Any] = {}

    if not texts:
        return {"error": "no texts to benchmark"}

    # Cold cache benchmark
    times = []
    for _ in range(iterations):
        clear_cache()
        start = time.perf_counter()
        for t in texts:
            count_tokens(t)
        times.append(time.perf_counter() - start)

    results["cold_cache_avg_ms"] = statistics.mean(times) * 1000

    # Warm cache benchmark (already populated from cold run)
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        for t in texts:
            count_tokens(t)
        times.append(time.perf_counter() - start)

    results["warm_cache_avg_ms"] = statistics.mean(times) * 1000
    results["cache_speedup"] = results["cold_cache_avg_ms"] / max(
        results["warm_cache_avg_ms"], 0.001
    )
    results["cache_info"] = str(cache_info())

    return results


def benchmark_processing(files: List[Tuple[str, str, int]], iterations: int = 3) -> dict[str, Any]:
    """Benchmark file processing (regex patterns)."""
    results: dict[str, Any] = {}

    # Group by type
    by_type: Dict[str, List[Tuple[str, str]]] = {}
    for path, file_type, _ in files:
        if file_type not in by_type:
            by_type[file_type] = []
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
            by_type[file_type].append((path, content))
        except Exception:
            pass

    for file_type, items in by_type.items():
        if not items:
            continue

        processor = get_processor(file_type)
        if not processor:
            continue

        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            for path, content in items:
                processor.process(content, path)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        avg_ms = statistics.mean(times) * 1000
        per_file_ms = avg_ms / len(items)
        results[file_type] = {
            "files": len(items),
            "total_ms": round(avg_ms, 2),
            "per_file_ms": round(per_file_ms, 3),
        }

    return results


def benchmark_indexing_baseline(directory: str, iterations: int = 3) -> dict[str, Any]:
    """Benchmark indexing WITHOUT optimizations (simulated baseline)."""
    results: dict[str, Any] = {}
    times = []

    for _ in range(iterations):
        clear_cache()  # No cache benefit

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/bench.db"
            # Simulate old behavior: individual commits, no batching
            import sqlite3

            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE blocks (
                    path TEXT PRIMARY KEY,
                    content_hash TEXT, version INTEGER, file_type TEXT,
                    raw_tokens INTEGER, compressed_tokens INTEGER,
                    compressed_content TEXT, quality_score REAL,
                    importance REAL, processed_at REAL
                )
            """)

            files = list(walk_directory(directory))
            start = time.perf_counter()
            processed = 0

            for path, file_type, _ in files:
                try:
                    content = Path(path).read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                if not content.strip():
                    continue

                processor = get_processor(file_type)
                if not processor:
                    continue

                compressed = processor.process(content, path)

                # Simulate old: uncached token counting
                raw_tokens = count_tokens_uncached(content)
                compressed_tokens = count_tokens_uncached(compressed)

                # Simulate old: individual commit per file
                conn.execute(
                    """
                    INSERT OR REPLACE INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                    (
                        path,
                        hashlib.sha256(content.encode()).hexdigest(),
                        1,
                        file_type,
                        raw_tokens,
                        compressed_tokens,
                        compressed,
                        1.0,
                        5.0,
                        time.time(),
                    ),
                )
                conn.commit()  # Commit per file = slow
                processed += 1

            elapsed = time.perf_counter() - start
            times.append((elapsed, processed))
            conn.close()

    avg_time = statistics.mean([t[0] for t in times])
    avg_files = statistics.mean([t[1] for t in times])

    results["total_files"] = int(avg_files)
    results["total_ms"] = round(avg_time * 1000, 2)
    results["per_file_ms"] = round((avg_time * 1000) / max(avg_files, 1), 3)
    results["files_per_second"] = round(avg_files / max(avg_time, 0.001), 1)

    return results


def benchmark_indexing_optimized(directory: str, iterations: int = 3) -> dict[str, Any]:
    """Benchmark indexing WITH all optimizations."""
    results: dict[str, Any] = {}
    times = []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/bench.db"
            registry = BlockRegistry(db_path)
            files = list(walk_directory(directory))

            start = time.perf_counter()
            processed = 0

            with registry.batch_transaction() as conn:
                for path, file_type, _ in files:
                    try:
                        content = Path(path).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue

                    if not content.strip():
                        continue

                    processor = get_processor(file_type)
                    if not processor:
                        continue

                    compressed = processor.process(content, path)

                    block = Block(
                        path=path,
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                        version=1,
                        file_type=file_type,
                        raw_tokens=count_tokens(content),
                        compressed_tokens=count_tokens(compressed),
                        compressed_content=compressed,
                        quality_score=1.0,
                        importance=5.0,
                    )
                    registry.add_block_batch(block, conn)
                    processed += 1

            elapsed = time.perf_counter() - start
            times.append((elapsed, processed))
            registry.close()

    avg_time = statistics.mean([t[0] for t in times])
    avg_files = statistics.mean([t[1] for t in times])

    results["total_files"] = int(avg_files)
    results["total_ms"] = round(avg_time * 1000, 2)
    results["per_file_ms"] = round((avg_time * 1000) / max(avg_files, 1), 3)
    results["files_per_second"] = round(avg_files / max(avg_time, 0.001), 1)

    return results


def benchmark_search(
    registry: BlockRegistry, queries: List[str], iterations: int = 3
) -> dict[str, Any]:
    """Benchmark search operations."""
    results: dict[str, Any] = {}

    if not queries:
        return {"error": "no queries"}

    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        for q in queries:
            registry.search(q, top_k=10)
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    avg_ms = statistics.mean(times) * 1000
    results["queries"] = len(queries)
    results["total_ms"] = round(avg_ms, 2)
    results["per_query_ms"] = round(avg_ms / len(queries), 3)

    return results


def run_benchmark(directory: str, iterations: int = 3, compare: bool = False):
    """Run full benchmark suite with optional baseline comparison."""
    print("TokenPak Latency Benchmark")
    print(f"Directory: {directory}")
    print(f"Iterations: {iterations}")
    print(f"Compare mode: {'ON' if compare else 'OFF'}")
    print("=" * 60)

    # Collect files
    files = list(walk_directory(directory))
    print(f"Found {len(files)} files")

    # Read file contents
    texts = []
    for path, _, _ in files:
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
            texts.append(content)
        except Exception:
            pass

    print(f"Read {len(texts)} files\n")

    # 1. Tokenization benchmark
    print("1. TOKEN COUNTING")
    token_results = benchmark_tokenization(texts, iterations)
    print(f"   Cold cache: {token_results['cold_cache_avg_ms']:.2f}ms")
    print(f"   Warm cache: {token_results['warm_cache_avg_ms']:.2f}ms")
    print(f"   Speedup: {token_results['cache_speedup']:.1f}x")
    print()

    # 2. Processing benchmark
    print("2. FILE PROCESSING (regex)")
    proc_results = benchmark_processing(files, iterations)
    for ftype, stats in proc_results.items():
        print(f"   {ftype}: {stats['per_file_ms']:.3f}ms/file ({stats['files']} files)")
    print()

    # 3. Indexing benchmark
    if compare:
        print("3. INDEXING — BASELINE vs OPTIMIZED")
        print("   [baseline] Running without optimizations...")
        baseline = benchmark_indexing_baseline(directory, iterations)
        print(
            f"   [baseline] {baseline['total_ms']:.2f}ms | {baseline['files_per_second']:.1f} files/sec"
        )

        print("   [optimized] Running with all optimizations...")
        optimized = benchmark_indexing_optimized(directory, iterations)
        print(
            f"   [optimized] {optimized['total_ms']:.2f}ms | {optimized['files_per_second']:.1f} files/sec"
        )

        speedup = baseline["total_ms"] / max(optimized["total_ms"], 0.001)
        improvement = ((baseline["total_ms"] - optimized["total_ms"]) / baseline["total_ms"]) * 100
        print(f"   SPEEDUP: {speedup:.2f}x ({improvement:.1f}% faster)")
        index_results = optimized
    else:
        print("3. FULL INDEXING")
        index_results = benchmark_indexing_optimized(directory, iterations)
        print(
            f"   Total: {index_results['total_ms']:.2f}ms for {index_results['total_files']} files"
        )
        print(f"   Per file: {index_results['per_file_ms']:.3f}ms")
        print(f"   Throughput: {index_results['files_per_second']:.1f} files/sec")
    print()

    # 4. Search benchmark
    print("4. SEARCH")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/bench.db"
        registry = BlockRegistry(db_path)

        # Index first
        with registry.batch_transaction() as conn:
            for path, file_type, _ in files:
                try:
                    content = Path(path).read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                processor = get_processor(file_type)
                if not processor:
                    continue

                compressed = processor.process(content, path)

                block = Block(
                    path=path,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    version=1,
                    file_type=file_type,
                    raw_tokens=count_tokens(content),
                    compressed_tokens=count_tokens(compressed),
                    compressed_content=compressed,
                    quality_score=1.0,
                    importance=5.0,
                )
                registry.add_block_batch(block, conn)

        queries = ["import", "function", "class", "def", "return", "error", "config", "data"]
        search_results = benchmark_search(registry, queries, iterations)
        print(
            f"   Per query: {search_results['per_query_ms']:.3f}ms ({search_results['queries']} queries)"
        )
        registry.close()

    print()
    print("=" * 60)
    print("SUMMARY")
    print(f"  Token cache speedup: {token_results['cache_speedup']:.1f}x")
    print(f"  Indexing throughput: {index_results['files_per_second']:.1f} files/sec")
    print(f"  Search latency: {search_results['per_query_ms']:.3f}ms/query")

    if compare:
        print(f"  Indexing improvement: {speedup:.2f}x faster vs baseline")


if __name__ == "__main__":
    import sys

    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    compare = "--compare" in sys.argv
    run_benchmark(directory, compare=compare)
