"""``DLPScanner`` — stateless match engine over a rule set.

The scanner takes a text body and returns a list of :class:`Finding`
objects — one per distinct match. Callers (the pipeline Stage or the
companion hook) decide what to do with findings based on the active
:class:`~tokenpak.core.routing.policy.Policy`.dlp_mode.

The scanner itself does NOT rewrite, block, or log. Those concerns
live in :mod:`.modes`, which is where the `off` / `warn` / `redact` /
`block` behaviors are implemented. Keeping detection and action
separate is the architectural requirement — the same findings can be
surfaced in the TUI (via companion), written to telemetry (via
services), AND enforced at the wire (via the Stage) from a single scan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from tokenpak.security.dlp.rules import DEFAULT_RULES, Rule

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Finding:
    """A single DLP hit.

    The ``matched`` value is the raw matched substring; callers that
    log findings should rely on :meth:`redacted` instead so logs don't
    leak the very secret we detected.
    """

    rule_id: str
    rule_label: str
    severity: str
    start: int
    end: int
    matched: str

    def redacted(self) -> str:
        """Safe-to-log representation: shows rule + length, not content."""
        return f"<DLP:{self.rule_id}:len={len(self.matched)}>"


class DLPScanner:
    """Stateless scanner. Safe to share across threads / pipelines."""

    def __init__(self, rules: Optional[Sequence[Rule]] = None) -> None:
        self._rules: tuple[Rule, ...] = tuple(rules) if rules else DEFAULT_RULES

    @property
    def rules(self) -> tuple[Rule, ...]:
        return self._rules

    def scan(self, text: str) -> list[Finding]:
        """Return every distinct match across every rule.

        Overlapping matches from different rules are kept independently
        (e.g. an OpenAI key regex and a generic high-entropy regex can
        both fire on the same span). De-duplication of identical spans
        from the SAME rule is done here; across rules it's not, because
        two rules firing on one span is useful signal.
        """
        if not text:
            return []
        findings: list[Finding] = []
        for rule in self._rules:
            seen_spans: set[tuple[int, int]] = set()
            for m in rule.pattern.finditer(text):
                span = (m.start(), m.end())
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                findings.append(
                    Finding(
                        rule_id=rule.id,
                        rule_label=rule.label,
                        severity=rule.severity,
                        start=m.start(),
                        end=m.end(),
                        matched=m.group(0),
                    )
                )
        return findings

    def scan_bytes(self, body: bytes) -> list[Finding]:
        """Convenience: decode bytes as UTF-8 (errors=replace) + scan."""
        if not body:
            return []
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8", errors="replace")
        return self.scan(text)


__all__ = ["DLPScanner", "Finding"]
