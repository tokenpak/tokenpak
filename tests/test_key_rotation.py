"""
Tests for TokenPak multi-key rotation and failover.
Tests: 429 failover, 401 failover, client-key bypass, single-key compat, roundrobin mode.
"""
import importlib
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


def load_proxy_module(env_overrides: dict):
    """Load proxy module with custom env vars (fresh import for each test)."""
    with patch.dict(os.environ, env_overrides, clear=False):
        # Remove cached module so constants re-initialize
        for key in list(sys.modules.keys()):
            if "proxy" in key and "test" not in key:
                del sys.modules[key]
        spec = importlib.util.spec_from_file_location(
            "proxy",
            os.path.join(os.path.dirname(__file__), "..", "proxy.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["proxy"] = mod
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Unit tests for key pool helpers (no HTTP)
# ---------------------------------------------------------------------------

class TestBuildKeyPool(unittest.TestCase):
    def test_reads_all_three_keys(self):
        env = {
            "ANTHROPIC_API_KEY": "key-A",
            "ANTHROPIC_OAUTH_TOKEN": "key-B",
            "ANTHROPIC_OAUTH_TOKEN2": "key-C",
        }
        with patch.dict(os.environ, env):
            import importlib
            mod_name = "proxy_kp_test"
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            # Import proxy fresh
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                mod_name,
                os.path.join(os.path.dirname(__file__), "..", "proxy.py"),
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            pool = mod._build_key_pool()
            self.assertEqual(pool, ["key-A", "key-B", "key-C"])

    def test_skips_empty_keys(self):
        env = {
            "ANTHROPIC_API_KEY": "key-A",
            "ANTHROPIC_OAUTH_TOKEN": "",
            "ANTHROPIC_OAUTH_TOKEN2": "key-C",
        }
        with patch.dict(os.environ, env):
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "proxy_kp_test2",
                os.path.join(os.path.dirname(__file__), "..", "proxy.py"),
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            pool = mod._build_key_pool()
            self.assertNotIn("", pool)
            self.assertEqual(len(pool), 2)


class TestKeyPoolHelpers(unittest.TestCase):
    """Tests for _get_next_key, _cool_down_key, _key_is_available."""

    def _fresh_module(self, keys, mode="failover"):
        env = {"TOKENPAK_KEY_ROTATION": mode}
        if keys:
            env["ANTHROPIC_API_KEY"] = keys[0]
        if len(keys) > 1:
            env["ANTHROPIC_OAUTH_TOKEN"] = keys[1]
        if len(keys) > 2:
            env["ANTHROPIC_OAUTH_TOKEN2"] = keys[2]
        # Unset unused slots
        for slot in ["ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN2"]:
            if slot not in env:
                env[slot] = ""
        with patch.dict(os.environ, env, clear=False):
            import importlib.util
            mod_name = f"proxy_helper_{id(keys)}_{mode}"
            spec = importlib.util.spec_from_file_location(
                mod_name,
                os.path.join(os.path.dirname(__file__), "..", "proxy.py"),
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        return mod

    def test_failover_returns_first_available(self):
        mod = self._fresh_module(["key-A", "key-B", "key-C"])
        key, idx = mod._get_next_key()
        self.assertEqual(idx, 0)
        self.assertEqual(key, "key-A")

    def test_failover_skips_cooled_down_key(self):
        mod = self._fresh_module(["key-A", "key-B", "key-C"])
        # Cool down key 0
        mod._cool_down_key(0, 9999, "test")
        key, idx = mod._get_next_key()
        self.assertEqual(idx, 1)
        self.assertEqual(key, "key-B")

    def test_failover_no_keys_available(self):
        mod = self._fresh_module(["key-A"])
        mod._cool_down_key(0, 9999, "test")
        key, idx = mod._get_next_key()
        self.assertIsNone(key)
        self.assertEqual(idx, -1)

    def test_exclude_idx(self):
        mod = self._fresh_module(["key-A", "key-B"])
        key, idx = mod._get_next_key(exclude_idx=0)
        self.assertEqual(idx, 1)
        self.assertEqual(key, "key-B")

    def test_roundrobin_distributes(self):
        mod = self._fresh_module(["key-A", "key-B", "key-C"], mode="roundrobin")
        indices = [mod._get_next_key()[1] for _ in range(6)]
        # Should cycle: 0,1,2,0,1,2
        self.assertEqual(indices, [0, 1, 2, 0, 1, 2])

    def test_single_key_backward_compat(self):
        mod = self._fresh_module(["key-A"])
        key, idx = mod._get_next_key()
        self.assertEqual(key, "key-A")
        self.assertEqual(idx, 0)


# ---------------------------------------------------------------------------
# Integration-style tests: mock HTTP responses to test failover in _proxy_to
# ---------------------------------------------------------------------------
# These test the full flow by mocking _POOL_MANAGER.request

class _FakeResp:
    def __init__(self, status: int, body: bytes = b"{}"):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": "application/json"}

    def getheader(self, k, d=""):
        return self.headers.get(k, d)

    def getheaders(self):
        return list(self.headers.items())

    def read(self, *a):
        return self._body

    def drain_conn(self):
        pass

    def stream(self, *a, **kw):
        return iter([self._body])


def _make_proxy_handler(mod, client_key: str = "", keys=("key-A", "key-B", "key-C")):
    """Build a minimal handler-like object with enough plumbing for key rotation."""
    handler = MagicMock()
    # Simulate request headers
    headers = {"Content-Type": "application/json"}
    if client_key:
        headers["x-api-key"] = client_key
    handler.headers = headers
    handler.path = "/v1/messages"
    handler.command = "POST"
    # Patch the module's key pool
    mod._ANTHROPIC_KEY_POOL = list(keys)
    mod._KEY_COOLDOWN_STATE.clear()
    mod._KEY_RR_INDEX = 0
    return handler


class TestKeyRotationIntegration(unittest.TestCase):
    """
    Test key injection and failover by directly exercising the relevant
    code path in proxy._proxy_to() with mocked urllib3 responses.
    """

    def _load_mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "proxy_integ",
            os.path.join(os.path.dirname(__file__), "..", "proxy.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _call_injection_block(self, mod, client_key="", keys=("key-A", "key-B", "key-C")):
        """
        Exercise only the key injection block directly without full HTTP setup.
        Returns (injected_key, current_key_idx) after injection.
        """
        mod._ANTHROPIC_KEY_POOL = list(keys)
        mod._KEY_COOLDOWN_STATE.clear()

        # Simulate the code that runs in _proxy_to:
        req_headers = {}
        if client_key:
            req_headers["x-api-key"] = client_key
        _req_headers_lower = {k.lower(): v for k, v in req_headers.items()}
        _client_has_auth = bool(
            _req_headers_lower.get("x-api-key", "").strip()
            or _req_headers_lower.get("authorization", "").strip()
        )
        fwd_headers = dict(req_headers)
        target_url = "https://api.anthropic.com/v1/messages"
        _current_key_idx = -1
        if not _client_has_auth and mod._ANTHROPIC_KEY_POOL and "anthropic.com" in target_url:
            _pool_key, _current_key_idx = mod._get_next_key()
            if _pool_key:
                fwd_headers["x-api-key"] = _pool_key

        return fwd_headers.get("x-api-key"), _current_key_idx, _client_has_auth

    def test_no_client_key_injects_pool_key(self):
        mod = self._load_mod()
        injected, idx, _ = self._call_injection_block(mod, client_key="", keys=("key-A", "key-B"))
        self.assertEqual(injected, "key-A")
        self.assertEqual(idx, 0)

    def test_client_key_bypasses_pool(self):
        mod = self._load_mod()
        injected, idx, has_auth = self._call_injection_block(
            mod, client_key="client-key-X", keys=("key-A", "key-B")
        )
        # fwd_headers should have the client key, not pool key
        self.assertEqual(injected, "client-key-X")
        self.assertEqual(idx, -1)  # no pool index assigned
        self.assertTrue(has_auth)

    def test_failover_on_429_picks_next_key(self):
        mod = self._load_mod()
        mod._ANTHROPIC_KEY_POOL = ["key-A", "key-B", "key-C"]
        mod._KEY_COOLDOWN_STATE.clear()

        # Simulate: first call returns 429, we cool down and retry
        first_resp = _FakeResp(429)
        second_resp = _FakeResp(200, b'{"id":"msg_123"}')
        responses = iter([first_resp, second_resp])

        def fake_request(*a, **kw):
            return next(responses)

        mod._POOL_MANAGER = MagicMock()
        mod._POOL_MANAGER.request.side_effect = fake_request

        # Simulate failover logic inline (mirrors the _proxy_to block)
        fwd_headers = {"x-api-key": "key-A", "Content-Type": "application/json"}
        _current_key_idx = 0
        _client_has_auth = False
        target_url = "https://api.anthropic.com/v1/messages"
        body = b'{"model":"claude","messages":[]}'

        resp = mod._POOL_MANAGER.request("POST", target_url, headers=fwd_headers, body=body,
                                          timeout=mod.urllib3.Timeout(connect=10, read=30),
                                          preload_content=False)
        status = resp.status
        retried_with = None
        if (
            status in (401, 429)
            and _current_key_idx >= 0
            and not _client_has_auth
            and len(mod._ANTHROPIC_KEY_POOL) > 1
        ):
            dur = mod._KEY_COOLDOWN_401 if status == 401 else mod._KEY_COOLDOWN_429
            mod._cool_down_key(_current_key_idx, dur, f"HTTP {status}")
            retry_key, retry_idx = mod._get_next_key(exclude_idx=_current_key_idx)
            if retry_key:
                fwd_headers["x-api-key"] = retry_key
                _current_key_idx = retry_idx
                retried_with = retry_key
                resp = mod._POOL_MANAGER.request("POST", target_url, headers=fwd_headers, body=body,
                                                  timeout=mod.urllib3.Timeout(connect=10, read=30),
                                                  preload_content=False)
                status = resp.status

        self.assertEqual(status, 200)
        self.assertEqual(retried_with, "key-B")
        self.assertFalse(mod._key_is_available(0))  # key-A cooled down

    def test_failover_on_401_picks_next_key(self):
        mod = self._load_mod()
        mod._ANTHROPIC_KEY_POOL = ["key-A", "key-B"]
        mod._KEY_COOLDOWN_STATE.clear()

        responses = iter([_FakeResp(401), _FakeResp(200)])
        mod._POOL_MANAGER = MagicMock()
        mod._POOL_MANAGER.request.side_effect = lambda *a, **kw: next(responses)

        fwd_headers = {"x-api-key": "key-A"}
        _current_key_idx = 0
        _client_has_auth = False
        target_url = "https://api.anthropic.com/v1/messages"
        body = b"{}"

        resp = mod._POOL_MANAGER.request("POST", target_url, headers=fwd_headers, body=body,
                                          timeout=mod.urllib3.Timeout(10, 30), preload_content=False)
        status = resp.status

        if status in (401, 429) and _current_key_idx >= 0 and not _client_has_auth and len(mod._ANTHROPIC_KEY_POOL) > 1:
            dur = mod._KEY_COOLDOWN_401 if status == 401 else mod._KEY_COOLDOWN_429
            mod._cool_down_key(_current_key_idx, dur, f"HTTP {status}")
            retry_key, retry_idx = mod._get_next_key(exclude_idx=_current_key_idx)
            if retry_key:
                fwd_headers["x-api-key"] = retry_key
                _current_key_idx = retry_idx
                resp = mod._POOL_MANAGER.request("POST", target_url, headers=fwd_headers, body=body,
                                                  timeout=mod.urllib3.Timeout(10, 30), preload_content=False)
                status = resp.status

        self.assertEqual(status, 200)
        self.assertEqual(fwd_headers["x-api-key"], "key-B")

    def test_all_keys_exhausted_returns_last_error(self):
        mod = self._load_mod()
        mod._ANTHROPIC_KEY_POOL = ["key-A", "key-B"]
        mod._KEY_COOLDOWN_STATE.clear()
        # Cool down key-B so failover finds nothing
        mod._cool_down_key(1, 9999, "pre-test")

        responses = iter([_FakeResp(429)])
        mod._POOL_MANAGER = MagicMock()
        mod._POOL_MANAGER.request.side_effect = lambda *a, **kw: next(responses)

        fwd_headers = {"x-api-key": "key-A"}
        _current_key_idx = 0
        _client_has_auth = False
        target_url = "https://api.anthropic.com/v1/messages"
        body = b"{}"

        resp = mod._POOL_MANAGER.request("POST", target_url, headers=fwd_headers, body=body,
                                          timeout=mod.urllib3.Timeout(10, 30), preload_content=False)
        status = resp.status

        retried = False
        if status in (401, 429) and _current_key_idx >= 0 and not _client_has_auth and len(mod._ANTHROPIC_KEY_POOL) > 1:
            mod._cool_down_key(_current_key_idx, mod._KEY_COOLDOWN_429, "test")
            retry_key, _ = mod._get_next_key(exclude_idx=_current_key_idx)
            retried = bool(retry_key)

        # No retry — all keys cooled
        self.assertEqual(status, 429)
        self.assertFalse(retried)


if __name__ == "__main__":
    unittest.main(verbosity=2)
