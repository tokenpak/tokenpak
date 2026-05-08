# SPDX-License-Identifier: Apache-2.0
"""``[TIP: ...]`` header parser, stripper, and directive registry.

Grammar (v1):

    [TIP: <key>(=<value>)?( <key>(=<value>)?)*]

The header MUST appear at the very front of the FIRST user-text segment of
the request body (after optional leading whitespace). Mid-sentence tokens
are NOT directives — they're content the model should see.

This module is the single source of truth for the v1 directive vocabulary.
Adding a new directive means adding to ``DIRECTIVE_REGISTRY`` here and
nowhere else (per ``feedback_always_dynamic``).
"""

from __future__ import annotations

import json
import re
from typing import Optional

from .contracts import TIPDirective

# ---------------------------------------------------------------------------
# Directive registry — single source of truth for v1 functions
# ---------------------------------------------------------------------------

# Each entry: function name → (handler, doc).
# Handler signature: (directive: TIPDirective, value: str | None) -> None
# (mutates directive in place).

def _set_allow(d: TIPDirective, v: Optional[str]) -> None:
    if v in ("once", "15m", "session"):
        d.allow_scope = v  # type: ignore[assignment]
    # ``allow=on`` is treated as ``allow=session`` for ergonomics.
    elif v in ("on", "true", "1"):
        d.allow_scope = "session"

def _set_bypass(d: TIPDirective, v: Optional[str]) -> None:
    if v is None or v in ("on", "true", "1", "yes"):
        d.bypass = True
    elif v in ("off", "false", "0", "no"):
        d.bypass = False

def _set_max(d: TIPDirective, v: Optional[str]) -> None:
    if not v:
        return
    v = v.strip()
    # $X.YY → max_cost_usd
    if v.startswith("$"):
        try:
            d.max_cost_usd = float(v[1:])
        except ValueError:
            pass
        return
    # Nk_tokens / Nm_tokens / N_tokens
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)([km])?_?tokens?$", v.lower())
    if m:
        n = float(m.group(1))
        suf = m.group(2)
        mult = {"k": 1_000, "m": 1_000_000}.get(suf or "", 1)
        d.max_tokens = int(n * mult)
        return
    # Bare number → tokens
    try:
        d.max_tokens = int(float(v))
    except ValueError:
        pass

def _set_estimate(d: TIPDirective, v: Optional[str]) -> None:
    if v is None or v in ("on", "true", "1", "yes"):
        d.estimate_only = True

def _set_cancel(d: TIPDirective, v: Optional[str]) -> None:
    if v is None or v in ("on", "true", "1", "yes"):
        d.cancel = True

def _set_reason(d: TIPDirective, v: Optional[str]) -> None:
    if v is not None:
        # Strip surrounding quotes if present.
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        d.reason = v


DIRECTIVE_REGISTRY: dict = {
    "allow":    (_set_allow,    "Authorize the held request: once / 15m / session."),
    "bypass":   (_set_bypass,   "Skip Yes/No prompt; still subject to hard-block."),
    "max":      (_set_max,      "Cost ceiling ($N) or token ceiling (Nk_tokens / Nm_tokens)."),
    "estimate": (_set_estimate, "Return RiskEstimate JSON, no provider call."),
    "cancel":   (_set_cancel,   "Discard any pending request for this session."),
    "reason":   (_set_reason,   "Free-text annotation for audit log."),
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Match `[TIP: ...]` at the very beginning of a user-text segment, allowing
# only leading whitespace before the bracket. Capture the inside.
_TIP_RE = re.compile(r"^\s*\[TIP:\s*([^\]]*)\]\s*", re.IGNORECASE)

# Tokenize key=value pairs inside the TIP body. Values can be:
# - bare alphanumerics ("once", "on", "1m_tokens")
# - $-prefixed amounts ("$10", "$10.50")
# - quoted strings ("planned refactor")
_KV_RE = re.compile(r"""
    (?P<key>[a-zA-Z_]+)
    (?:=(?:
        "(?P<qval>[^"]*)" |
        '(?P<sval>[^']*)' |
        (?P<bval>[^\s\]]+)
    ))?
""", re.VERBOSE)


def parse_tip_header(text: str) -> tuple[Optional[TIPDirective], str]:
    """Parse a leading ``[TIP: ...]`` header from a string.

    Returns ``(directive, remainder)``. If no header is present (or it isn't
    at the start), returns ``(None, original_text)``.
    """
    if not text:
        return None, text
    m = _TIP_RE.match(text)
    if not m:
        return None, text

    inside = m.group(1).strip()
    directive = TIPDirective(raw=m.group(0).strip())

    for kv in _KV_RE.finditer(inside):
        key = kv.group("key").lower()
        val = kv.group("qval") or kv.group("sval") or kv.group("bval")
        handler = DIRECTIVE_REGISTRY.get(key)
        if handler is None:
            directive.unknown_keys.append(key)
            continue
        try:
            handler[0](directive, val)
        except Exception:
            directive.unknown_keys.append(f"{key}=parse_error")

    remainder = text[m.end():]
    return directive, remainder


def parse_and_strip_tip_header(body: bytes) -> tuple[Optional[TIPDirective], bytes]:
    """Strip a leading ``[TIP: ...]`` from the FIRST user-text segment.

    Operates on the parsed JSON shape for Anthropic ``/v1/messages``
    bodies. For other shapes, attempts a leading-prefix strip on the
    decoded body and re-encodes.

    Returns ``(directive, modified_body)``. ``modified_body is body``
    when no directive is present (zero-cost path).
    """
    if not body:
        return None, body

    try:
        body_text = body.decode("utf-8", errors="replace")
        body_json = json.loads(body_text)
    except Exception:
        # Non-JSON body — try a raw-prefix strip.
        d, rem = parse_tip_header(body.decode("utf-8", errors="replace"))
        return d, rem.encode("utf-8") if d else body

    msgs = body_json.get("messages") or []
    if not isinstance(msgs, list):
        return None, body

    # Find the first user message and mutate its content.
    for msg in msgs:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            d, rem = parse_tip_header(content)
            if d is None:
                return None, body
            msg["content"] = rem
            return d, json.dumps(body_json).encode("utf-8")
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    d, rem = parse_tip_header(str(blk.get("text") or ""))
                    if d is None:
                        return None, body
                    blk["text"] = rem
                    return d, json.dumps(body_json).encode("utf-8")
        # Only inspect the first user message.
        break

    return None, body


def strip_tip_header(text: str) -> str:
    """Convenience: strip a leading [TIP: ...] from a string."""
    _, rem = parse_tip_header(text)
    return rem


__all__ = [
    "DIRECTIVE_REGISTRY",
    "parse_tip_header",
    "parse_and_strip_tip_header",
    "strip_tip_header",
]
