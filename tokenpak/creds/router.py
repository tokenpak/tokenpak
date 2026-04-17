# SPDX-License-Identifier: Apache-2.0
"""Credential selection at request time.

The router is a **pure function** — given a context, it returns a
credential (or raises :class:`AmbiguousRoute` / :class:`NoRoute`).
No side effects, no caching. Callers own their own caching.

Three-layer decision chain (first match wins):

1. **Explicit tag** — context.explicit_tag (typically from an
   ``X-Tokenpak-Credential`` header or a ``/cred/<id>/...`` path). If
   the id is unknown, raise :class:`NoRoute` loudly; don't silently
   fall through.
2. **Route rule** — ``~/.tokenpak/routes.toml`` maps caller identity
   + destination to a credential id. First matching rule wins.
3. **Platform default** — pick the first healthy credential whose
   platform matches the destination host.

Ambiguity at any layer = :class:`AmbiguousRoute`. Fail loudly per
the Kevin 2026-04-16 scope decision — never silently route to the
wrong account.
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from .model import Credential, KIND_OAUTH
from .providers import discover_all


ROUTES_PATH = Path.home() / ".tokenpak" / "routes.toml"


class RouterError(Exception):
    """Base for router failures. Carries a human message + a short tag
    so callers (proxy / CLI) can distinguish failure modes."""

    tag = "router-error"


class NoRoute(RouterError):
    tag = "no-route"


class AmbiguousRoute(RouterError):
    tag = "ambiguous"


@dataclass(frozen=True)
class RouteContext:
    """Everything the router needs to decide.

    ``destination_host`` is the host the request is bound for (the
    upstream provider, e.g. ``api.openai.com``). ``caller_identity`` is
    a free-form string the caller declares about itself (e.g.
    ``openclaw:trix:main``) — the router matches it against glob
    patterns in routes.toml.
    """

    destination_host: str
    caller_identity: Optional[str] = None
    explicit_tag: Optional[str] = None


@dataclass(frozen=True)
class RouteDecision:
    credential: Credential
    reason: str         # human-readable "why this one"
    layer: str          # "explicit" | "rule" | "platform-default"


# ── routes.toml ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rule:
    """One row from routes.toml.

    Any field left as ``None`` (or empty list) is a wildcard; all
    specified fields must match.
    """

    cred_id: str
    callers: tuple[str, ...] = ()       # glob patterns
    destinations: tuple[str, ...] = ()  # glob patterns against host
    tags: tuple[str, ...] = ()          # exact match on X-Tokenpak-Tag (future use)


def load_rules(path: Optional[Path] = None) -> list[Rule]:
    """Parse routes.toml. Missing/invalid files yield ``[]``.

    Path defaults are resolved at call time (not def time) so tests
    and callers can monkey-patch ``ROUTES_PATH`` at the module level
    and have it take effect on the next call.
    """
    path = path or ROUTES_PATH
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text())
    except Exception:
        return []

    raw = data.get("routes")
    if not isinstance(raw, list):
        return []

    rules: list[Rule] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        cred_id = row.get("credential")
        if not isinstance(cred_id, str) or not cred_id:
            continue
        rules.append(
            Rule(
                cred_id=cred_id,
                callers=_to_tuple(row.get("callers") or row.get("caller")),
                destinations=_to_tuple(row.get("destinations") or row.get("destination")),
                tags=_to_tuple(row.get("tags") or row.get("tag")),
            )
        )
    return rules


def _to_tuple(val) -> tuple[str, ...]:
    if val is None:
        return ()
    if isinstance(val, str):
        return (val,)
    if isinstance(val, (list, tuple)):
        return tuple(str(x) for x in val if x)
    return ()


def _rule_matches(rule: Rule, ctx: RouteContext) -> bool:
    if rule.destinations and not any(
        fnmatch.fnmatchcase(ctx.destination_host, pat) for pat in rule.destinations
    ):
        return False
    if rule.callers:
        if not ctx.caller_identity:
            return False
        if not any(fnmatch.fnmatchcase(ctx.caller_identity, pat) for pat in rule.callers):
            return False
    # ``tags`` is reserved for a future X-Tokenpak-Tag header; ignored for now.
    return True


# ── selection ────────────────────────────────────────────────────────


def select(
    ctx: RouteContext,
    creds: Optional[list[Credential]] = None,
    rules: Optional[list[Rule]] = None,
    now: Optional[int] = None,
) -> RouteDecision:
    """Run the 3-layer chain. Raises ``NoRoute`` / ``AmbiguousRoute``.

    ``creds`` and ``rules`` are injectable for testing; callers in
    production pass None and let the router discover.
    """
    if creds is None:
        creds = discover_all()
    if rules is None:
        rules = load_rules()
    now = now if now is not None else int(time.time())

    # Layer 1: explicit tag
    if ctx.explicit_tag:
        matched = [c for c in creds if c.id == ctx.explicit_tag]
        if not matched:
            raise NoRoute(
                f"explicit credential tag {ctx.explicit_tag!r} is not known "
                f"(no provider has discovered it)"
            )
        if len(matched) > 1:
            sources = ", ".join(m.source for m in matched)
            raise AmbiguousRoute(
                f"explicit tag {ctx.explicit_tag!r} matches {len(matched)} "
                f"credentials ({sources})"
            )
        return RouteDecision(matched[0], f"explicit tag {ctx.explicit_tag!r}", "explicit")

    # Layer 2: route rule (first match wins — rules are ordered)
    by_id = {c.id: c for c in creds}
    for rule in rules:
        if not _rule_matches(rule, ctx):
            continue
        cred = by_id.get(rule.cred_id)
        if cred is None:
            raise NoRoute(
                f"routes.toml rule points at {rule.cred_id!r} but no provider "
                f"has discovered that id"
            )
        return RouteDecision(
            cred,
            f"rule matched (callers={rule.callers or '*'}, dests={rule.destinations or '*'})",
            "rule",
        )

    # Layer 3: platform default — match destination_host to a platform via scope_hosts.
    host_matches = [
        c for c in creds
        if any(_host_matches(ctx.destination_host, h) for h in c.scope_hosts)
    ]
    if not host_matches:
        raise NoRoute(
            f"no credential scope covers host {ctx.destination_host!r} "
            f"(tried {len(creds)} discovered credentials)"
        )

    # Prefer healthy creds (non-expired OAuth / API keys) before falling back.
    healthy = [c for c in host_matches if not c.is_stale(now)]
    candidates = healthy or host_matches

    if len(candidates) > 1:
        ids = ", ".join(sorted(c.id for c in candidates))
        raise AmbiguousRoute(
            f"{len(candidates)} credentials match host {ctx.destination_host!r}: {ids}. "
            f"Disambiguate with an explicit tag or add a rule to routes.toml."
        )

    chosen = candidates[0]
    stale_tag = " (stale — no healthy alternative)" if chosen.is_stale(now) else ""
    return RouteDecision(
        chosen,
        f"platform default for {ctx.destination_host}{stale_tag}",
        "platform-default",
    )


def _host_matches(request_host: str, scope_host: str) -> bool:
    """Exact match, or suffix match for ``.example.com`` scope entries."""
    if scope_host == request_host:
        return True
    if scope_host.startswith(".") and request_host.endswith(scope_host):
        return True
    return False
