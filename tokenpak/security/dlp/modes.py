"""DLP mode executors — what to do with the findings the scanner produced.

The four modes from :class:`tokenpak.core.routing.policy.Policy`:

- ``off``    — scanner is not run; the Stage short-circuits before
                calling into this module at all.
- ``warn``   — findings logged, body forwarded unchanged.
- ``redact`` — findings replaced with ``<REDACTED:rule_id>`` tokens
                inline; modified body forwarded.
- ``block``  — any finding halts the request; caller emits an error
                response.

This module returns :class:`DLPOutcome` — an immutable description of
what was decided. It does NOT write to the network or raise. Callers
(the pipeline Stage, companion hook) turn an ``Outcome`` into
side-effects in their own layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from tokenpak.security.dlp.scanner import Finding


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DLPOutcome:
    """What the mode executor decided.

    Fields
    ------
    mode:
        Echo of the mode name that produced this outcome.
    findings:
        Every finding the scanner produced (unchanged by the executor).
    new_body:
        The body to forward downstream. For ``warn`` and ``block``
        this equals the input body. For ``redact`` this is the
        rewritten body.
    blocked:
        True only when ``mode='block'`` fired AND at least one finding
        was present. Callers must abort the request in that case.
    """

    mode: str
    findings: Sequence[Finding]
    new_body: bytes
    blocked: bool = False


def _redact_text(text: str, findings: Sequence[Finding]) -> str:
    """Replace each finding span with ``<REDACTED:rule_id>``.

    Spans are rewritten in reverse order so earlier offsets remain
    valid while we mutate the string.
    """
    if not findings:
        return text
    # Sort by start descending so later edits don't shift earlier offsets.
    ordered = sorted(findings, key=lambda f: f.start, reverse=True)
    out = text
    for f in ordered:
        out = out[: f.start] + f"<REDACTED:{f.rule_id}>" + out[f.end :]
    return out


def apply_mode(
    mode: str,
    body: bytes,
    findings: Sequence[Finding],
) -> DLPOutcome:
    """Run the mode's action and return an outcome.

    Never raises. Unknown modes are treated as ``"off"`` (fail-open
    so a mis-configured policy can't block traffic silently).
    """
    if not findings or mode == "off":
        return DLPOutcome(mode=mode, findings=findings, new_body=body, blocked=False)

    if mode == "warn":
        logger.warning(
            "tokenpak.dlp: %d secret(s) detected in outbound request: %s "
            "(set Policy.dlp_mode='redact' to auto-redact or 'block' to halt)",
            len(findings),
            ", ".join(f.redacted() for f in findings),
        )
        return DLPOutcome(mode=mode, findings=findings, new_body=body, blocked=False)

    if mode == "redact":
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8", errors="replace")
        redacted_text = _redact_text(text, findings)
        logger.info(
            "tokenpak.dlp: redacted %d secret(s): %s",
            len(findings),
            ", ".join(f.redacted() for f in findings),
        )
        return DLPOutcome(
            mode=mode,
            findings=findings,
            new_body=redacted_text.encode("utf-8"),
            blocked=False,
        )

    if mode == "block":
        logger.warning(
            "tokenpak.dlp: BLOCKING outbound request — %d secret(s) detected: %s",
            len(findings),
            ", ".join(f.redacted() for f in findings),
        )
        return DLPOutcome(mode=mode, findings=findings, new_body=body, blocked=True)

    # Unknown mode → fail-open (treat as 'off'). Intentional: never
    # accidentally block traffic because an env var typo.
    logger.warning(
        "tokenpak.dlp: unknown mode %r — treating as 'off' (findings not acted on)",
        mode,
    )
    return DLPOutcome(mode="off", findings=findings, new_body=body, blocked=False)


__all__ = ["DLPOutcome", "apply_mode"]
