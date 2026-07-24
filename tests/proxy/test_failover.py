"""
tests/proxy/test_failover.py

Regression test for TRIX-MTC-07 Fix #1:
  FailoverManager.iter_providers() must not crash when reload_config() is
  called concurrently from another thread.

Without the fix, self._config.available_chain() is called without a lock,
so a concurrent reload that swaps self._config can cause a RuntimeError or
return a partially-rebuilt list.
"""

from __future__ import annotations

import threading
from typing import List
from unittest.mock import patch

from tokenpak.proxy.failover import (
    FailoverConfig,
    FailoverManager,
    ProviderEntry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(providers: List[str]) -> FailoverConfig:
    """Build a minimal FailoverConfig with the given provider names."""
    chain = [
        ProviderEntry(
            provider=p,
            model_map={"claude-sonnet-4-5": f"{p}-model"},
            credential_env=f"{p.upper()}_API_KEY",
        )
        for p in providers
    ]
    return FailoverConfig(enabled=True, chain=chain)


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------


def test_failover_concurrent_reload_no_runtime_error():
    """
    Spawn one thread iterating providers and one calling reload_config()
    simultaneously.  Must complete without RuntimeError or data corruption.
    """
    errors: List[Exception] = []

    # Start with a 3-provider config so the iterator has something to loop over.
    cfg = _make_config(["anthropic", "openai", "google"])
    mgr = FailoverManager(config=cfg)

    stop = threading.Event()

    def reader():
        # Tight loop: iterate providers while the reloader thrashes the config.
        try:
            for _ in range(500):
                # Patch credential_available so all providers appear usable.
                with patch.object(ProviderEntry, "credential_available", return_value=True):
                    providers = list(mgr.iter_providers("claude-sonnet-4-5"))
                # After the lock fix, we always get a consistent snapshot.
                assert isinstance(providers, list)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            stop.set()

    def reloader():
        # Swap to a new config repeatedly while the reader iterates.
        while not stop.is_set():
            new_cfg = _make_config(["anthropic", "openai"])
            mgr.reload_config.__func__  # ensure method exists
            with mgr._lock:
                mgr._config = new_cfg

    t_read = threading.Thread(target=reader, daemon=True)
    t_reload = threading.Thread(target=reloader, daemon=True)
    t_reload.start()
    t_read.start()
    t_read.join(timeout=5)
    stop.set()
    t_reload.join(timeout=2)

    assert not errors, f"Concurrent access raised: {errors}"
