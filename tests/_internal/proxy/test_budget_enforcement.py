"""tests/proxy/test_budget_enforcement.py

AC-1.2 Verification — BudgetController.check() wired; 429 budget_exceeded response (TRIX-02 / pmgtm).

Tests:
  - BudgetController.check() unit tests (all four cases)
  - BUDGET_MONTHLY_USD module constant loaded from env var
  - HTTP 429 with documented shape when spend exceeds limit
  - No 429 budget_exceeded when spend is below limit
  - Budget unset → BUDGET_MONTHLY_USD is None → check never blocks
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest  # noqa: F401 — kept for downstream pytest fixtures + markers

# TSR-07 / WS-F (2026-05-08) — relocated to tests/_internal/proxy/.
# Default OSS gate excludes this directory via pyproject.toml
# `norecursedirs`; the previous TSR-01-followup module-level
# importorskip is no longer needed. See tests/_internal/README.md.

# ---------------------------------------------------------------------------
# Repo root + path to the standalone proxy.py
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROXY_PATH = _REPO_ROOT / "proxy.py"


def _reload_config_loader():
    """Reload the config_loader chain so CONFIG_PATH picks up current TOKENPAK_CONFIG env var."""
    try:
        import tokenpak._internal.config_loader as _icl
        importlib.reload(_icl)
        import tokenpak.config_loader as _cl
        importlib.reload(_cl)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Unit tests: BudgetController.check()
# ---------------------------------------------------------------------------

class TestBudgetControllerCheck:
    """Unit tests for the new BudgetController.check() method."""

    def test_check_unlimited_when_limit_is_none(self):
        """check(None, any_spend) must never return exceeded=True."""
        from tokenpak._internal.budget_controller import BudgetController
        bc = BudgetController()
        result = bc.check(None, 999.99)
        assert result.exceeded is False

    def test_check_ok_when_spend_below_limit(self):
        """check(50.0, 10.0) must return exceeded=False with correct fields."""
        from tokenpak._internal.budget_controller import BudgetController
        bc = BudgetController()
        result = bc.check(50.0, 10.0)
        assert result.exceeded is False
        assert result.limit_usd == pytest.approx(50.0)
        assert result.spent_usd == pytest.approx(10.0)

    def test_check_exceeded_when_spend_meets_or_exceeds_limit(self):
        """check(0.01, 0.05) must return exceeded=True with correct fields."""
        from tokenpak._internal.budget_controller import BudgetController
        bc = BudgetController()
        result = bc.check(0.01, 0.05)
        assert result.exceeded is True
        assert result.limit_usd == pytest.approx(0.01)
        assert result.spent_usd == pytest.approx(0.05)

    def test_check_result_has_reset_at_in_utc(self):
        """check() result must include a reset_at field in ISO 8601 UTC format."""
        from tokenpak._internal.budget_controller import BudgetController
        bc = BudgetController()
        result = bc.check(10.0, 15.0)
        assert isinstance(result.reset_at, str)
        assert result.reset_at.endswith("Z"), f"reset_at not UTC: {result.reset_at!r}"
        assert "T00:00:00Z" in result.reset_at, (
            f"reset_at should be start-of-month midnight: {result.reset_at!r}"
        )


# ---------------------------------------------------------------------------
# Integration tests: proxy HTTP enforcement
# ---------------------------------------------------------------------------

_ENV_KEYS = (
    "TOKENPAK_CONFIG",
    "TOKENPAK_BUDGET_MONTHLY_USD",
    "TOKENPAK_PORT",
    "TOKENPAK_PROFILE",
    "TOKENPAK_VALIDATION_GATE",
)
_MOD_BUDGET_SET = "_test_pv4_budget_set"
_MOD_BUDGET_UNSET = "_test_pv4_budget_unset"


def _stash_env():
    return {k: os.environ.pop(k) for k in _ENV_KEYS if k in os.environ}


def _restore_env(stashed):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in stashed.items():
        os.environ[k] = v
    _reload_config_loader()


def _load_proxy_module(mod_name: str) -> object:
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, _PROXY_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _start_server(mod, host: str, port: int):
    """Start the proxy server in a daemon thread; return the server object."""
    server = mod.ThreadedHTTPServer((host, port), mod.ForwardProxyHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.15)
    return server


def _post_messages(port: int, timeout: int = 5):
    """POST a minimal Anthropic-format request to the local proxy."""
    body = json.dumps({
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode()
    conn = HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request(
        "POST", "/v1/messages", body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    resp = conn.getresponse()
    status = resp.status
    resp_body = json.loads(resp.read())
    conn.close()
    return status, resp_body


class TestBudgetMonthlyUSDConfig:
    """Verify BUDGET_MONTHLY_USD is loaded from env var at module import time."""

    def test_budget_monthly_usd_is_set_when_env_var_present(self):
        stashed = _stash_env()
        os.environ["TOKENPAK_CONFIG"] = "/tmp/_tokenpak_test_nonexistent_TRIX02.yaml"
        os.environ["TOKENPAK_BUDGET_MONTHLY_USD"] = "0.01"
        _reload_config_loader()
        try:
            mod = _load_proxy_module(_MOD_BUDGET_SET)
            assert mod.BUDGET_MONTHLY_USD == pytest.approx(0.01), (
                f"Expected 0.01, got {mod.BUDGET_MONTHLY_USD}"
            )
        finally:
            sys.modules.pop(_MOD_BUDGET_SET, None)
            _restore_env(stashed)

    def test_budget_monthly_usd_is_none_when_env_var_absent(self):
        stashed = _stash_env()
        os.environ["TOKENPAK_CONFIG"] = "/tmp/_tokenpak_test_nonexistent_TRIX02.yaml"
        _reload_config_loader()
        try:
            mod = _load_proxy_module(_MOD_BUDGET_UNSET)
            assert mod.BUDGET_MONTHLY_USD is None, (
                f"Expected None when env var unset, got {mod.BUDGET_MONTHLY_USD}"
            )
        finally:
            sys.modules.pop(_MOD_BUDGET_UNSET, None)
            _restore_env(stashed)


class TestBudgetEnforcementHTTP:
    """Integration tests: proxy HTTP response when monthly budget is enforced."""

    @pytest.fixture(scope="class")
    def pv4_budget_set(self):
        """Load proxy with BUDGET_MONTHLY_USD=0.01."""
        stashed = _stash_env()
        os.environ["TOKENPAK_CONFIG"] = "/tmp/_tokenpak_test_nonexistent_TRIX02.yaml"
        os.environ["TOKENPAK_BUDGET_MONTHLY_USD"] = "0.01"
        _reload_config_loader()

        try:
            mod = _load_proxy_module(_MOD_BUDGET_SET)
        except Exception as exc:
            _restore_env(stashed)
            pytest.skip(f"proxy.py failed to load: {exc}")

        yield mod

        sys.modules.pop(_MOD_BUDGET_SET, None)
        _restore_env(stashed)

    def test_429_budget_exceeded_shape(self, pv4_budget_set):
        """Spend > limit → 429 with error.type=budget_exceeded and all four fields."""
        # Inject high spend into the module-level cache (fresh timestamp = no DB hit)
        pv4_budget_set._MONTHLY_SPEND_CACHE["usd"] = 999.0
        pv4_budget_set._MONTHLY_SPEND_CACHE["ts"] = time.time()

        server = _start_server(pv4_budget_set, "127.0.0.1", 18760)
        try:
            status, body = _post_messages(18760)
        finally:
            server.shutdown()

        assert status == 429, f"Expected 429, got {status}. Body: {body}"
        error = body.get("error", {})
        assert error.get("type") == "budget_exceeded", (
            f"Expected error.type='budget_exceeded', got {error.get('type')!r}"
        )
        assert "limit_usd" in error, "429 body missing 'limit_usd'"
        assert "spent_usd" in error, "429 body missing 'spent_usd'"
        assert "reset_at" in error, "429 body missing 'reset_at'"
        assert error["limit_usd"] == pytest.approx(0.01), (
            f"limit_usd mismatch: {error['limit_usd']}"
        )
        assert error["spent_usd"] == pytest.approx(999.0), (
            f"spent_usd mismatch: {error['spent_usd']}"
        )
        assert error["reset_at"].endswith("Z"), (
            f"reset_at not UTC: {error['reset_at']!r}"
        )

    def test_no_budget_exceeded_when_spend_is_zero(self, pv4_budget_set):
        """Spend = 0, limit = 0.01 → request must NOT return 429 budget_exceeded.

        If the proxy reaches the upstream (no budget block), it will attempt to
        connect to Anthropic and either get a non-429 response or a connection
        timeout/error — all of which confirm the budget gate was not triggered.
        """
        pv4_budget_set._MONTHLY_SPEND_CACHE["usd"] = 0.0
        pv4_budget_set._MONTHLY_SPEND_CACHE["ts"] = time.time()

        server = _start_server(pv4_budget_set, "127.0.0.1", 18761)
        try:
            # Use a short timeout: if we don't get a fast 429, the request
            # passed the budget gate and is attempting upstream forwarding.
            try:
                status, body = _post_messages(18761, timeout=2)
                if status == 429:
                    error = body.get("error", {})
                    assert error.get("type") != "budget_exceeded", (
                        "Got 429 budget_exceeded even though spend is $0.00"
                    )
                # Any other status (200, 401, 502…) confirms no budget block.
            except (TimeoutError, OSError, ConnectionError):
                # Request reached the upstream forwarding stage — not budget-blocked.
                pass
        finally:
            server.shutdown()

    def test_budget_blocked_total_increments(self, pv4_budget_set):
        """budget_blocked_total counter must increment on each blocked request."""
        pv4_budget_set._MONTHLY_SPEND_CACHE["usd"] = 999.0
        pv4_budget_set._MONTHLY_SPEND_CACHE["ts"] = time.time()
        before = pv4_budget_set.SESSION.get("budget_blocked_total", 0)

        server = _start_server(pv4_budget_set, "127.0.0.1", 18762)
        try:
            status, _ = _post_messages(18762)
        finally:
            server.shutdown()

        if status == 429:
            after = pv4_budget_set.SESSION.get("budget_blocked_total", 0)
            assert after > before, (
                f"budget_blocked_total did not increment: before={before}, after={after}"
            )

    def test_budget_unset_does_not_block(self):
        """When BUDGET_MONTHLY_USD is None, requests are never budget-blocked."""
        stashed = _stash_env()
        os.environ["TOKENPAK_CONFIG"] = "/tmp/_tokenpak_test_nonexistent_TRIX02b.yaml"
        _reload_config_loader()

        try:
            mod = _load_proxy_module(_MOD_BUDGET_UNSET)
        except Exception as exc:
            _restore_env(stashed)
            pytest.skip(f"proxy.py failed to load: {exc}")

        # Verify module constant
        assert mod.BUDGET_MONTHLY_USD is None

        # Inject high spend — should not matter since limit is None
        mod._MONTHLY_SPEND_CACHE["usd"] = 999.0
        mod._MONTHLY_SPEND_CACHE["ts"] = time.time()

        server = _start_server(mod, "127.0.0.1", 18763)
        try:
            try:
                status, body = _post_messages(18763, timeout=2)
                if status == 429:
                    error = body.get("error", {})
                    assert error.get("type") != "budget_exceeded", (
                        "Got 429 budget_exceeded even though BUDGET_MONTHLY_USD is None"
                    )
                # Any other status confirms no budget block.
            except (TimeoutError, OSError, ConnectionError):
                # Reached upstream forwarding — not budget-blocked.
                pass
        finally:
            server.shutdown()
            sys.modules.pop(_MOD_BUDGET_UNSET, None)
            _restore_env(stashed)
