# SPDX-License-Identifier: Apache-2.0
"""Feature-flagged credential injection using the creds router.

Default **off**. Flip ``TOKENPAK_CREDS_ROUTER_ENABLED=1`` to route
request credentials through :mod:`tokenpak.creds.router` + per-provider
``resolve_secret`` instead of the hardcoded Codex-auth-file path.

Design goals:

* **Fail-open.** Any router error (no route, ambiguous, secret
  unresolvable, exception) falls back to the caller's existing flow.
  The proxy must never drop a working request because of this module.
* **No cache.** Providers' ``resolve()`` functions already read fresh
  from their owning tool's auth file. Caching here would re-introduce
  the staleness class of bug we built the router to surface.
* **Explicit inputs.** Destination host + caller identity come from the
  request; callers can add ``X-Tokenpak-Credential`` for explicit-tag
  routing or ``X-Tokenpak-Caller`` to identify themselves to rules.

Return contract: ``maybe_inject()`` returns True iff the caller should
**skip** the old hardcoded Codex injection — i.e. we've injected a
router-chosen credential and the caller is done. Otherwise the caller
continues with its existing logic unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping, Optional
from urllib.parse import urlparse


log = logging.getLogger(__name__)

_ENABLED_ENV = "TOKENPAK_CREDS_ROUTER_ENABLED"

# Client-side hints the proxy honours. Both are optional; absence means
# "let the router use its default chain".
_HEADER_EXPLICIT_TAG = "X-Tokenpak-Credential"
_HEADER_CALLER = "X-Tokenpak-Caller"


def enabled() -> bool:
    """True if the router path should run at all.

    Read each call so the flag is flippable at runtime by an operator
    toggling the env var in the service unit and restarting."""
    return os.environ.get(_ENABLED_ENV, "").lower() in ("1", "true", "yes")


def _get_header_ci(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive lookup into an arbitrary headers mapping."""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return None


def maybe_inject(
    fwd_headers: dict[str, str],
    target_url: str,
    client_headers: Mapping[str, str],
) -> bool:
    """Inject a router-chosen credential into ``fwd_headers``.

    Returns True only on full success. The caller should then skip
    any hardcoded auth-injection it would otherwise do. On any
    failure, returns False and leaves ``fwd_headers`` untouched —
    the caller continues with its existing behavior.
    """
    if not enabled():
        return False

    try:
        from tokenpak.creds.router import (
            AmbiguousRoute,
            NoRoute,
            RouteContext,
            select,
        )
        from tokenpak.creds.providers import resolve_secret
    except Exception as exc:
        # Import-time failure = bug in the creds subsystem; fail-open.
        log.warning("creds router unavailable, falling back to passthrough: %s", exc)
        return False

    try:
        dest_host = urlparse(target_url).netloc or ""
        if not dest_host:
            return False
        ctx = RouteContext(
            destination_host=dest_host,
            caller_identity=_get_header_ci(client_headers, _HEADER_CALLER),
            explicit_tag=_get_header_ci(client_headers, _HEADER_EXPLICIT_TAG),
        )
    except Exception as exc:  # defensive: any failure prepping context → passthrough
        log.warning("creds router context prep failed, passthrough: %s", exc)
        return False

    try:
        decision = select(ctx)
    except (NoRoute, AmbiguousRoute) as exc:
        # These are "user said fail-loud" at the router layer, but at
        # the proxy layer a router miss just means "I don't know —
        # use whatever the caller sent". Log so the operator can see
        # the router isn't covering this request yet.
        log.info("creds router declined (%s): %s", exc.tag, exc)
        return False
    except Exception as exc:
        log.warning("creds router raised, falling back to passthrough: %s", exc)
        return False

    secret = resolve_secret(decision.credential)
    if not secret:
        log.info(
            "creds router chose %s but secret couldn't be resolved; passthrough",
            decision.credential.id,
        )
        return False

    _inject_secret(fwd_headers, decision.credential.platform, secret, decision.credential.kind)

    log.info(
        "creds router → %s (platform=%s layer=%s reason=%s)",
        decision.credential.id,
        decision.credential.platform,
        decision.layer,
        decision.reason,
    )
    return True


def _inject_secret(
    fwd_headers: dict[str, str],
    platform: str,
    secret: str,
    kind: str,
) -> None:
    """Write the right header shape for the platform.

    Anthropic uses ``x-api-key`` for API keys; OAuth subscription tokens
    go in ``Authorization: Bearer``. Every other platform we know about
    uses ``Authorization: Bearer`` for both kinds.
    """
    if platform == "anthropic":
        if kind == "oauth":
            fwd_headers["Authorization"] = f"Bearer {secret}"
            _strip(fwd_headers, "x-api-key")
        else:
            fwd_headers["x-api-key"] = secret
            _strip(fwd_headers, "Authorization")
    elif platform == "openai":
        fwd_headers["Authorization"] = f"Bearer {secret}"
        _strip(fwd_headers, "x-api-key")
    elif platform in ("google",):
        # Google Gemini accepts an API key either as a query param
        # (``?key=``) or via ``Authorization: Bearer``. Bearer is
        # forward-compatible, so that's what we emit.
        fwd_headers["Authorization"] = f"Bearer {secret}"
        _strip(fwd_headers, "x-api-key")
    elif platform in ("xai", "grok"):
        fwd_headers["Authorization"] = f"Bearer {secret}"
        _strip(fwd_headers, "x-api-key")
    else:
        # Unknown platform: conservative default. Emit Bearer; leave
        # whatever else the caller sent alone so we don't strip useful
        # context for a provider we haven't modelled.
        fwd_headers["Authorization"] = f"Bearer {secret}"


def _strip(headers: dict[str, str], name: str) -> None:
    """Remove a header in any case variant."""
    target = name.lower()
    for key in [k for k in headers if k.lower() == target]:
        headers.pop(key, None)
