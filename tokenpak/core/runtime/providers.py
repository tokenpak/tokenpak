"""
Provider detection for TokenPak proxy.

Provides a ``Provider`` enum and a ``detect_provider`` function that maps
an upstream target URL to the corresponding provider.  This module is the
single source of truth for provider identification — all other code should
import from here rather than performing ad-hoc hostname string checks.
"""

from enum import Enum
from urllib.parse import urlparse

__all__ = ["Provider", "detect_provider"]


class Provider(Enum):
    """Known LLM API providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    GROQ = "groq"
    XAI = "xai"
    FIREWORKS = "fireworks"
    TOGETHER = "together"
    AZURE_OPENAI = "azure_openai"
    BEDROCK = "bedrock"
    # CODEX currently routes through OpenAI endpoints.  If a distinct
    # hostname or header is introduced in the future, add detection here.
    CODEX = "codex"
    VOYAGE = "voyage"
    JINA = "jina"
    UNKNOWN = "unknown"


# Exact hostname → Provider lookup (fastest path)
_EXACT_HOSTS: dict[str, Provider] = {
    "api.anthropic.com": Provider.ANTHROPIC,
    "api.openai.com": Provider.OPENAI,
    "generativelanguage.googleapis.com": Provider.GEMINI,
    "api.groq.com": Provider.GROQ,
    "api.x.ai": Provider.XAI,
    "api.fireworks.ai": Provider.FIREWORKS,
    "api.together.xyz": Provider.TOGETHER,
    "api.together.ai": Provider.TOGETHER,
    "chatgpt.com": Provider.CODEX,
    "api.voyageai.com": Provider.VOYAGE,
    "api.jina.ai": Provider.JINA,
}

# Suffix-based checks for subdomains — evaluated in order, first match wins.
# Each entry is (suffix, Provider).
_SUFFIX_RULES: list[tuple[str, Provider]] = [
    (".anthropic.com", Provider.ANTHROPIC),
    (".openai.azure.com", Provider.AZURE_OPENAI),
    (".azure-api.net", Provider.AZURE_OPENAI),
    (".openai.com", Provider.OPENAI),
    (".googleapis.com", Provider.GEMINI),
    (".groq.com", Provider.GROQ),
    (".x.ai", Provider.XAI),
    (".fireworks.ai", Provider.FIREWORKS),
    (".together.xyz", Provider.TOGETHER),
    (".together.ai", Provider.TOGETHER),
]


def detect_provider(target_url: str) -> Provider:
    """Detect the LLM provider from an upstream *target_url*.

    The function first tries an exact hostname match (O(1) dict lookup), then
    falls back to ordered suffix checks for subdomain / regional variations,
    and finally checks for Bedrock-style hostnames that embed region codes.

    Returns ``Provider.UNKNOWN`` for unrecognised hostnames or when *target_url*
    is empty / malformed.
    """
    if not target_url:
        return Provider.UNKNOWN

    try:
        hostname = urlparse(target_url).hostname or ""
    except Exception:
        return Provider.UNKNOWN

    if not hostname:
        return Provider.UNKNOWN

    # Fast exact match
    provider = _EXACT_HOSTS.get(hostname)
    if provider is not None:
        return provider

    # Suffix / subdomain patterns
    for suffix, prov in _SUFFIX_RULES:
        if hostname.endswith(suffix):
            return prov

    # Bedrock uses region-embedded hostnames like
    # bedrock-runtime.us-east-1.amazonaws.com
    if "amazonaws.com" in hostname and ("bedrock" in hostname):
        return Provider.BEDROCK

    return Provider.UNKNOWN
