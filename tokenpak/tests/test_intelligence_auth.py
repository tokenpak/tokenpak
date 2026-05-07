# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for tokenpak.proxy.intelligence.auth.

Covers:
- LicenseTier enum values and TIER_RATE_LIMITS mapping
- PIIScrubFilter — redacts API keys/bearer tokens from log records
- APIKeyValidator — init, env-key loading, register, lookup, validate
- RateLimiter — allowed/denied paths, window reset, enterprise unlimited
- TokenPakAuthMiddleware — bypass paths, 401 auth, 429 rate-limit, 200 pass-through

All external I/O is mocked. No live network or filesystem calls.
"""

from __future__ import annotations

import logging
import os
import time
import unittest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# LicenseTier + TIER_RATE_LIMITS
# ---------------------------------------------------------------------------


class TestLicenseTier(unittest.TestCase):
    def setUp(self):
        from tokenpak.proxy.intelligence.auth import TIER_RATE_LIMITS, LicenseTier

        self.LicenseTier = LicenseTier
        self.TIER_RATE_LIMITS = TIER_RATE_LIMITS

    def test_enum_values(self):
        self.assertEqual(self.LicenseTier.FREE.value, "free")
        self.assertEqual(self.LicenseTier.PRO.value, "pro")
        self.assertEqual(self.LicenseTier.TEAM.value, "team")
        self.assertEqual(self.LicenseTier.ENTERPRISE.value, "enterprise")

    def test_tier_rate_limits_free(self):
        self.assertEqual(self.TIER_RATE_LIMITS[self.LicenseTier.FREE], 20)

    def test_tier_rate_limits_pro(self):
        self.assertEqual(self.TIER_RATE_LIMITS[self.LicenseTier.PRO], 100)

    def test_tier_rate_limits_team(self):
        self.assertEqual(self.TIER_RATE_LIMITS[self.LicenseTier.TEAM], 500)

    def test_tier_rate_limits_enterprise_unlimited(self):
        self.assertIsNone(self.TIER_RATE_LIMITS[self.LicenseTier.ENTERPRISE])

    def test_str_subclass(self):
        # LicenseTier(str, Enum) — must be usable as a plain string
        self.assertIsInstance(self.LicenseTier.PRO, str)


# ---------------------------------------------------------------------------
# PIIScrubFilter
# ---------------------------------------------------------------------------


class TestPIIScrubFilter(unittest.TestCase):
    def setUp(self):
        from tokenpak.proxy.intelligence.auth import PIIScrubFilter

        self.filt = PIIScrubFilter()

    def _make_record(self, msg: str, *args) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=args, exc_info=None,
        )
        return record

    def test_redacts_x_tokenpak_key(self):
        rec = self._make_record("X-TokenPak-Key: supersecret123")
        self.filt.filter(rec)
        self.assertIn("[REDACTED]", rec.msg)
        self.assertNotIn("supersecret123", rec.msg)

    def test_redacts_bearer_token(self):
        rec = self._make_record("Authorization: Bearer tok_abc987xyz")
        self.filt.filter(rec)
        self.assertIn("[REDACTED]", rec.msg)
        self.assertNotIn("tok_abc987xyz", rec.msg)

    def test_redacts_api_key_json_field(self):
        rec = self._make_record('"api_key": "secret_value"')
        self.filt.filter(rec)
        self.assertIn("[REDACTED]", rec.msg)
        self.assertNotIn("secret_value", rec.msg)

    def test_redacts_token_equals(self):
        rec = self._make_record("token=mysecrettoken")
        self.filt.filter(rec)
        self.assertIn("[REDACTED]", rec.msg)
        self.assertNotIn("mysecrettoken", rec.msg)

    def test_innocuous_message_unchanged(self):
        rec = self._make_record("User logged in successfully")
        self.filt.filter(rec)
        self.assertEqual(rec.msg, "User logged in successfully")

    def test_filter_returns_true(self):
        rec = self._make_record("anything")
        result = self.filt.filter(rec)
        self.assertTrue(result)

    def test_args_cleared(self):
        rec = self._make_record("key=%s", "secretvalue")
        self.filt.filter(rec)
        self.assertEqual(rec.args, ())

    def test_case_insensitive_x_tokenpak_key(self):
        rec = self._make_record("x-tokenpak-key: lowercase_secret")
        self.filt.filter(rec)
        self.assertNotIn("lowercase_secret", rec.msg)


# ---------------------------------------------------------------------------
# APIKeyValidator
# ---------------------------------------------------------------------------


class TestAPIKeyValidatorInit(unittest.TestCase):
    def test_empty_init_no_keys(self):
        from tokenpak.proxy.intelligence.auth import APIKeyValidator

        with patch.dict(os.environ, {"TOKENPAK_ALLOWED_KEYS": ""}, clear=False):
            v = APIKeyValidator()
        self.assertIsNone(v.lookup("anykey"))

    def test_loads_env_keys(self):
        from tokenpak.proxy.intelligence.auth import APIKeyValidator, LicenseTier

        env = {"TOKENPAK_ALLOWED_KEYS": "testkey:pro,teamkey:team"}
        with patch.dict(os.environ, env, clear=False):
            v = APIKeyValidator()
        self.assertEqual(v.lookup("testkey"), LicenseTier.PRO)
        self.assertEqual(v.lookup("teamkey"), LicenseTier.TEAM)

    def test_invalid_tier_in_env_is_skipped(self):
        from tokenpak.proxy.intelligence.auth import APIKeyValidator

        env = {"TOKENPAK_ALLOWED_KEYS": "badkey:unknowntier"}
        with patch.dict(os.environ, env, clear=False):
            v = APIKeyValidator()
        self.assertIsNone(v.lookup("badkey"))

    def test_env_key_missing_colon_is_skipped(self):
        from tokenpak.proxy.intelligence.auth import APIKeyValidator

        env = {"TOKENPAK_ALLOWED_KEYS": "nokeyformat"}
        with patch.dict(os.environ, env, clear=False):
            v = APIKeyValidator()
        self.assertIsNone(v.lookup("nokeyformat"))

    def test_env_whitespace_trimmed(self):
        from tokenpak.proxy.intelligence.auth import APIKeyValidator, LicenseTier

        env = {"TOKENPAK_ALLOWED_KEYS": " trimmed : free "}
        with patch.dict(os.environ, env, clear=False):
            v = APIKeyValidator()
        self.assertEqual(v.lookup("trimmed"), LicenseTier.FREE)


class TestAPIKeyValidatorRegisterLookup(unittest.TestCase):
    def setUp(self):
        from tokenpak.proxy.intelligence.auth import APIKeyValidator

        with patch.dict(os.environ, {"TOKENPAK_ALLOWED_KEYS": ""}, clear=False):
            self.v = APIKeyValidator()

    def test_register_and_lookup(self):
        from tokenpak.proxy.intelligence.auth import LicenseTier

        self.v.register("k1", LicenseTier.ENTERPRISE)
        self.assertEqual(self.v.lookup("k1"), LicenseTier.ENTERPRISE)

    def test_lookup_unknown_key_returns_none(self):
        self.assertIsNone(self.v.lookup("does_not_exist"))

    def test_register_overwrites(self):
        from tokenpak.proxy.intelligence.auth import LicenseTier

        self.v.register("k1", LicenseTier.FREE)
        self.v.register("k1", LicenseTier.PRO)
        self.assertEqual(self.v.lookup("k1"), LicenseTier.PRO)


class TestAPIKeyValidatorValidate(unittest.TestCase):
    def setUp(self):
        from tokenpak.proxy.intelligence.auth import APIKeyValidator, LicenseTier

        with patch.dict(os.environ, {"TOKENPAK_ALLOWED_KEYS": ""}, clear=False):
            self.v = APIKeyValidator()
        self.v.register("valid_key", LicenseTier.PRO)
        self.LicenseTier = LicenseTier

    def test_valid_key_returns_ok_and_tier(self):
        ok, tier, reason = self.v.validate("valid_key")
        self.assertTrue(ok)
        self.assertEqual(tier, self.LicenseTier.PRO)
        self.assertEqual(reason, "")

    def test_missing_key_returns_false(self):
        ok, tier, reason = self.v.validate(None)
        self.assertFalse(ok)
        self.assertIsNone(tier)
        self.assertIn("Missing", reason)

    def test_empty_string_key_returns_false(self):
        ok, tier, reason = self.v.validate("")
        self.assertFalse(ok)
        self.assertIsNone(tier)

    def test_unknown_key_returns_false(self):
        ok, tier, reason = self.v.validate("garbage_key")
        self.assertFalse(ok)
        self.assertIsNone(tier)
        self.assertIn("Invalid", reason)


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiterEnterprise(unittest.TestCase):
    def setUp(self):
        from tokenpak.proxy.intelligence.auth import LicenseTier, RateLimiter

        self.limiter = RateLimiter()
        self.enterprise = LicenseTier.ENTERPRISE

    def test_enterprise_always_allowed(self):
        allowed, remaining, reset_ts = self.limiter.check("anykey", self.enterprise)
        self.assertTrue(allowed)
        self.assertEqual(remaining, 999_999)

    def test_enterprise_allows_many_requests(self):
        for _ in range(1000):
            allowed, _, _ = self.limiter.check("enterprise_key", self.enterprise)
            self.assertTrue(allowed)


class TestRateLimiterFree(unittest.TestCase):
    def setUp(self):
        from tokenpak.proxy.intelligence.auth import LicenseTier, RateLimiter

        self.limiter = RateLimiter()
        self.free = LicenseTier.FREE
        self.key = "free_test_key"

    def test_first_request_allowed(self):
        allowed, remaining, _ = self.limiter.check(self.key, self.free)
        self.assertTrue(allowed)
        self.assertEqual(remaining, 19)  # 20 - 1

    def test_depletes_over_limit(self):
        # Use 20 requests to exhaust the free limit
        for _ in range(20):
            self.limiter.check(self.key, self.free)
        # 21st should be denied
        allowed, remaining, _ = self.limiter.check(self.key, self.free)
        self.assertFalse(allowed)
        self.assertEqual(remaining, 0)

    def test_remaining_decrements(self):
        _, r0, _ = self.limiter.check(self.key, self.free)
        _, r1, _ = self.limiter.check(self.key, self.free)
        self.assertEqual(r1, r0 - 1)

    def test_window_resets(self):
        from tokenpak.proxy.intelligence.auth import RateLimiter

        limiter = RateLimiter()
        key = "window_reset_key"
        # Exhaust the free limit
        for _ in range(20):
            limiter.check(key, self.free)
        # Should be denied
        allowed_before, _, _ = limiter.check(key, self.free)
        self.assertFalse(allowed_before)

        # Simulate window expiry by rewinding bucket timestamp
        hk = limiter._hash(key)
        limiter._buckets[hk][1] = time.time() - 61  # push window_start back 61s

        allowed_after, _, _ = limiter.check(key, self.free)
        self.assertTrue(allowed_after)

    def test_reset_ts_is_future(self):
        _, _, reset_ts = self.limiter.check(self.key, self.free)
        self.assertGreater(reset_ts, int(time.time()))

    def test_thread_safety(self):
        import threading

        from tokenpak.proxy.intelligence.auth import LicenseTier, RateLimiter

        limiter = RateLimiter()
        key = "thread_key"
        allowed_count = []
        lock = threading.Lock()

        def do_check():
            allowed, _, _ = limiter.check(key, LicenseTier.FREE)
            if allowed:
                with lock:
                    allowed_count.append(True)

        threads = [threading.Thread(target=do_check) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not exceed the free limit of 20
        self.assertLessEqual(len(allowed_count), 20)


class TestRateLimiterHashing(unittest.TestCase):
    def test_hash_is_deterministic(self):
        from tokenpak.proxy.intelligence.auth import RateLimiter

        h1 = RateLimiter._hash("mykey")
        h2 = RateLimiter._hash("mykey")
        self.assertEqual(h1, h2)

    def test_different_keys_different_hash(self):
        from tokenpak.proxy.intelligence.auth import RateLimiter

        self.assertNotEqual(RateLimiter._hash("key1"), RateLimiter._hash("key2"))


# ---------------------------------------------------------------------------
# TokenPakAuthMiddleware (via Starlette TestClient)
# ---------------------------------------------------------------------------


class TestTokenPakAuthMiddlewareBypass(unittest.TestCase):
    """Bypass paths (/health, /metrics, /) do not require auth."""

    def _make_client(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from tokenpak.proxy.intelligence.auth import (
            APIKeyValidator,
            RateLimiter,
            TokenPakAuthMiddleware,
        )

        app = FastAPI()
        validator = APIKeyValidator()
        limiter = RateLimiter()
        app.add_middleware(TokenPakAuthMiddleware, validator=validator, limiter=limiter)

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.get("/metrics")
        async def metrics():
            return {"data": []}

        @app.get("/")
        async def root():
            return {"root": True}

        return TestClient(app, raise_server_exceptions=True)

    def test_health_no_auth_required(self):
        client = self._make_client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_metrics_no_auth_required(self):
        client = self._make_client()
        resp = client.get("/metrics")
        self.assertEqual(resp.status_code, 200)

    def test_root_no_auth_required(self):
        client = self._make_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_bypass_path_has_request_id_header(self):
        client = self._make_client()
        resp = client.get("/health")
        self.assertIn("x-request-id", resp.headers)


class TestTokenPakAuthMiddlewareAuth(unittest.TestCase):
    def _make_client_with_key(self, key, tier_str):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from tokenpak.proxy.intelligence.auth import (
            APIKeyValidator,
            LicenseTier,
            RateLimiter,
            TokenPakAuthMiddleware,
        )

        app = FastAPI()
        validator = APIKeyValidator()
        validator.register(key, LicenseTier(tier_str))
        limiter = RateLimiter()
        app.add_middleware(TokenPakAuthMiddleware, validator=validator, limiter=limiter)

        @app.get("/v1/test")
        async def protected():
            return {"protected": True}

        return TestClient(app, raise_server_exceptions=False)

    def test_missing_key_returns_401(self):
        client = self._make_client_with_key("k1", "pro")
        resp = client.get("/v1/test")
        self.assertEqual(resp.status_code, 401)

    def test_invalid_key_returns_401(self):
        client = self._make_client_with_key("k1", "pro")
        resp = client.get("/v1/test", headers={"X-TokenPak-Key": "wrong_key"})
        self.assertEqual(resp.status_code, 401)

    def test_valid_key_passes_through(self):
        client = self._make_client_with_key("goodkey", "pro")
        resp = client.get("/v1/test", headers={"X-TokenPak-Key": "goodkey"})
        self.assertEqual(resp.status_code, 200)

    def test_401_response_has_request_id(self):
        client = self._make_client_with_key("k1", "pro")
        resp = client.get("/v1/test")
        self.assertIn("x-request-id", resp.headers)

    def test_401_response_has_www_authenticate(self):
        client = self._make_client_with_key("k1", "pro")
        resp = client.get("/v1/test")
        self.assertIn("www-authenticate", resp.headers)


class TestTokenPakAuthMiddlewareRateLimit(unittest.TestCase):
    """Free tier exhausted → 429."""

    def _make_client_exhausted(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from tokenpak.proxy.intelligence.auth import (
            TIER_RATE_LIMITS,
            APIKeyValidator,
            LicenseTier,
            RateLimiter,
            TokenPakAuthMiddleware,
        )

        key = "rl_test_key"
        app = FastAPI()
        validator = APIKeyValidator()
        validator.register(key, LicenseTier.FREE)
        limiter = RateLimiter()
        app.add_middleware(TokenPakAuthMiddleware, validator=validator, limiter=limiter)

        @app.get("/v1/test")
        async def protected():
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)

        # Exhaust the free limit (20 req/min)
        limit = TIER_RATE_LIMITS[LicenseTier.FREE]
        for _ in range(limit):
            client.get("/v1/test", headers={"X-TokenPak-Key": key})

        return client, key

    def test_rate_limit_returns_429(self):
        client, key = self._make_client_exhausted()
        resp = client.get("/v1/test", headers={"X-TokenPak-Key": key})
        self.assertEqual(resp.status_code, 429)

    def test_429_has_retry_after_header(self):
        client, key = self._make_client_exhausted()
        resp = client.get("/v1/test", headers={"X-TokenPak-Key": key})
        self.assertIn("retry-after", resp.headers)

    def test_429_has_rate_limit_reset_header(self):
        client, key = self._make_client_exhausted()
        resp = client.get("/v1/test", headers={"X-TokenPak-Key": key})
        self.assertIn("x-ratelimit-reset", resp.headers)


class TestTokenPakAuthMiddlewareRateLimitHeaders(unittest.TestCase):
    """Successful requests include rate-limit headers."""

    def setUp(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from tokenpak.proxy.intelligence.auth import (
            APIKeyValidator,
            LicenseTier,
            RateLimiter,
            TokenPakAuthMiddleware,
        )

        self.key = "header_test_key"
        app = FastAPI()
        validator = APIKeyValidator()
        validator.register(self.key, LicenseTier.PRO)
        limiter = RateLimiter()
        app.add_middleware(TokenPakAuthMiddleware, validator=validator, limiter=limiter)

        @app.get("/v1/test")
        async def protected():
            return {"ok": True}

        self.client = TestClient(app, raise_server_exceptions=True)

    def test_success_has_x_request_id(self):
        resp = self.client.get("/v1/test", headers={"X-TokenPak-Key": self.key})
        self.assertIn("x-request-id", resp.headers)

    def test_success_has_ratelimit_limit_header(self):
        resp = self.client.get("/v1/test", headers={"X-TokenPak-Key": self.key})
        self.assertIn("x-ratelimit-limit", resp.headers)

    def test_success_has_ratelimit_remaining_header(self):
        resp = self.client.get("/v1/test", headers={"X-TokenPak-Key": self.key})
        self.assertIn("x-ratelimit-remaining", resp.headers)

    def test_enterprise_shows_unlimited_limit(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from tokenpak.proxy.intelligence.auth import (
            APIKeyValidator,
            LicenseTier,
            RateLimiter,
            TokenPakAuthMiddleware,
        )

        ent_key = "enterprise_header_key"
        app2 = FastAPI()
        v2 = APIKeyValidator()
        v2.register(ent_key, LicenseTier.ENTERPRISE)
        l2 = RateLimiter()
        app2.add_middleware(TokenPakAuthMiddleware, validator=v2, limiter=l2)

        @app2.get("/v1/test")
        async def ep2():
            return {}

        c2 = TestClient(app2)
        resp = c2.get("/v1/test", headers={"X-TokenPak-Key": ent_key})
        self.assertEqual(resp.headers.get("x-ratelimit-limit"), "unlimited")


if __name__ == "__main__":
    unittest.main()
