"""
TokenPak Provider Detector (F.1)

Auto-detects which LLM provider a request targets, based on:
  1. URL path patterns
  2. Request headers
  3. Request body structure (model name / field names)

Returns one of: "anthropic" | "openai" | "google" | "ollama" | "unknown"
"""

from __future__ import annotations

import json
from typing import Dict, Optional, Union

# ---------------------------------------------------------------------------
# Known path patterns → provider
# ---------------------------------------------------------------------------

_PATH_PATTERNS: list[tuple[str, str]] = [
    # Anthropic
    ("/v1/messages", "anthropic"),
    ("/v1/complete", "anthropic"),
    # OpenAI / compatible
    ("/v1/chat/completions", "openai"),
    ("/v1/completions", "openai"),
    ("/v1/embeddings", "openai"),
    # Google
    ("/v1beta/models/", "google"),
    ("/v1/models/", "google"),
    ("generateContent", "google"),
    ("streamGenerateContent", "google"),
    # Ollama
    ("/api/generate", "ollama"),
    ("/api/chat", "ollama"),
    ("/api/embeddings", "ollama"),
]

# URL hostnames → provider
_HOST_MAP: dict[str, str] = {
    "api.anthropic.com": "anthropic",
    "api.openai.com": "openai",
    "generativelanguage.googleapis.com": "google",
    "aiplatform.googleapis.com": "google",
    "localhost": "ollama",
    "127.0.0.1": "ollama",
    "ollama": "ollama",
}

# Model-name prefix → provider
_MODEL_PREFIX_MAP: list[tuple[str, str]] = [
    ("claude-", "anthropic"),
    ("claude", "anthropic"),
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("text-davinci", "openai"),
    ("gemini-", "google"),
    ("gemini", "google"),
    ("palm", "google"),
    ("llama", "ollama"),
    ("mistral", "ollama"),
    ("mixtral", "ollama"),
    ("qwen", "ollama"),
    ("deepseek", "ollama"),
    ("phi-", "ollama"),
    ("phi", "ollama"),
]


def detect_provider(
    request: Union[dict, None] = None,
    *,
    path: str = "",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    host: str = "",
) -> str:
    """
    Detect which LLM provider a request targets.

    Can be called with:
        detect_provider({"path": ..., "headers": ..., "body": ...})
    or with keyword args:
        detect_provider(path=..., headers=..., body=...)

    Priority:
      1. Host name (if provided)
      2. URL path patterns
      3. Request headers (x-api-key, anthropic-version, Authorization Bearer)
      4. Request body: model name, field names (messages vs contents)
      5. "unknown" as fallback

    Returns:
        "anthropic" | "openai" | "google" | "ollama" | "unknown"
    """
    # Normalise input — accept a dict or keyword args
    if request is not None:
        path = path or request.get("path", "") or request.get("url", "")
        headers = headers if headers is not None else request.get("headers", {})
        body = body if body is not None else request.get("body")
        host = host or request.get("host", "")

    headers = {k.lower(): v for k, v in (headers or {}).items()}
    path = path or ""
    host = host or ""

    # ── 1. Host-based detection ──────────────────────────────────────────
    if host:
        host_lower = host.lower().split(":")[0]  # strip port
        for h, provider in _HOST_MAP.items():
            if h in host_lower:
                return provider

    # Also scan path for hostname clues (full URL passed as path)
    if path.startswith("http"):
        from urllib.parse import urlparse

        parsed = urlparse(path)
        netloc_lower = parsed.netloc.lower().split(":")[0]
        for h, provider in _HOST_MAP.items():
            if h in netloc_lower:
                return provider
        # Re-point path to just the URL path portion
        path = parsed.path

    # ── 2. Path-based detection ──────────────────────────────────────────
    path_lower = path.lower()
    for pattern, provider in _PATH_PATTERNS:
        if pattern.lower() in path_lower:
            return provider

    # ── 3. Header-based detection ────────────────────────────────────────
    if "anthropic-version" in headers:
        return "anthropic"
    if "x-api-key" in headers:
        # Anthropic uses x-api-key exclusively; OpenAI uses Authorization: Bearer
        return "anthropic"

    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token.startswith("sk-ant-"):
            return "anthropic"
        if token.startswith("sk-"):
            return "openai"
        if token.startswith("AIza"):
            return "google"

    # ── 4. Body-based detection ──────────────────────────────────────────
    if body:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            data = {}

        if isinstance(data, dict):
            # Model name
            model = str(data.get("model", "")).lower()
            if model:
                for prefix, provider in _MODEL_PREFIX_MAP:
                    if model.startswith(prefix) or model == prefix.rstrip("-"):
                        return provider

            # Field-name fingerprinting
            if "systemInstruction" in data or "contents" in data:
                return "google"
            if "system" in data and "messages" in data:
                return "anthropic"
            if "messages" in data:
                # Could be OpenAI or Anthropic — check message roles
                msgs = data.get("messages", [])
                if any(m.get("role") == "system" for m in msgs if isinstance(m, dict)):
                    return "openai"
                return "anthropic"
            if "prompt" in data and "model" not in data:
                # Legacy completions
                return "openai"
            if "prompt" in data:
                return "ollama"

    return "unknown"
