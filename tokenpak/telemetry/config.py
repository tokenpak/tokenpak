"""
TokenPak Telemetry Configuration

Pydantic-based config validation with sensible defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ServerConfig(BaseModel):
    """Server configuration."""

    host: str = "0.0.0.0"
    port: int = 17888
    cors_origins: List[str] = ["*"]


class StorageConfig(BaseModel):
    """Storage configuration."""

    type: str = "sqlite"
    path: str = "~/.tokenpak/telemetry.db"


class RetentionConfig(BaseModel):
    """Data retention policy."""

    events_days: int = 90
    rollups_days: int = 365
    auto_prune: bool = True


class CaptureConfig(BaseModel):
    """Capture settings."""

    store_prompts: bool = False
    store_payloads: bool = False
    debug_mode: bool = False
    sampling_rate: float = 1.0

    @field_validator("sampling_rate")
    def validate_sampling_rate(cls, v):
        if not 0 < v <= 1.0:
            raise ValueError("sampling_rate must be between 0 and 1")
        return v


class PricingConfig(BaseModel):
    """Pricing configuration."""

    source: str = "embedded"
    catalog_path: Optional[str] = None
    catalog_url: Optional[str] = None


class AdaptersConfig(BaseModel):
    """Provider adapters."""

    providers: List[str] = ["anthropic", "openai", "google"]


class TelemetryConfig(BaseModel):
    """Top-level configuration."""

    version: str = "1.0"
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    adapters: AdaptersConfig = Field(default_factory=AdaptersConfig)

    @field_validator("version")
    def validate_version(cls, v):
        if v != "1.0":
            raise ValueError("Only version 1.0 is supported")
        return v


def load_config(config_path: str | Path) -> TelemetryConfig:
    """Load and parse config file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        config_dict = json.load(f)

    return TelemetryConfig(**config_dict)


def validate_config(config_dict: dict) -> None:
    """Validate config dict."""
    try:
        TelemetryConfig(**config_dict)
    except Exception as e:
        raise ValueError(f"Config validation failed: {e}")


def get_default_config() -> TelemetryConfig:
    """Get default configuration."""
    return TelemetryConfig()
