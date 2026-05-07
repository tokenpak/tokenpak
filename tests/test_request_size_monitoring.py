"""
Tests for TokenPak request size monitoring module.

Tests cover:
- Threshold detection (yellow/orange/red)
- First-breach-only logic per session
- Thread safety
- Configuration
- History tracking
- Singleton pattern
"""


import pytest

pytest.importorskip("tokenpak.monitoring.request_size", reason="module not available in current build")
from datetime import datetime

import pytest

from tokenpak.monitoring.request_size import (
    AlertLevel,
    RequestSizeConfig,
    RequestSizeMonitor,
    get_monitor,
    reset_monitor,
)


class TestRequestSizeConfig:
    """Test configuration object."""

    def test_default_config(self):
        """Test default thresholds."""
        config = RequestSizeConfig()
        assert config.enabled is True
        assert config.yellow_threshold == 300_000
        assert config.orange_threshold == 500_000
        assert config.red_threshold == 700_000

    def test_custom_config(self):
        """Test custom thresholds."""
        config = RequestSizeConfig(
            yellow_threshold=100_000,
            orange_threshold=200_000,
            red_threshold=300_000,
        )
        assert config.yellow_threshold == 100_000
        assert config.orange_threshold == 200_000
        assert config.red_threshold == 300_000

    def test_config_disabled(self):
        """Test disabled monitoring."""
        config = RequestSizeConfig(enabled=False)
        assert config.enabled is False


class TestRequestSizeMonitor:
    """Test request size monitor."""

    def test_no_alert_below_yellow(self):
        """No alert when below yellow threshold."""
        monitor = RequestSizeMonitor()
        alert = monitor.check_request_size(100_000)
        assert alert is None

    def test_yellow_alert(self):
        """Yellow alert at 300KB threshold."""
        monitor = RequestSizeMonitor()
        alert = monitor.check_request_size(300_000)

        assert alert is not None
        assert alert.level == AlertLevel.YELLOW
        assert alert.size_bytes == 300_000
        assert "growing large" in alert.message.lower()

    def test_orange_alert(self):
        """Orange alert at 500KB threshold."""
        monitor = RequestSizeMonitor()
        alert = monitor.check_request_size(500_000)

        assert alert is not None
        assert alert.level == AlertLevel.ORANGE
        assert alert.size_bytes == 500_000
        assert "/compact" in alert.message.lower()

    def test_red_alert(self):
        """Red alert at 700KB threshold."""
        monitor = RequestSizeMonitor()
        alert = monitor.check_request_size(700_000)

        assert alert is not None
        assert alert.level == AlertLevel.RED
        assert alert.size_bytes == 700_000
        assert "now" in alert.message.lower()

    def test_first_breach_only_per_session(self):
        """Only alert once per level per session."""
        monitor = RequestSizeMonitor()
        session_id = "test-session-1"

        # First yellow alert
        alert1 = monitor.check_request_size(300_000, session_id=session_id)
        assert alert1 is not None
        assert alert1.level == AlertLevel.YELLOW

        # Second yellow alert (same level) — should not trigger
        alert2 = monitor.check_request_size(350_000, session_id=session_id)
        assert alert2 is None

        # Escalate to orange — should trigger
        alert3 = monitor.check_request_size(500_000, session_id=session_id)
        assert alert3 is not None
        assert alert3.level == AlertLevel.ORANGE

    def test_different_sessions_independent(self):
        """Different sessions track alerts independently."""
        monitor = RequestSizeMonitor()

        # Session 1 gets yellow
        alert1 = monitor.check_request_size(300_000, session_id="session-1")
        assert alert1 is not None
        assert alert1.level == AlertLevel.YELLOW

        # Session 2 gets yellow independently
        alert2 = monitor.check_request_size(300_000, session_id="session-2")
        assert alert2 is not None
        assert alert2.level == AlertLevel.YELLOW

    def test_no_session_id_tracking(self):
        """Requests without session_id are tracked under None key."""
        monitor = RequestSizeMonitor()

        alert1 = monitor.check_request_size(300_000)
        assert alert1 is not None

        alert2 = monitor.check_request_size(350_000)
        assert alert2 is None  # Already alerted at yellow for None session

    def test_reset_session_clears_state(self):
        """Reset session clears alert state."""
        monitor = RequestSizeMonitor()
        session_id = "test-session"

        # First alert
        alert1 = monitor.check_request_size(300_000, session_id=session_id)
        assert alert1 is not None

        # Second alert (same level) — blocked
        alert2 = monitor.check_request_size(350_000, session_id=session_id)
        assert alert2 is None

        # Reset session
        monitor.reset_session(session_id)

        # Alert again at same level — now allowed
        alert3 = monitor.check_request_size(350_000, session_id=session_id)
        assert alert3 is not None
        assert alert3.level == AlertLevel.YELLOW

    def test_disabled_monitoring(self):
        """Disabled monitoring returns no alerts."""
        config = RequestSizeConfig(enabled=False)
        monitor = RequestSizeMonitor(config)

        alert = monitor.check_request_size(700_000)
        assert alert is None

    def test_alert_history_tracking(self):
        """Alerts are tracked in history."""
        config = RequestSizeConfig(track_history=True, max_history_size=10)
        monitor = RequestSizeMonitor(config)

        # Generate alerts from different sessions
        monitor.check_request_size(300_000, session_id="s1")
        monitor.check_request_size(500_000, session_id="s2")
        monitor.check_request_size(700_000, session_id="s3")

        history = monitor.get_alert_history()
        assert len(history) == 3
        assert history[0]["level"] == "yellow"
        assert history[1]["level"] == "orange"
        assert history[2]["level"] == "red"

    def test_alert_history_limit(self):
        """History respects max size."""
        config = RequestSizeConfig(track_history=True, max_history_size=5)
        monitor = RequestSizeMonitor(config)

        # Generate 10 alerts
        for i in range(10):
            monitor.check_request_size(300_000 + i, session_id=f"s{i}")

        history = monitor.get_alert_history(limit=100)
        assert len(history) <= 5

    def test_stats_reporting(self):
        """Stats correctly report alert counts and configuration."""
        config = RequestSizeConfig()
        monitor = RequestSizeMonitor(config)

        monitor.check_request_size(300_000, session_id="s1")
        monitor.check_request_size(500_000, session_id="s2")

        stats = monitor.get_stats()
        assert stats["enabled"] is True
        assert stats["thresholds"]["yellow_bytes"] == 300_000
        assert stats["thresholds"]["orange_bytes"] == 500_000
        assert stats["thresholds"]["red_bytes"] == 700_000
        assert stats["alert_counts"]["yellow"] == 1
        assert stats["alert_counts"]["orange"] == 1
        assert stats["alert_counts"]["red"] == 0
        assert stats["active_sessions"] == 2

    def test_to_dict_serialization(self):
        """Monitor serializes to dict for telemetry."""
        monitor = RequestSizeMonitor()
        monitor.check_request_size(500_000)

        data = monitor.to_dict()
        assert data["type"] == "request_size_alert"
        assert "stats" in data
        assert "recent_alerts" in data
        assert isinstance(data["stats"], dict)
        assert isinstance(data["recent_alerts"], list)

    def test_size_calculations_in_alerts(self):
        """Alerts include size in both bytes and KB."""
        monitor = RequestSizeMonitor()
        alert = monitor.check_request_size(512_000)

        assert alert.size_bytes == 512_000
        assert "500.0" in alert.message or "512.5" in alert.message

    def test_alert_timestamp(self):
        """Alert includes proper timestamp."""
        monitor = RequestSizeMonitor()
        before = datetime.utcnow()
        alert = monitor.check_request_size(300_000)
        after = datetime.utcnow()

        assert alert.timestamp >= before
        assert alert.timestamp <= after

    def test_custom_thresholds(self):
        """Custom thresholds work correctly."""
        config = RequestSizeConfig(
            yellow_threshold=100_000,
            orange_threshold=200_000,
            red_threshold=300_000,
        )
        monitor = RequestSizeMonitor(config)

        # Yellow at custom threshold
        alert1 = monitor.check_request_size(100_000, session_id="s1")
        assert alert1.level == AlertLevel.YELLOW

        # Orange at custom threshold
        alert2 = monitor.check_request_size(200_000, session_id="s2")
        assert alert2.level == AlertLevel.ORANGE

        # Red at custom threshold
        alert3 = monitor.check_request_size(300_000, session_id="s3")
        assert alert3.level == AlertLevel.RED


class TestSingletonPattern:
    """Test get_monitor singleton."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_monitor()

    def test_singleton_creation(self):
        """get_monitor creates singleton on first call."""
        monitor1 = get_monitor()
        monitor2 = get_monitor()
        assert monitor1 is monitor2

    def test_singleton_with_config(self):
        """Singleton respects initial config."""
        config = RequestSizeConfig(yellow_threshold=100_000)
        monitor = get_monitor(config)

        stats = monitor.get_stats()
        assert stats["thresholds"]["yellow_bytes"] == 100_000

    def test_singleton_ignores_second_config(self):
        """Second get_monitor call ignores new config."""
        config1 = RequestSizeConfig(yellow_threshold=100_000)
        monitor1 = get_monitor(config1)

        config2 = RequestSizeConfig(yellow_threshold=200_000)
        monitor2 = get_monitor(config2)

        # Should still have first config
        stats = monitor2.get_stats()
        assert stats["thresholds"]["yellow_bytes"] == 100_000

    def test_reset_monitor(self):
        """Reset clears singleton."""
        monitor1 = get_monitor()
        reset_monitor()

        monitor2 = get_monitor()
        assert monitor1 is not monitor2


class TestThreadSafety:
    """Test concurrent access (basic)."""

    def test_concurrent_alerts(self):
        """Multiple alerts can be processed concurrently."""
        from concurrent.futures import ThreadPoolExecutor

        monitor = RequestSizeMonitor()
        results = []

        def check_size(session_id, size):
            alert = monitor.check_request_size(size, session_id=session_id)
            results.append(alert)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i in range(10):
                future = executor.submit(check_size, f"s{i % 3}", 300_000 + i)
                futures.append(future)

            for future in futures:
                future.result()

        # Should have some alerts (exact count depends on concurrency)
        assert len([r for r in results if r is not None]) >= 1
        assert len([r for r in results if r is None]) >= 1


class TestIntegration:
    """Integration tests."""

    def test_escalation_sequence(self):
        """Test full escalation from yellow → orange → red."""
        monitor = RequestSizeMonitor()
        session_id = "escalation-test"

        # Start below threshold
        alert0 = monitor.check_request_size(200_000, session_id=session_id)
        assert alert0 is None

        # Hit yellow
        alert1 = monitor.check_request_size(300_000, session_id=session_id)
        assert alert1 is not None
        assert alert1.level == AlertLevel.YELLOW

        # Escalate to orange
        alert2 = monitor.check_request_size(500_000, session_id=session_id)
        assert alert2 is not None
        assert alert2.level == AlertLevel.ORANGE

        # Escalate to red
        alert3 = monitor.check_request_size(700_000, session_id=session_id)
        assert alert3 is not None
        assert alert3.level == AlertLevel.RED

        # Stay at red
        alert4 = monitor.check_request_size(750_000, session_id=session_id)
        assert alert4 is None

    def test_full_workflow(self):
        """Test complete monitoring workflow."""
        # Create monitor with custom config
        config = RequestSizeConfig()
        monitor = RequestSizeMonitor(config)

        # Simulate requests from multiple sessions
        sessions = ["user-1", "user-2", "user-3"]
        sizes = [300_000, 500_000, 700_000]  # At each threshold

        alerts = []
        for session, size in zip(sessions, sizes):
            alert = monitor.check_request_size(size, session_id=session)
            if alert:
                alerts.append(alert)

        # Verify we got expected alerts
        assert len(alerts) == 3
        assert alerts[0].level == AlertLevel.YELLOW
        assert alerts[1].level == AlertLevel.ORANGE
        assert alerts[2].level == AlertLevel.RED

        # Check stats
        stats = monitor.get_stats()
        assert stats["active_sessions"] == 3
        assert stats["alert_counts"]["yellow"] == 1
        assert stats["alert_counts"]["orange"] == 1
        assert stats["alert_counts"]["red"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
