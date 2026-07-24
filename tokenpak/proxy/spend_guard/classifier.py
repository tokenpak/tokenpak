# SPDX-License-Identifier: Apache-2.0
"""Internal request traffic classifier.

Attributes every proxied request to exactly one of three canonical classes
(``managed`` / ``raw_claude_observed`` / ``external_untagged``) using the
strict detection precedence defined in :func:`classify`.

The attributed class has exactly two consumers wired today:

- the listener admission gate in ``tokenpak.proxy.server``, which bounds
  managed model-endpoint requests with an admission lease before a worker
  thread is created; and
- the upstream forwarding boundary in ``tokenpak.proxy.headers``, which uses
  :func:`is_internal_header` to keep internal markers from leaving the proxy.

No other consumer (accounting, attribution, persistence) is wired to this
classification in this tree.

The classifier is read-only on the request: the only output is the attributed
class + detection reason + agent attribution. It NEVER mutates the body, NEVER
reads OS process env at request time, and NEVER infers the class from
URL / port / remote IP.

``__all__`` is intentionally empty: this is internal proxy plumbing, not released
public API. Every consumer imports the specific name it needs function-locally so
these helpers stay off the public API surface.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

# ── Canonical request classes. Stable string literals — no synonyms,
#    no plurals, no aliasing. These three values are the canonical set.
MANAGED = "managed"
RAW_CLAUDE_OBSERVED = "raw_claude_observed"
EXTERNAL_UNTAGGED = "external_untagged"

REQUEST_CLASSES = frozenset({MANAGED, RAW_CLAUDE_OBSERVED, EXTERNAL_UNTAGGED})

# ── Canonical detection reasons. Persisted alongside the class on audit rows
#    for forensic reconstruction. No synonyms (``header-agent`` /
#    ``agentHeader`` are forbidden). ``env_launcher`` is a reserved literal for
#    the launcher-synthesised process-env marker; no header grammar for it is
#    ratified in this tree, so :func:`classify` never returns it today.
HEADER_AGENT = "header_agent"
HEADER_MANAGED = "header_managed"
ENV_LAUNCHER = "env_launcher"
UA_CLAUDE_CODE = "ua_claude_code"
NO_MARKER = "no_marker"

DETECTION_REASONS = frozenset(
    {HEADER_AGENT, HEADER_MANAGED, ENV_LAUNCHER, UA_CLAUDE_CODE, NO_MARKER}
)

# ── Header names (lowercase for case-insensitive matching).
HEADER_NAME_AGENT = "x-tokenpak-agent"
HEADER_NAME_MANAGED = "x-tokenpak-managed"

# The managed marker grammar is exact: ``X-Tokenpak-Managed: 1``. No other
# value (including truthy-looking tokens such as ``true`` / ``yes`` / ``on``)
# marks a request managed. Surrounding whitespace is tolerated because header
# values are stripped before comparison.
MANAGED_MARKER_VALUE = "1"

# TokenPak-internal header namespace. Any header whose name starts with one of
# these prefixes is internal proxy plumbing and MUST be stripped before
# upstream forwarding (re-synthesised at each hop, never passed through, even
# to a TokenPak-managed downstream proxy).
INTERNAL_HEADER_PREFIXES = ("x-tokenpak-", "x-tpk-")

# Canonical Claude Code CLI User-Agent substring.
# Matched case-insensitively, and ONLY after every higher-precedence marker has
# failed. Single source of truth — do not duplicate this literal elsewhere.
CLAUDE_CODE_UA_SUBSTRING = "claude-cli"

# "managed marker present, but no agent name to attribute" sentinel.
# Empty string, never fabricated.
UNKNOWN_MANAGED_AGENT = ""

# Intentionally empty — keeps every name above off the public-API snapshot.
__all__: list[str] = []


@dataclass(frozen=True)
class Classification:
    """Result of classifying one request — read-only attribution.

    ``request_class`` is one of :data:`REQUEST_CLASSES`; ``reason`` is one of
    :data:`DETECTION_REASONS`; ``agent_attribution`` is the lower-cased
    ``X-Tokenpak-Agent`` value for ``header_agent`` matches, else ``""``.
    """

    request_class: str
    reason: str
    agent_attribution: str = ""


def _header(headers: Mapping[str, str] | None, name: str) -> str:
    """Case-insensitive header lookup → stripped string value (or "")."""
    if not headers:
        return ""
    try:
        items = headers.items()
    except AttributeError:
        return ""
    target = name.lower()
    for key, value in items:
        if str(key).lower() == target:
            return str(value).strip()
    return ""


def is_internal_header(name: object) -> bool:
    """True when *name* is in the TokenPak-internal header namespace.

    Internal headers MUST never be forwarded to a provider upstream — every
    forwarding strategy strips them at the upstream boundary
    (``tokenpak.proxy.headers``).
    """
    return str(name).lower().startswith(INTERNAL_HEADER_PREFIXES)


def classify(headers: Mapping[str, str] | None) -> Classification:
    """Attribute a request via the defined precedence chain.

    Evaluates markers in strict order, returning on the first match:

      1. ``X-Tokenpak-Agent: <name>``  → ``managed`` (reason ``header_agent``),
         agent attribution = the lower-cased header value.
      2. ``X-Tokenpak-Managed: 1``     → ``managed`` (reason ``header_managed``),
         agent attribution = unknown-managed. The marker value must be exactly
         ``1`` after whitespace stripping; no alternate values are recognised.
      3. Claude Code UA substring      → ``raw_claude_observed`` (reason
         ``ua_claude_code``) when no higher marker matched.
      4. otherwise                     → ``external_untagged`` (reason
         ``no_marker``).

    Read-only: never mutates ``headers``, never consults ``os.environ``, never
    infers from URL / port / remote IP.
    """
    agent = _header(headers, HEADER_NAME_AGENT)
    if agent:
        return Classification(MANAGED, HEADER_AGENT, agent.lower())

    if _header(headers, HEADER_NAME_MANAGED) == MANAGED_MARKER_VALUE:
        return Classification(MANAGED, HEADER_MANAGED, UNKNOWN_MANAGED_AGENT)

    ua = _header(headers, "user-agent").lower()
    if ua and CLAUDE_CODE_UA_SUBSTRING in ua:
        return Classification(RAW_CLAUDE_OBSERVED, UA_CLAUDE_CODE, "")

    return Classification(EXTERNAL_UNTAGGED, NO_MARKER, "")


def strip_managed_headers(headers: MutableMapping[str, str]) -> list[str]:
    """Remove TokenPak-internal headers from a forward-bound header map.

    Removes every header in the internal namespace (see
    :func:`is_internal_header`). Mutates ``headers`` in place
    (case-insensitive) and returns the list of header names actually removed.
    These markers are re-synthesised at each hop and MUST NEVER be forwarded
    upstream — even to a TokenPak-managed downstream proxy. A no-op (returns
    ``[]``) when none are present or ``headers`` is not a mutable mapping.
    """
    removed: list[str] = []
    try:
        keys = list(headers.keys())
    except AttributeError:
        return removed
    for key in keys:
        if is_internal_header(key):
            try:
                del headers[key]
            except (KeyError, TypeError):
                continue
            removed.append(key)
    return removed
