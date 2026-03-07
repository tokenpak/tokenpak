"""Provider adapter sub-package for TokenPak telemetry.

Each adapter knows how to:
  1. Detect whether a raw payload came from its provider (``detect``).
  2. Normalise a raw request into ``CanonicalRequest`` (``to_canonical_request``).
  3. Normalise a raw response into ``CanonicalResponse`` (``to_canonical_response``).
  4. Extract token-usage into ``CanonicalUsage`` (``extract_usage``).

Exported symbols
----------------
BaseAdapter      — abstract base class every adapter must implement
AnthropicAdapter — Anthropic Messages API
OpenAIAdapter    — OpenAI Chat Completions + Responses API
GeminiAdapter    — Google Gemini API
AdapterRegistry  — auto-detection registry
"""

from __future__ import annotations

from tokenpak.telemetry.adapters.anthropic import AnthropicAdapter
from tokenpak.telemetry.adapters.base import BaseAdapter
from tokenpak.telemetry.adapters.gemini import GeminiAdapter
from tokenpak.telemetry.adapters.openai import OpenAIAdapter
from tokenpak.telemetry.adapters.registry import AdapterRegistry, UnknownAdapter

__all__ = [
    "BaseAdapter",
    "AnthropicAdapter",
    "OpenAIAdapter",
    "GeminiAdapter",
    "AdapterRegistry",
    "UnknownAdapter",
]
