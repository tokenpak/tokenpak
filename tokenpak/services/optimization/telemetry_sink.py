# SPDX-License-Identifier: Apache-2.0
"""Telemetry sink for the optimization pipeline.

``TelemetrySink.persist()`` is the single call site for persisting:
- Per-source savings attribution records (tp_savings_attribution)
- Cache miss reason records (tp_cache_miss_reasons)

The sink is additive — it never modifies request or response bytes and
never raises. Errors are logged at DEBUG level to avoid breaking the
proxy hot path.

Feature flag: ``TOKENPAK_ATTRIBUTION_V2`` gates savings/miss persistence.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .attribution_stage import get_attributions, is_attribution_v2_enabled
from .context import OptimizationContext

_log = logging.getLogger(__name__)


def _default_db_path():
    """Resolve telemetry.db via the single resolver (honors env overrides)."""
    from tokenpak.core.paths import get_db_path

    return get_db_path("telemetry.db")


class TelemetrySink:
    """Persists optimization pipeline telemetry after a request completes.

    Instantiate once at process startup; call ``persist()`` at the end of
    every request that passed through the optimization pipeline.

    Parameters
    ----------
    db_path:
        Path to the TelemetryDB SQLite file.  Defaults to the location
        resolved by ``tokenpak.core.paths.get_db_path("telemetry.db")``.
    env:
        Optional env dict override for feature flag checks (used in tests).
    """

    def __init__(
        self,
        db_path: Optional[Any] = None,
        env: Optional[dict] = None,
    ) -> None:
        self._db_path = db_path or _default_db_path()
        self._env = env
        self._db: Optional[Any] = None

    def _get_db(self) -> Any:
        """Lazily open TelemetryDB (deferred import to avoid circular imports)."""
        if self._db is None:
            from tokenpak.telemetry.storage import TelemetryDB
            self._db = TelemetryDB(self._db_path)
        return self._db

    def persist(
        self,
        ctx: OptimizationContext,
        response_body: Optional[bytes] = None,
        *,
        platform: Optional[str] = None,
        model: str = "",
    ) -> None:
        """Persist savings attributions and cache miss reasons for *ctx*.

        Safe to call even when feature flags are off — the method checks
        the flags internally and is a no-op when disabled.
        """
        if not is_attribution_v2_enabled(self._env):
            return

        try:
            self._persist_attributions(ctx, platform=platform, model=model)
        except Exception as exc:
            _log.debug(
                "[TelemetrySink] attribution persist error for %s: %s",
                ctx.request_id, exc,
            )

        try:
            self._persist_cache_miss(ctx, platform=platform, model=model)
        except Exception as exc:
            _log.debug(
                "[TelemetrySink] cache_miss persist error for %s: %s",
                ctx.request_id, exc,
            )

    def _persist_attributions(
        self,
        ctx: OptimizationContext,
        platform: Optional[str] = None,
        model: str = "",
    ) -> None:
        from tokenpak.telemetry.savings import attribution_to_row

        attributions = get_attributions(ctx)
        if not attributions:
            return

        db = self._get_db()
        rows = [
            attribution_to_row(
                ctx.request_id,
                attr,
                platform=platform or ctx.platform,
                model=model,
            )
            for attr in attributions
        ]
        db.batch_insert_savings_attributions(rows)
        _log.debug(
            "[TelemetrySink] persisted %d attribution records for %s",
            len(rows), ctx.request_id,
        )

    def _persist_cache_miss(
        self,
        ctx: OptimizationContext,
        platform: Optional[str] = None,
        model: str = "",
    ) -> None:
        from tokenpak.telemetry.cache_miss import cache_stage_trace_to_miss_record

        # Look for a CacheStageTrace on ctx
        cache_result = getattr(ctx, "_tip04_cache_result", None)
        if cache_result is None:
            return

        record = cache_stage_trace_to_miss_record(
            ctx.request_id,
            cache_result,
            platform=platform or ctx.platform or "",
            model=model,
        )
        if record is None:
            return

        db = self._get_db()
        db.insert_cache_miss(record.to_row())
        _log.debug(
            "[TelemetrySink] persisted cache miss reason=%s for %s",
            record.reason, ctx.request_id,
        )


__all__ = ["TelemetrySink"]
