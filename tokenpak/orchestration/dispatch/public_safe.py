# SPDX-License-Identifier: Apache-2.0
"""Public-safe sanitization for Dispatch delivery/receipt surfaces.

Dispatch output intended for public surfaces — Delivery Package summaries and
exported Receipts — is sanitized through this public-safe path before display.
Three classes of machine-local / internal token are redacted by *pattern* so the
sanitizer carries no embedded denylist of its own:

* **machine-local home paths** — absolute home roots (``/home/<user>/...``,
  ``/Users/<user>/...``) and home-relative dot-directory paths (``~/.<dir>/...``,
  which covers local tool state/config directories);
* **internal task-ID-shaped tokens** — an uppercase prefix, a dash, then two or
  more digits (e.g. ``ABC-1234``). The shape is matched generically rather than
  from a fixed prefix list, so no specific prefix is hardcoded here; the
  two-digit minimum avoids eating ordinary tokens like ``UTF-8`` or ``SHA-1``.

Callers that need to redact additional exact terms — for example an internal
caller injecting a set of operator/agent names that should never reach a public
surface — pass them via the ``extra_terms`` argument. The open-source default is
empty, so this module ships with **no** embedded internal names or prefixes.

The canonical project-wide enforcement of public-safe defaults lives in CI as
``.github/workflows/identity-language-check.yml`` (a checker, not an importable
redactor). This module implements a **minimal, focused redaction pass** scoped
to the Dispatch delivery/receipt surfaces. If a shared Python sanitizer lands
later, these call sites should delegate to it.

The redaction is conservative: it only touches string *values* (keys are left
intact so downstream parsers keep working) and replaces matched tokens with a
neutral ``[redacted]`` marker.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

_REDACTED = "[redacted]"

# Machine-local home paths. Two forms, both matched generically so no literal
# absolute path or tool-state directory name is embedded in this source:
#   * absolute home roots: /home/<user>/...  and  /Users/<user>/...
#   * home-relative dot-directory paths: ~/.<dir>/...  (covers local tool
#     state/config directories under the user's home).
# Greedy to the end of the path token so the full leak is removed.
_HOME_PATH_RE = re.compile(r"(?:/(?:home|Users)/[^/\s]+|~/\.[^/\s]+)/\S*")

# Internal task-ID-shaped tokens: an uppercase prefix (2-5 letters, optionally
# with internal dashes), a dash, then >=2 digits. Matched by shape rather than
# from a fixed prefix list, so no specific prefix is hardcoded. The two-digit
# minimum keeps ordinary tokens like "UTF-8" / "SHA-1" intact.
_TASK_ID_RE = re.compile(r"\b[A-Z][A-Z-]{1,8}-\d{2,}\b")

# Pattern-based redactions applied to every string. Order is stable but the
# patterns do not overlap, so ordering is not load-bearing here.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    _HOME_PATH_RE,
    _TASK_ID_RE,
)


def _extra_terms_pattern(extra_terms: Sequence[str]) -> re.Pattern[str] | None:
    """Compile a word-boundary, case-insensitive alternation for *extra_terms*.

    Empty / falsy entries are skipped. Returns ``None`` when nothing is left to
    match, so the common (open-source default) empty case adds no work.
    """
    terms = [t for t in extra_terms if isinstance(t, str) and t.strip()]
    if not terms:
        return None
    # Longest-first so a multi-word term (e.g. "Two Words") wins over a substring.
    terms.sort(key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in terms)
    return re.compile(r"\b(?:" + alternation + r")\b", re.IGNORECASE)


def sanitize_public_text(text: str, extra_terms: Iterable[str] = ()) -> str:
    """Redact home paths and internal-id-shaped tokens from *text*.

    ``extra_terms`` is an optional caller-supplied denylist of exact terms to
    also redact (word-boundary, case-insensitive). The open-source default is
    empty. Idempotent and safe on already-clean text (returns it unchanged).
    Non-string input is returned untouched.
    """
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pattern in _PATTERNS:
        out = pattern.sub(_REDACTED, out)
    extra = _extra_terms_pattern(tuple(extra_terms))
    if extra is not None:
        out = extra.sub(_REDACTED, out)
    return out


def sanitize_public_obj(obj: Any, extra_terms: Iterable[str] = ()) -> Any:
    """Recursively sanitize every string *value* in a JSON-like structure.

    Dict keys are left intact (so the shape/contract is preserved); only string
    values, and strings nested in lists/tuples, are passed through
    :func:`sanitize_public_text`. ``extra_terms`` is forwarded to every string.
    Returns a new structure; the input is not mutated.
    """
    # Materialize once so a generator passed as extra_terms is not exhausted by
    # the first nested string.
    terms = tuple(extra_terms)
    if isinstance(obj, str):
        return sanitize_public_text(obj, terms)
    if isinstance(obj, dict):
        return {k: sanitize_public_obj(v, terms) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_public_obj(v, terms) for v in obj]
    return obj


__all__ = ["sanitize_public_text", "sanitize_public_obj"]
