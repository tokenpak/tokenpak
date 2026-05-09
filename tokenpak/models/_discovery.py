# SPDX-License-Identifier: Apache-2.0
"""Background model discovery — polls provider APIs to learn new model IDs.

Discovery is opt-in: set ``TOKENPAK_MODEL_DISCOVERY=1`` or call
``start_discovery()`` from proxy startup.

Discovered models get family-inferred properties (provider APIs don't return
pricing). Results are cached to ``~/.tokenpak/data/discovered_models.json``
for offline resilience.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from ._registry import get_registry

log = logging.getLogger(__name__)

REFRESH_INTERVAL = int(os.environ.get("TOKENPAK_DISCOVERY_INTERVAL", "3600"))
CACHE_PATH = Path.home() / ".tokenpak" / "data" / "discovered_models.json"

# Provider endpoints that return model lists
_PROVIDER_ENDPOINTS: dict[str, dict[str, str]] = {
    "anthropic": {
        "url": "https://api.anthropic.com/v1/models",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_header": "x-api-key",
        "version_header": "anthropic-version",
        "version_value": "2023-06-01",
    },
    "openai": {
        "url": "https://api.openai.com/v1/models",
        "api_key_env": "OPENAI_API_KEY",
        "auth_prefix": "Bearer ",
    },
}


class ModelDiscovery:
    """Background thread that polls provider APIs for new model IDs."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._discovered: dict[str, str] = {}  # model_id -> provider

    def start(self) -> None:
        """Start the discovery background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._load_cache()
        self._thread = threading.Thread(
            target=self._run, name="tokenpak-model-discovery", daemon=True
        )
        self._thread.start()
        log.info("Model discovery started (interval=%ds)", REFRESH_INTERVAL)

    def stop(self) -> None:
        """Stop the discovery thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        """Main loop: discover → sleep → repeat."""
        # Run immediately on start, then at interval
        self._discover_all()
        while not self._stop.wait(REFRESH_INTERVAL):
            self._discover_all()

    def _discover_all(self) -> None:
        """Poll all configured provider endpoints."""
        for provider, config in _PROVIDER_ENDPOINTS.items():
            try:
                self._discover_provider(provider, config)
            except Exception:
                log.debug("Discovery failed for %s", provider, exc_info=True)

    def _discover_provider(self, provider: str, config: dict[str, str]) -> None:
        """Poll a single provider's model list endpoint."""
        import urllib.error
        import urllib.request

        api_key = os.environ.get(config.get("api_key_env", ""), "")
        if not api_key:
            return

        url = config["url"]
        headers: dict[str, str] = {}

        if "api_key_header" in config:
            headers[config["api_key_header"]] = api_key
        elif "auth_prefix" in config:
            headers["Authorization"] = config["auth_prefix"] + api_key

        if "version_header" in config:
            headers[config["version_header"]] = config["version_value"]

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            log.debug("Failed to fetch models from %s", url, exc_info=True)
            return

        # Parse model IDs from response
        model_ids = self._extract_model_ids(data, provider)
        registry = get_registry()
        new_count = 0

        for model_id in model_ids:
            if model_id in self._discovered:
                continue
            # Check if it's already in the seed catalog
            existing = registry.resolve(model_id)
            if existing.source == "seed":
                continue

            # New model — register with inferred properties
            self._discovered[model_id] = provider
            registry.register(existing)  # resolve() already built a ModelInfo
            new_count += 1

        if new_count > 0:
            log.info("Discovered %d new %s models", new_count, provider)
            self._save_cache()

    def _extract_model_ids(self, data: Any, provider: str) -> list[str]:
        """Extract model IDs from a provider API response."""
        models: list[str] = []
        if isinstance(data, dict):
            items = data.get("data", [])
        elif isinstance(data, list):
            items = data
        else:
            return models

        for item in items:
            if isinstance(item, dict):
                model_id = item.get("id", "") or item.get("model", "")
                if model_id:
                    models.append(model_id)
            elif isinstance(item, str):
                models.append(item)
        return models

    def _load_cache(self) -> None:
        """Load previously discovered models from disk cache."""
        if not CACHE_PATH.exists():
            return
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            self._discovered = raw.get("models", {})
            registry = get_registry()
            for model_id, provider in self._discovered.items():
                info = registry.resolve(model_id)
                if info.source != "seed":
                    registry.register(info)
            log.debug("Loaded %d cached discovered models", len(self._discovered))
        except (json.JSONDecodeError, OSError):
            log.debug("Failed to load discovery cache", exc_info=True)

    def _save_cache(self) -> None:
        """Persist discovered models to disk."""
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "models": self._discovered,
            }
            CACHE_PATH.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except OSError:
            log.debug("Failed to save discovery cache", exc_info=True)


# Module-level singleton
_discovery: ModelDiscovery | None = None


def start_discovery() -> None:
    """Start model discovery if enabled by env var or explicit call."""
    global _discovery
    if _discovery is None:
        _discovery = ModelDiscovery()
    _discovery.start()


def stop_discovery() -> None:
    """Stop model discovery."""
    if _discovery is not None:
        _discovery.stop()


def auto_start_if_enabled() -> None:
    """Auto-start discovery unless explicitly disabled.

    Default: enabled when an API key is present for at least one provider.
    Set TOKENPAK_MODEL_DISCOVERY=0 to force-disable.
    """
    flag = os.environ.get("TOKENPAK_MODEL_DISCOVERY", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return
    if flag in ("1", "true", "yes", "on"):
        start_discovery()
        return
    # Auto-mode: enable if any provider has credentials available
    for cfg in _PROVIDER_ENDPOINTS.values():
        if os.environ.get(cfg.get("api_key_env", ""), ""):
            start_discovery()
            return
