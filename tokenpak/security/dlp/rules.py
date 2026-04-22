"""DLP rules — named regex patterns for known secret shapes.

Each rule carries:

- ``id``       — stable identifier used in logs, redactions, and allowlists.
- ``label``    — human-readable name ("AWS access key").
- ``pattern``  — compiled regex against the request body (text).
- ``severity`` — informational ("info"|"warn"|"high") used by modes.

The default rule set covers the most common provider API keys seen in
Claude Code / SDK traffic. Additional rules can be registered at
runtime via :func:`register_rule`; the canonical set lives here so the
behavior is identical across proxy and companion entrypoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Pattern


Severity = Literal["info", "warn", "high"]


@dataclass(slots=True, frozen=True)
class Rule:
    """A single DLP detection rule."""

    id: str
    label: str
    pattern: Pattern[str]
    severity: Severity = "warn"


# --- Default rule set ---------------------------------------------------
#
# Patterns are deliberately tight — false-positive cost on redact/block
# modes is high. When in doubt, the rule goes into a separate
# high-entropy / heuristic module rather than here.

_DEFAULT_PATTERNS: list[tuple[str, str, str, Severity]] = [
    (
        "aws_access_key",
        "AWS Access Key ID",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        "high",
    ),
    (
        "aws_secret_key",
        "AWS Secret Access Key",
        r"(?i)aws.{0,20}?(?:secret|private).{0,20}?['\"`]([A-Za-z0-9/+=]{40})['\"`]",
        "high",
    ),
    (
        "stripe_live_key",
        "Stripe live secret key",
        r"\bsk_live_[0-9a-zA-Z]{24,}\b",
        "high",
    ),
    (
        "stripe_restricted_key",
        "Stripe restricted key",
        r"\brk_live_[0-9a-zA-Z]{24,}\b",
        "high",
    ),
    (
        "github_pat",
        "GitHub Personal Access Token",
        r"\bghp_[0-9A-Za-z]{36}\b",
        "high",
    ),
    (
        "github_fine_grained",
        "GitHub fine-grained token",
        r"\bgithub_pat_[0-9A-Za-z_]{22,}\b",
        "high",
    ),
    (
        "openai_api_key",
        "OpenAI API key",
        # Project keys: sk-proj-..., legacy keys: sk-... (48+ chars)
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b",
        "high",
    ),
    (
        "anthropic_api_key",
        "Anthropic API key",
        r"\bsk-ant-api\d{2}-[A-Za-z0-9_-]{20,}\b",
        "high",
    ),
    (
        "google_api_key",
        "Google API key",
        r"\bAIza[0-9A-Za-z_-]{35}\b",
        "high",
    ),
    (
        "slack_token",
        "Slack token",
        r"\bxox[aboprs]-[0-9A-Za-z]{10,}-[0-9A-Za-z]{10,}(?:-[0-9A-Za-z-]{10,})?\b",
        "high",
    ),
    (
        "private_key_pem",
        "PEM private key block",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
        "high",
    ),
]


DEFAULT_RULES: tuple[Rule, ...] = tuple(
    Rule(id=i, label=l, pattern=re.compile(p), severity=s)
    for (i, l, p, s) in _DEFAULT_PATTERNS
)


__all__ = ["Rule", "DEFAULT_RULES", "Severity"]
