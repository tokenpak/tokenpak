"""tokenpak.proxy.stats_api — Proxy HTTP endpoints for stats."""

from __future__ import annotations

import json

from tokenpak.telemetry.stats import get_stats_storage


class StatsAPI:
    """Handles HTTP requests for stats endpoints."""

    @staticmethod
    def handle_stats_last() -> tuple[str, dict]:
        """Handle GET /stats/last request.

        Returns last request stats with session totals.
        """
        storage = get_stats_storage()
        data = storage.get_last_with_session()

        return json.dumps(data), {"Content-Type": "application/json"}

    @staticmethod
    def handle_stats_session() -> tuple[str, dict]:
        """Handle GET /stats/session request.

        Returns current session stats.
        """
        storage = get_stats_storage()
        session = storage.get_session()

        return json.dumps(session.to_dict()), {"Content-Type": "application/json"}

    @staticmethod
    def route(path: str) -> tuple[str, dict] | None:
        """Route HTTP requests to appropriate handler.

        Args:
            path: Request path (e.g. "/stats/last")

        Returns:
            (response_body, headers) tuple or None if not found
        """
        if path == "/stats/last":
            return StatsAPI.handle_stats_last()
        elif path == "/stats/session":
            return StatsAPI.handle_stats_session()
        return None
