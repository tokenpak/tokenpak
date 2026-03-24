"""Provider detection from API keys and request headers."""

import re
from typing import Optional, Tuple
from enum import Enum


class Provider(str, Enum):
    """Supported providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    BEDROCK = "bedrock"
    LITELLM = "litellm"


class ProviderDetector:
    """Detect provider from API key format and request headers."""

    # API key patterns for each provider
    KEY_PATTERNS = {
        Provider.ANTHROPIC: r'^sk-ant-[A-Za-z0-9_-]+$',
        Provider.OPENAI: r'^sk-[A-Za-z0-9_-]{20,}$',
        Provider.GOOGLE: r'^AIzaSy[A-Za-z0-9_-]{30,}$',
        Provider.BEDROCK: r'^[a-zA-Z0-9_-]+@bedrock$',
        Provider.LITELLM: r'^litellm-[A-Za-z0-9_-]+$',
    }

    # Model name patterns
    MODEL_PATTERNS = {
        Provider.ANTHROPIC: r'^claude-[a-z0-9-]+',
        Provider.OPENAI: r'^(gpt-|text-davinci|text-curie)',
        Provider.GOOGLE: r'^(gemini-|palm-)',
        Provider.BEDROCK: r'^(anthropic\.claude|amazon\.titan)',
        Provider.LITELLM: r'^[a-z0-9_/-]+/[a-z0-9_-]+',
    }

    def __init__(self):
        """Initialize detector with compiled patterns."""
        self.key_patterns = {
            k: re.compile(v) for k, v in self.KEY_PATTERNS.items()
        }
        self.model_patterns = {
            k: re.compile(v) for k, v in self.MODEL_PATTERNS.items()
        }

    def detect_from_key(self, api_key: str) -> Optional[Provider]:
        """
        Detect provider from API key format.

        Args:
            api_key: The API key to detect

        Returns:
            Provider enum or None if not detected
        """
        if not api_key or not isinstance(api_key, str):
            return None

        for provider, pattern in self.key_patterns.items():
            if pattern.match(api_key):
                return provider

        return None

    def detect_from_model(self, model: str) -> Optional[Provider]:
        """
        Detect provider from model name.

        Args:
            model: The model identifier

        Returns:
            Provider enum or None if not detected
        """
        if not model or not isinstance(model, str):
            return None

        for provider, pattern in self.model_patterns.items():
            if pattern.match(model):
                return provider

        return None

    def detect_from_headers(self, headers: dict) -> Optional[Provider]:
        """
        Detect provider from request headers.

        Args:
            headers: HTTP request headers (dict)

        Returns:
            Provider enum or None if not detected
        """
        if not headers or not isinstance(headers, dict):
            return None

        # Check Authorization header
        auth = headers.get('Authorization', '')
        if 'Bearer' in auth:
            # Extract key after Bearer
            parts = auth.split()
            if len(parts) == 2:
                key = parts[1]
                return self.detect_from_key(key)

        # Check X-API-Key header
        api_key = headers.get('X-API-Key') or headers.get('x-api-key', '')
        if api_key:
            return self.detect_from_key(api_key)

        # Check provider-specific headers
        if headers.get('anthropic-version'):
            return Provider.ANTHROPIC
        if headers.get('openai-organization'):
            return Provider.OPENAI
        if headers.get('google-cloud-project'):
            return Provider.GOOGLE

        return None

    def detect(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> Tuple[Optional[Provider], str]:
        """
        Multi-strategy provider detection.

        Tries detection in order: key → model → headers

        Args:
            api_key: API key to check
            model: Model name to check
            headers: Request headers to check

        Returns:
            Tuple of (Provider, reason_string) or (None, reason)
        """
        if api_key:
            provider = self.detect_from_key(api_key)
            if provider:
                return provider, f"detected from API key format"

        if model:
            provider = self.detect_from_model(model)
            if provider:
                return provider, f"detected from model name"

        if headers:
            provider = self.detect_from_headers(headers)
            if provider:
                return provider, f"detected from request headers"

        return None, "no provider detected"
