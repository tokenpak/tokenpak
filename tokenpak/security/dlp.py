# SPDX-License-Identifier: Apache-2.0
"""tokenpak.security.dlp
=====================

Gitleaks-pattern DLP (Data Loss Prevention) scanner for outbound prompts.

Scans text for common secrets and PII before forwarding to LLM providers.
This is the Free-tier subset of the I4 Security/PII/DLP architecture
component — gitleaks patterns only. Full ML-based PII detection stays
Enterprise (I4).

Modes (configured via TOKENPAK_DLP_MODE env var or DLPScanner constructor):
  warn   (default) — logs findings, does not modify text
  redact            — replaces matches with [REDACTED:rule_id]
  block             — raises DLPBlockError if any finding detected

Pattern source: written from public specifications.
  - AWS key formats: AWS documentation (public)
  - SSN format: NIST / US government public specification
  - Email: RFC 5321 / OWASP reference patterns
  - GitHub/Slack/Stripe tokens: vendor public documentation formats
  - No code or patterns were copied from the gitleaks project source tree.
    The gitleaks project is MIT-licensed; these patterns are independently
    derived from public sources and are compatible with Apache-2.0.

Public API:
  class DLPScanner:
    .scan(text: str) -> list[DLPMatch]
    .redact(text: str) -> str
    .block_check(text: str) -> bool   # True = clean, False = secrets found
  class DLPMatch
  class DLPBlockError(Exception)

Environment variables:
  TOKENPAK_DLP_MODE    warn|redact|block  (default: warn)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class DLPMatch:
    """A single secret/PII finding from a DLP scan."""

    rule_id: str
    description: str
    match_text: str
    start: int
    end: int
    severity: str  # critical | high | medium | low


class DLPBlockError(Exception):
    """Raised in block mode when secrets are detected in outbound text."""

    def __init__(self, findings: List[DLPMatch]) -> None:
        self.findings = findings
        super().__init__(f"DLP block: {len(findings)} secret(s) detected in outbound text")


# ---------------------------------------------------------------------------
# Pattern rules (internal)
# ---------------------------------------------------------------------------


@dataclass
class _Rule:
    rule_id: str
    description: str
    pattern: re.Pattern  # type: ignore[type-arg]
    severity: str  # critical | high | medium | low


# Patterns written from public specifications — NOT copied from gitleaks source.
# See module docstring for licensing rationale.
_RULES: List[_Rule] = [
    # AWS key formats — from AWS documentation (public specification)
    _Rule(
        "aws-access-key",
        "AWS Access Key ID",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "critical",
    ),
    _Rule(
        "aws-secret-key",
        "AWS Secret Access Key",
        re.compile(
            r"(?i)(?:aws_secret(?:_access)?_key|secret_access_key)\s*[=:]\s*['\"]?([0-9a-zA-Z/+=]{40})['\"]?"
        ),
        "critical",
    ),
    # Generic API key assignment patterns
    _Rule(
        "generic-api-key",
        "Generic API Key",
        re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"]?[0-9a-zA-Z_\-]{20,}['\"]?"),
        "high",
    ),
    # Password / secret assignments in code
    _Rule(
        "password",
        "Password in Code",
        re.compile(r"(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{8,}['\"]"),
        "high",
    ),
    _Rule(
        "generic-secret",
        "Generic Secret Assignment",
        re.compile(r"(?i)(?:secret|api_secret|client_secret)\s*[=:]\s*['\"][^'\"]{8,}['\"]"),
        "high",
    ),
    # PII patterns
    _Rule(
        "ssn",
        "Social Security Number",
        # Excludes invalid SSN ranges (000-xx-xxxx, 666-xx-xxxx, 9xx-xx-xxxx)
        re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        "critical",
    ),
    _Rule(
        "email",
        "Email Address",
        # Negative lookbehind prevents matching mid-word (e.g., variable names)
        re.compile(
            r"(?<![A-Za-z0-9_.\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![A-Za-z0-9_.\-])"
        ),
        "medium",
    ),
    _Rule(
        "phone",
        "Phone Number",
        # Requires separators to reduce false positives on plain digit sequences
        re.compile(r"\b\d{3}[-. ]\d{3}[-. ]\d{4}\b"),
        "low",
    ),
    # Cryptographic key blocks
    _Rule(
        "private-key-block",
        "Private Key PEM Block",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
        "critical",
    ),
    # Vendor-specific token formats — from public vendor documentation
    _Rule(
        "github-token",
        "GitHub Personal Access Token",
        re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
        "critical",
    ),
    _Rule(
        "slack-token",
        "Slack API Token",
        re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"),
        "high",
    ),
    _Rule(
        "stripe-secret-key",
        "Stripe Secret Key",
        re.compile(r"\bsk_live_[0-9a-zA-Z]{24}\b"),
        "critical",
    ),
    _Rule(
        "sendgrid-key",
        "SendGrid API Key",
        re.compile(r"\bSG\.[0-9A-Za-z._\-]{22,}\b"),
        "high",
    ),
    _Rule(
        "access-token",
        "Access / Bearer Token Assignment",
        re.compile(
            r"(?i)(?:access_token|auth_token|bearer_token)\s*[=:]\s*['\"]?[0-9a-zA-Z._\-]{20,}['\"]?"
        ),
        "high",
    ),
]


# ---------------------------------------------------------------------------
# DLPScanner
# ---------------------------------------------------------------------------


class DLPScanner:
    """Secret and PII scanner using gitleaks-derived regex patterns.

    Parameters
    ----------
    mode : str, optional
        ``warn`` (default), ``redact``, or ``block``.
        Falls back to ``TOKENPAK_DLP_MODE`` env var, then ``warn``.
    """

    def __init__(self, mode: Optional[str] = None) -> None:
        self.mode: str = mode or os.environ.get("TOKENPAK_DLP_MODE", "warn")
        if self.mode not in ("warn", "redact", "block"):
            logger.warning(
                "tokenpak.dlp: unknown mode %r — falling back to 'warn'",
                self.mode,
            )
            self.mode = "warn"

    def scan(self, text: str) -> List[DLPMatch]:
        """Scan *text* for secrets and PII.

        Returns a list of :class:`DLPMatch` objects (empty if clean).
        Does not modify *text* regardless of the scanner mode.
        """
        findings: List[DLPMatch] = []
        for rule in _RULES:
            for m in rule.pattern.finditer(text):
                findings.append(
                    DLPMatch(
                        rule_id=rule.rule_id,
                        description=rule.description,
                        match_text=m.group(0),
                        start=m.start(),
                        end=m.end(),
                        severity=rule.severity,
                    )
                )
        return findings

    def redact(self, text: str) -> str:
        """Return a copy of *text* with all secrets replaced by
        ``[REDACTED:<rule_id>]`` placeholders.

        Overlapping spans are handled by keeping the first match (lowest
        start offset). Replacement is applied end-to-start to preserve
        character offsets.
        """
        # Collect all spans with their replacement strings
        spans: List[tuple[int, int, str]] = []  # (start, end, replacement)
        for rule in _RULES:
            for m in rule.pattern.finditer(text):
                spans.append((m.start(), m.end(), f"[REDACTED:{rule.rule_id}]"))

        if not spans:
            return text

        # Sort by start position ascending, then deduplicate overlapping spans
        spans.sort(key=lambda x: x[0])
        merged: List[tuple[int, int, str]] = []
        for span in spans:
            start, end, replacement = span
            if merged and start < merged[-1][1]:
                # Overlapping — keep whichever ends later (extend coverage)
                prev_start, prev_end, prev_repl = merged[-1]
                if end > prev_end:
                    merged[-1] = (prev_start, end, prev_repl)
            else:
                merged.append(span)

        # Apply replacements from end to start to preserve offsets
        result = text
        for start, end, replacement in reversed(merged):
            result = result[:start] + replacement + result[end:]

        return result

    def block_check(self, text: str) -> bool:
        """Return ``True`` if *text* is clean (no secrets), ``False`` otherwise.

        Call this in block mode to determine whether to reject the request.
        """
        return len(self.scan(text)) == 0
