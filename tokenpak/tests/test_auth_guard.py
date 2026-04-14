"""
Tests for TokenPak Auth Guard (Phase 1)

Tests:
1. Counter increments on 401/403
2. Counter resets on successful response
3. Event fires at threshold (3 by default)
4. Cooldown prevents spam (no double-alert)
5. Incident is logged to file
6. Auth alert message formatting
"""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Override incident log path before importing module
_tmp = tempfile.mkdtemp()
os.environ["TOKENPAK_AUTH_FAILURE_THRESHOLD"] = "3"
os.environ["TOKENPAK_AUTH_ALERT_COOLDOWN"] = "1"  # 1 second for fast tests
os.environ["TOKENPAK_INCIDENT_LOG"] = os.path.join(_tmp, "incidents.log")

# Re-import to pick up env overrides
import importlib

import tokenpak.security.auth_guard as auth_guard_module

importlib.reload(auth_guard_module)
from tokenpak.security.auth_guard import AuthGuard


class TestAuthGuardCounters(unittest.TestCase):
    def _make_guard(self):
        """Create a fresh AuthGuard with a temp incident log."""
        guard = AuthGuard()
        guard._log_dir = Path(_tmp)
        return guard

    def test_counter_increments_on_401(self):
        guard = self._make_guard()
        guard.record_response("anthropic", 401)
        self.assertEqual(guard.get_counters().get("anthropic", 0), 1)

    def test_counter_increments_on_403(self):
        guard = self._make_guard()
        guard.record_response("anthropic", 403)
        self.assertEqual(guard.get_counters().get("anthropic", 0), 1)

    def test_counter_resets_on_success(self):
        guard = self._make_guard()
        guard.record_response("anthropic", 401)
        guard.record_response("anthropic", 401)
        guard.record_response("anthropic", 200)
        self.assertEqual(guard.get_counters().get("anthropic", 0), 0)

    def test_counter_resets_on_other_success(self):
        guard = self._make_guard()
        guard.record_response("openai", 403)
        guard.record_response("openai", 403)
        guard.record_response("openai", 201)
        self.assertEqual(guard.get_counters().get("openai", 0), 0)

    def test_independent_providers(self):
        guard = self._make_guard()
        guard.record_response("anthropic", 401)
        guard.record_response("openai", 200)
        self.assertEqual(guard.get_counters().get("anthropic", 0), 1)
        self.assertEqual(guard.get_counters().get("openai", 0), 0)


class TestAuthGuardEvents(unittest.TestCase):
    def _make_guard(self):
        return AuthGuard()

    def test_event_fires_at_threshold(self):
        guard = self._make_guard()
        events = []
        guard.on_auth_failure(lambda p, e, d: events.append((p, e, d)))

        for _ in range(3):
            guard.record_response("anthropic", 401)

        # Give background thread time to fire
        time.sleep(0.2)
        self.assertEqual(len(events), 1)
        provider, event, details = events[0]
        self.assertEqual(provider, "anthropic")
        self.assertEqual(event, "auth-failure-detected")
        self.assertEqual(details["consecutive_failures"], 3)

    def test_event_does_not_fire_below_threshold(self):
        guard = self._make_guard()
        events = []
        guard.on_auth_failure(lambda p, e, d: events.append((p, e, d)))

        guard.record_response("anthropic", 401)
        guard.record_response("anthropic", 401)
        time.sleep(0.2)
        self.assertEqual(len(events), 0)

    def test_cooldown_prevents_duplicate_alert(self):
        guard = self._make_guard()
        # Override cooldown to 10 seconds for this test
        import tokenpak.security.auth_guard as m

        orig = m.AUTH_ALERT_COOLDOWN_SEC
        m.AUTH_ALERT_COOLDOWN_SEC = 10
        try:
            events = []
            guard.on_auth_failure(lambda p, e, d: events.append((p, e, d)))

            # 3 failures → alert
            for _ in range(3):
                guard.record_response("anthropic", 401)
            time.sleep(0.2)
            self.assertEqual(len(events), 1)

            # 3 more failures → NO new alert (cooldown)
            for _ in range(3):
                guard.record_response("anthropic", 401)
            time.sleep(0.2)
            self.assertEqual(len(events), 1, "Should not alert again within cooldown")
        finally:
            m.AUTH_ALERT_COOLDOWN_SEC = orig

    def test_alert_after_cooldown_expires(self):
        """After cooldown, a new burst of failures should trigger a second alert."""
        guard = self._make_guard()
        # Use 1-second cooldown (set in env before reload)
        events = []
        guard.on_auth_failure(lambda p, e, d: events.append((p, e, d)))

        for _ in range(3):
            guard.record_response("anthropic", 401)
        time.sleep(0.2)
        self.assertEqual(len(events), 1)

        # Wait out the 1-second cooldown
        time.sleep(1.2)
        guard._counters["anthropic"] = 0  # Simulate a "reset" then new burst
        for _ in range(3):
            guard.record_response("anthropic", 401)
        time.sleep(0.2)
        self.assertEqual(len(events), 2, "Should alert again after cooldown")


class TestAuthGuardIncidentLog(unittest.TestCase):
    def test_incident_logged_to_file(self):
        log_path = Path(_tmp) / "incidents_test.log"
        os.environ["TOKENPAK_INCIDENT_LOG"] = str(log_path)
        importlib.reload(auth_guard_module)
        from tokenpak.security.auth_guard import AuthGuard as FreshGuard

        guard = FreshGuard()
        fired = threading.Event()
        guard.on_auth_failure(lambda p, e, d: fired.set())

        for _ in range(3):
            guard.record_response("anthropic", 401)
        fired.wait(timeout=2)
        time.sleep(0.1)  # let _log_incident finish

        self.assertTrue(log_path.exists(), "Incident log should be created")
        with open(log_path) as f:
            lines = [l.strip() for l in f if l.strip()]
        self.assertGreater(len(lines), 0)
        data = json.loads(lines[-1])
        self.assertEqual(data["provider"], "anthropic")
        self.assertIn("timestamp", data)


class TestAuthAlertMessage(unittest.TestCase):
    def test_message_contains_expected_text(self):
        from tokenpak.security.auth_alert import _build_alert_message

        msg = _build_alert_message(
            "anthropic",
            {
                "consecutive_failures": 3,
                "threshold": 3,
                "timestamp": "2026-03-19T22:00:00Z",
            },
        )
        self.assertIn("Auth Failure", msg)
        self.assertIn("expired", msg)
        self.assertIn("OFFLINE", msg)
        self.assertIn("update-anthropic-token.sh", msg)


if __name__ == "__main__":
    unittest.main()
