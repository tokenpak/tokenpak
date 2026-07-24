# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for tokenpak.proxy.intelligence.server.

Covers:
- _cors_origins — env var parsing (wildcard, list, default)
- _estimate_tokens — tiktoken path + fallback
- CompressRequest model validators
- BudgetRequest model validators
- create_app — returns FastAPI instance
- GET /health liveness probe (no auth required)
- GET /health?deep=true — mocked deep checker
- GET /v1/status — tier + rate-limit info in response
- POST /v1/compress — modes, budget_tokens trim
- POST /v1/budget — fits / overage
- POST /v1/compress invalid mode → 422
- POST /v1/compress empty content → 422
- TOKENPAK_DISABLE_DOCS env var

No live network calls. Starlette TestClient is used for HTTP tests.
All external dependencies (tiktoken, deep_health checker) are mocked.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# _cors_origins
# ---------------------------------------------------------------------------


class TestCorsOrigins(unittest.TestCase):
    def _call(self, env_val=None):
        env = {}
        if env_val is not None:
            env["TOKENPAK_CORS_ORIGINS"] = env_val
        else:
            env.pop("TOKENPAK_CORS_ORIGINS", None)

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TOKENPAK_CORS_ORIGINS", None)
            if env_val is not None:
                os.environ["TOKENPAK_CORS_ORIGINS"] = env_val
            import importlib

            from tokenpak.proxy.intelligence import server as srv

            importlib.reload(srv)
            return srv._cors_origins()

    def test_wildcard_returns_star_list(self):
        from tokenpak.proxy.intelligence.server import _cors_origins

        with patch.dict(os.environ, {"TOKENPAK_CORS_ORIGINS": "*"}):
            result = _cors_origins()
        self.assertEqual(result, ["*"])

    def test_comma_separated_parsed(self):
        from tokenpak.proxy.intelligence.server import _cors_origins

        with patch.dict(os.environ, {"TOKENPAK_CORS_ORIGINS": "https://a.com,https://b.com"}):
            result = _cors_origins()
        self.assertIn("https://a.com", result)
        self.assertIn("https://b.com", result)

    def test_empty_env_returns_defaults(self):
        from tokenpak.proxy.intelligence.server import _cors_origins

        with patch.dict(os.environ, {"TOKENPAK_CORS_ORIGINS": ""}):
            result = _cors_origins()
        self.assertGreater(len(result), 0)
        # Should include the default localhost origins
        self.assertTrue(any("localhost" in o for o in result))

    def test_whitespace_stripped(self):
        from tokenpak.proxy.intelligence.server import _cors_origins

        with patch.dict(
            os.environ, {"TOKENPAK_CORS_ORIGINS": "  https://a.com ,  https://b.com  "}
        ):
            result = _cors_origins()
        self.assertIn("https://a.com", result)
        self.assertIn("https://b.com", result)


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens(unittest.TestCase):
    def setUp(self):
        from tokenpak.proxy.intelligence.server import _estimate_tokens

        self.fn = _estimate_tokens

    def test_fallback_no_tiktoken(self):
        # When tiktoken raises an exception, falls back to len/4
        with patch("tiktoken.encoding_for_model", side_effect=Exception("no tiktoken")):
            result = self.fn("a" * 400)
        self.assertEqual(result, 100)

    def test_fallback_minimum_is_1(self):
        with patch("tiktoken.encoding_for_model", side_effect=Exception("no tiktoken")):
            result = self.fn("a")  # 1 char / 4 = 0 → max(1, ...)
        self.assertEqual(result, 1)

    def test_fallback_proportional(self):
        with patch("tiktoken.encoding_for_model", side_effect=Exception("no tiktoken")):
            r1 = self.fn("a" * 100)
            r2 = self.fn("a" * 200)
        self.assertEqual(r2, r1 * 2)

    def test_tiktoken_path(self):
        mock_enc = MagicMock()
        mock_enc.encode.return_value = [1, 2, 3, 4, 5]  # 5 tokens
        with patch("tiktoken.encoding_for_model", return_value=mock_enc):
            result = self.fn("hello world foo")
        self.assertEqual(result, 5)


# ---------------------------------------------------------------------------
# CompressRequest / BudgetRequest validators
# ---------------------------------------------------------------------------


class TestCompressRequestValidator(unittest.TestCase):
    def _make(self, **kwargs):
        from tokenpak.proxy.intelligence.server import CompressRequest

        defaults = {"content": "hello world", "model": "gpt-4o", "mode": "hybrid"}
        defaults.update(kwargs)
        return CompressRequest(**defaults)

    def test_valid_strict_mode(self):
        req = self._make(mode="strict")
        self.assertEqual(req.mode, "strict")

    def test_valid_hybrid_mode(self):
        req = self._make(mode="hybrid")
        self.assertEqual(req.mode, "hybrid")

    def test_valid_aggressive_mode(self):
        req = self._make(mode="aggressive")
        self.assertEqual(req.mode, "aggressive")

    def test_invalid_mode_raises(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self._make(mode="bogus")

    def test_empty_content_raises(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self._make(content="")

    def test_model_sanitized(self):
        # sanitize_model_name should not raise for a clean name
        req = self._make(model="claude-3-opus")
        self.assertIsInstance(req.model, str)

    def test_optional_budget_tokens(self):
        req = self._make(budget_tokens=1000)
        self.assertEqual(req.budget_tokens, 1000)

    def test_default_model_is_gpt4o(self):
        req = self._make()
        self.assertEqual(req.model, "gpt-4o")


class TestBudgetRequestValidator(unittest.TestCase):
    def _make(self, **kwargs):
        from tokenpak.proxy.intelligence.server import BudgetRequest

        defaults = {"content": "hello world", "model": "gpt-4o", "target_tokens": 8000}
        defaults.update(kwargs)
        return BudgetRequest(**defaults)

    def test_valid_budget_request(self):
        req = self._make()
        self.assertEqual(req.target_tokens, 8000)

    def test_empty_content_raises(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self._make(content="")

    def test_default_target(self):
        req = self._make()
        self.assertEqual(req.target_tokens, 8000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_client(key="testkey", tier="pro"):
    """Return a TestClient with a pre-registered API key."""
    from starlette.testclient import TestClient

    from tokenpak.proxy.intelligence.auth import APIKeyValidator, LicenseTier, RateLimiter
    from tokenpak.proxy.intelligence.server import create_app

    validator = APIKeyValidator()
    validator.register(key, LicenseTier(tier))
    limiter = RateLimiter()
    app = create_app(validator=validator, limiter=limiter)
    return TestClient(app, raise_server_exceptions=False), key


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


class TestCreateApp(unittest.TestCase):
    def test_returns_fastapi_instance(self):
        from fastapi import FastAPI

        from tokenpak.proxy.intelligence.server import create_app

        app = create_app()
        self.assertIsInstance(app, FastAPI)

    def test_title(self):
        from tokenpak.proxy.intelligence.server import create_app

        app = create_app()
        self.assertIn("TokenPak", app.title)

    def test_disable_docs_env(self):
        from tokenpak.proxy.intelligence.server import create_app

        with patch.dict(os.environ, {"TOKENPAK_DISABLE_DOCS": "1"}):
            app = create_app()
        self.assertIsNone(app.docs_url)
        self.assertIsNone(app.redoc_url)

    def test_docs_enabled_by_default(self):
        from tokenpak.proxy.intelligence.server import create_app

        with patch.dict(os.environ, {"TOKENPAK_DISABLE_DOCS": "0"}):
            app = create_app()
        self.assertIsNotNone(app.docs_url)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint(unittest.TestCase):
    def setUp(self):
        self.client, self.key = _make_test_client()

    def test_liveness_no_auth_required(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_liveness_returns_ok_status(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.json()["status"], "ok")

    def test_liveness_includes_version(self):
        resp = self.client.get("/health")
        self.assertIn("version", resp.json())

    def test_deep_false_returns_fast(self):
        resp = self.client.get("/health?deep=false")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class TestDeepHealthEndpoint(unittest.TestCase):
    def _make_checker(self, status="ok"):
        from tokenpak.proxy.intelligence.deep_health import CheckResult, DeepHealthResult

        checks = {
            k: CheckResult(status=status)
            for k in ("anthropic", "openai", "database", "index", "memory", "disk")
        }
        return MagicMock(
            run=MagicMock(
                return_value=DeepHealthResult(status=status, checks=checks, duration_ms=5.0)
            )
        )

    def test_deep_true_returns_200_when_ok(self):
        client, _ = _make_test_client()
        checker = self._make_checker("ok")
        # get_checker is imported inside the endpoint: `from .deep_health import get_checker`
        with patch("tokenpak.proxy.intelligence.deep_health.get_checker", return_value=checker):
            resp = client.get("/health?deep=true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_deep_true_returns_503_when_error(self):
        client, _ = _make_test_client()
        checker = self._make_checker("error")
        with patch("tokenpak.proxy.intelligence.deep_health.get_checker", return_value=checker):
            resp = client.get("/health?deep=true")
        self.assertEqual(resp.status_code, 503)

    def test_deep_response_includes_checks(self):
        client, _ = _make_test_client()
        checker = self._make_checker("ok")
        with patch("tokenpak.proxy.intelligence.deep_health.get_checker", return_value=checker):
            resp = client.get("/health?deep=true")
        self.assertIn("checks", resp.json())


# ---------------------------------------------------------------------------
# GET /v1/status
# ---------------------------------------------------------------------------


class TestStatusEndpoint(unittest.TestCase):
    def test_status_authenticated(self):
        client, key = _make_test_client(key="statkey", tier="pro")
        resp = client.get("/v1/status", headers={"X-TokenPak-Key": key})
        self.assertEqual(resp.status_code, 200)

    def test_status_returns_tier(self):
        client, key = _make_test_client(key="tierkey", tier="team")
        resp = client.get("/v1/status", headers={"X-TokenPak-Key": key})
        self.assertEqual(resp.json()["tier"], "team")

    def test_status_returns_rate_limit(self):
        client, key = _make_test_client(key="rlkey", tier="pro")
        resp = client.get("/v1/status", headers={"X-TokenPak-Key": key})
        self.assertEqual(resp.json()["rate_limit_per_minute"], 100)

    def test_status_enterprise_shows_unlimited(self):
        client, key = _make_test_client(key="entkey", tier="enterprise")
        resp = client.get("/v1/status", headers={"X-TokenPak-Key": key})
        self.assertEqual(resp.json()["rate_limit_per_minute"], "unlimited")

    def test_status_unauthenticated_returns_401(self):
        client, _ = _make_test_client()
        resp = client.get("/v1/status")
        self.assertEqual(resp.status_code, 401)

    def test_status_has_server_time(self):
        client, key = _make_test_client(key="stkey", tier="free")
        resp = client.get("/v1/status", headers={"X-TokenPak-Key": key})
        self.assertIn("server_time", resp.json())


# ---------------------------------------------------------------------------
# POST /v1/compress
# ---------------------------------------------------------------------------


class TestCompressEndpoint(unittest.TestCase):
    def setUp(self):
        self.client, self.key = _make_test_client(key="cmpkey", tier="pro")
        self.headers = {"X-TokenPak-Key": self.key}

    def _post(self, body):
        return self.client.post("/v1/compress", json=body, headers=self.headers)

    def test_basic_compress(self):
        resp = self._post({"content": "hello world this is a test", "mode": "hybrid"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("compressed", data)
        self.assertIn("compression_ratio", data)

    def test_strict_mode_keeps_more(self):
        content = " ".join(["word"] * 100)
        resp_strict = self._post({"content": content, "mode": "strict"})
        resp_aggressive = self._post({"content": content, "mode": "aggressive"})
        self.assertEqual(resp_strict.status_code, 200)
        self.assertEqual(resp_aggressive.status_code, 200)
        # strict should produce fewer words dropped (more tokens remaining)
        self.assertGreaterEqual(
            resp_strict.json()["compressed_tokens"],
            resp_aggressive.json()["compressed_tokens"],
        )

    def test_invalid_mode_returns_422(self):
        resp = self._post({"content": "some text", "mode": "invalid_mode"})
        self.assertEqual(resp.status_code, 422)

    def test_empty_content_returns_422(self):
        resp = self._post({"content": "", "mode": "hybrid"})
        self.assertEqual(resp.status_code, 422)

    def test_unauthenticated_returns_401(self):
        resp = self.client.post("/v1/compress", json={"content": "test", "mode": "hybrid"})
        self.assertEqual(resp.status_code, 401)

    def test_response_has_request_id(self):
        resp = self._post({"content": "hello", "mode": "hybrid"})
        self.assertIn("request_id", resp.json())

    def test_original_tokens_positive(self):
        resp = self._post({"content": "hello world test content here", "mode": "hybrid"})
        self.assertGreater(resp.json()["original_tokens"], 0)

    def test_budget_tokens_trims_output(self):
        # Large content with a tiny budget
        content = " ".join(["word"] * 500)
        resp = self._post({"content": content, "mode": "hybrid", "budget_tokens": 5})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertLessEqual(data["compressed_tokens"], 20)  # should be much smaller

    def test_model_reflected_in_response(self):
        resp = self._post({"content": "hello world", "mode": "hybrid", "model": "gpt-4o"})
        self.assertEqual(resp.json()["model"], "gpt-4o")

    def test_compression_ratio_between_0_and_1(self):
        content = " ".join(["word"] * 50)
        resp = self._post({"content": content, "mode": "aggressive"})
        ratio = resp.json()["compression_ratio"]
        self.assertGreaterEqual(ratio, 0.0)
        self.assertLessEqual(ratio, 1.0)


# ---------------------------------------------------------------------------
# POST /v1/budget
# ---------------------------------------------------------------------------


class TestBudgetEndpoint(unittest.TestCase):
    def setUp(self):
        self.client, self.key = _make_test_client(key="budgkey", tier="pro")
        self.headers = {"X-TokenPak-Key": self.key}

    def _post(self, body):
        return self.client.post("/v1/budget", json=body, headers=self.headers)

    def test_basic_budget(self):
        resp = self._post({"content": "hello world", "target_tokens": 8000})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("estimated_tokens", data)
        self.assertIn("fits_in_budget", data)

    def test_fits_in_budget_true_when_small(self):
        resp = self._post({"content": "hi", "target_tokens": 10000})
        self.assertTrue(resp.json()["fits_in_budget"])
        self.assertEqual(resp.json()["overage_tokens"], 0)

    def test_fits_in_budget_false_when_large(self):
        # 4000 chars / 4 = 1000 tokens; target = 10
        resp = self._post({"content": "a" * 4000, "target_tokens": 10})
        self.assertFalse(resp.json()["fits_in_budget"])
        self.assertGreater(resp.json()["overage_tokens"], 0)

    def test_overage_matches_estimated_minus_target(self):
        resp = self._post({"content": "a" * 4000, "target_tokens": 10})
        data = resp.json()
        expected_overage = max(0, data["estimated_tokens"] - 10)
        self.assertEqual(data["overage_tokens"], expected_overage)

    def test_unauthenticated_returns_401(self):
        resp = self.client.post("/v1/budget", json={"content": "test", "target_tokens": 100})
        self.assertEqual(resp.status_code, 401)

    def test_empty_content_returns_422(self):
        resp = self._post({"content": "", "target_tokens": 8000})
        self.assertEqual(resp.status_code, 422)

    def test_response_has_request_id(self):
        resp = self._post({"content": "test content", "target_tokens": 8000})
        self.assertIn("request_id", resp.json())

    def test_model_reflected_in_response(self):
        resp = self._post({"content": "hello world", "model": "gpt-4o", "target_tokens": 8000})
        self.assertEqual(resp.json()["model"], "gpt-4o")


if __name__ == "__main__":
    unittest.main()
