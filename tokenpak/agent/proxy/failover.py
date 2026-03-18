"""
TokenPak Multi-Provider Failover (F.3)

Reads and validates the failover configuration block from
~/.tokenpak/config.yaml, and provides a FailoverManager that orchestrates
provider switching when primary providers fail.

Config file location: ~/.tokenpak/config.yaml

Example config:

    failover:
      enabled: true
      chain:
        - provider: anthropic
          model_map:
            claude-opus-4-5: claude-opus-4-5
            claude-sonnet-4-5: claude-sonnet-4-5
          credential_env: ANTHROPIC_API_KEY
        - provider: openai
          model_map:
            claude-opus-4-5: gpt-4o
            claude-sonnet-4-5: gpt-4o-mini
          credential_env: OPENAI_API_KEY
        - provider: google
          model_map:
            claude-opus-4-5: gemini-1.5-pro
            claude-sonnet-4-5: gemini-1.5-pro
          credential_env: GOOGLE_API_KEY

Credential passthrough:
  - credential_env must name an ENVIRONMENT VARIABLE, never a raw key value.
  - If the env var is not set, that provider is skipped in the chain.
  - Credentials are NEVER stored in config files or logs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(os.path.expanduser("~/.tokenpak/config.yaml"))

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


@dataclass
class ProviderEntry:
    """Single provider entry in the failover chain."""

    provider: str  # "anthropic" | "openai" | "google" | "ollama"
    model_map: Dict[str, str]  # original-model → replacement-model
    credential_env: str  # env var that holds the API key (never the key itself)

    def credential_available(self) -> bool:
        """True if the required env var is set and non-empty."""
        val = os.environ.get(self.credential_env, "")
        return bool(val.strip())

    def get_credential(self) -> Optional[str]:
        """
        Return the credential value from the environment.
        NEVER log, store, or cache this value.
        """
        return os.environ.get(self.credential_env) or None


@dataclass
class FailoverConfig:
    """Parsed failover configuration block."""

    enabled: bool
    chain: List[ProviderEntry] = field(default_factory=list)

    def available_chain(self) -> List[ProviderEntry]:
        """Return only providers whose credentials are present in the environment."""
        return [p for p in self.chain if p.credential_available()]


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_failover_config(path: Optional[Path] = None) -> FailoverConfig:
    """
    Load failover config from YAML file.

    Falls back gracefully if:
    - File does not exist (returns disabled config)
    - PyYAML not installed (returns disabled config with warning)
    - File is malformed (returns disabled config with warning)

    Args:
        path: Override config file path (defaults to ~/.tokenpak/config.yaml)

    Returns:
        FailoverConfig (may be disabled if file missing or invalid)
    """
    cfg_path = path or _CONFIG_PATH

    if not cfg_path.exists():
        return FailoverConfig(enabled=False)

    try:
        import yaml  # type: ignore[import]
    except ImportError:
        logger.warning(
            "PyYAML not installed — failover config unavailable. "
            "Install with: pip install pyyaml"
        )
        return FailoverConfig(enabled=False)

    try:
        with cfg_path.open() as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Failed to parse %s: %s — failover disabled", cfg_path, exc)
        return FailoverConfig(enabled=False)

    failover_raw = raw.get("failover", {})
    if not isinstance(failover_raw, dict):
        return FailoverConfig(enabled=False)

    enabled = bool(failover_raw.get("enabled", False))
    chain: List[ProviderEntry] = []

    for entry in failover_raw.get("chain", []):
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider", "")).lower()
        if not provider:
            continue
        model_map = entry.get("model_map") or {}
        credential_env = str(entry.get("credential_env", ""))
        chain.append(
            ProviderEntry(
                provider=provider,
                model_map=model_map,
                credential_env=credential_env,
            )
        )

    return FailoverConfig(enabled=enabled, chain=chain)


# ---------------------------------------------------------------------------
# Failover Manager
# ---------------------------------------------------------------------------


@dataclass
class FailoverResult:
    """Result of a single failover attempt."""

    provider: str
    model: str
    credential_env: str
    skipped_providers: List[str] = field(default_factory=list)


class FailoverManager:
    """
    Orchestrates provider failover.

    Usage::

        mgr = FailoverManager()
        for attempt in mgr.iter_providers("claude-sonnet-4-5", preferred="anthropic"):
            try:
                result = call_provider(attempt.provider, attempt.model, ...)
                break
            except ProviderError:
                continue
    """

    def __init__(self, config: Optional[FailoverConfig] = None):
        self._config = config or load_failover_config()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def map_model(self, original_model: str, provider: str) -> str:
        """
        Map an original model name to the equivalent for *provider*.

        Returns the original name unchanged if no mapping exists.
        """
        for entry in self._config.chain:
            if entry.provider == provider:
                return entry.model_map.get(original_model, original_model)
        return original_model

    def iter_providers(
        self,
        model: str,
        preferred: Optional[str] = None,
    ) -> Iterator[FailoverResult]:
        """
        Yield FailoverResult objects in failover priority order.

        Providers without credentials are skipped silently.
        If *preferred* is set, that provider is tried first (if available).

        Args:
            model: Original model name being requested
            preferred: Provider to try first

        Yields:
            FailoverResult for each available provider in chain order
        """
        if not self._config.enabled:
            return

        available = self._config.available_chain()
        if not available:
            return

        # Re-order so preferred comes first
        if preferred:
            available = sorted(available, key=lambda e: (e.provider != preferred))

        skipped: List[str] = []
        for entry in available:
            mapped_model = entry.model_map.get(model, model)
            yield FailoverResult(
                provider=entry.provider,
                model=mapped_model,
                credential_env=entry.credential_env,
                skipped_providers=list(skipped),
            )
            skipped.append(entry.provider)

    def get_provider_for(
        self, model: str, preferred: Optional[str] = None
    ) -> Optional[FailoverResult]:
        """
        Return the first available provider for the given model.
        Shortcut for when you just want the primary option.
        """
        for result in self.iter_providers(model, preferred=preferred):
            return result
        return None


# ---------------------------------------------------------------------------
# Default config YAML template (written on first run / reset)
# ---------------------------------------------------------------------------

DEFAULT_YAML_TEMPLATE = """\
# TokenPak config — ~/.tokenpak/config.yaml
# Edit this file to configure failover and other settings.

failover:
  enabled: false
  # Provider chain — tried in order when primary fails.
  # credential_env must be an ENVIRONMENT VARIABLE NAME (never a raw key).
  chain:
    - provider: anthropic
      model_map: {}
      credential_env: ANTHROPIC_API_KEY
    - provider: openai
      model_map:
        claude-opus-4-5: gpt-4o
        claude-sonnet-4-5: gpt-4o-mini
        claude-haiku-4-5: gpt-4o-mini
      credential_env: OPENAI_API_KEY
    - provider: google
      model_map:
        claude-opus-4-5: gemini-1.5-pro
        claude-sonnet-4-5: gemini-1.5-pro
        claude-haiku-4-5: gemini-pro
      credential_env: GOOGLE_API_KEY
"""


def write_default_config(path: Optional[Path] = None, overwrite: bool = False) -> Path:
    """
    Write the default config.yaml template to disk.

    Args:
        path: Target path (defaults to ~/.tokenpak/config.yaml)
        overwrite: If False (default), do nothing if file already exists.

    Returns:
        Path the file was written to.
    """
    cfg_path = path or _CONFIG_PATH
    if cfg_path.exists() and not overwrite:
        return cfg_path
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(DEFAULT_YAML_TEMPLATE)
    return cfg_path
