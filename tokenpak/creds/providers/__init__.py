# SPDX-License-Identifier: Apache-2.0
"""Built-in credential providers and the discovery driver.

Adding a new provider is one function: define ``discover()`` that
returns ``list[Credential]`` and add the module to :data:`BUILTIN_PROVIDERS`.
No registry enumeration in calling code — the driver walks this list
at runtime so discovery stays dynamic per ``feedback_always_dynamic.md``.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..model import Credential

log = logging.getLogger(__name__)


# Each entry is (provider_name, discover_callable). Callables may raise;
# the driver catches and logs rather than aborting the whole scan.
BUILTIN_PROVIDERS: list[tuple[str, Callable[[], list[Credential]]]] = []


def _register() -> None:
    """Lazy-register built-ins. Lets each provider module import cleanly
    without side-effects on import order."""
    from . import claude_cli, codex_cli, env_pool, openclaw, user_config

    BUILTIN_PROVIDERS.clear()
    BUILTIN_PROVIDERS.extend(
        [
            ("codex-cli", codex_cli.discover),
            ("claude-cli", claude_cli.discover),
            ("env-pool", env_pool.discover),
            ("user-config", user_config.discover),
            ("openclaw", openclaw.discover),
        ]
    )


def discover_all() -> list[Credential]:
    """Run every provider, concatenate results, stable-sort for display."""
    if not BUILTIN_PROVIDERS:
        _register()

    creds: list[Credential] = []
    for name, fn in BUILTIN_PROVIDERS:
        try:
            found = fn() or []
        except Exception as exc:  # keep one broken provider from killing discovery
            log.warning("credential provider %s failed: %s", name, exc)
            continue
        creds.extend(found)

    creds.sort(key=lambda c: (c.platform, c.provider, c.id))
    return creds


def resolve_secret(cred: Credential) -> "str | None":
    """Fetch the actual secret string for ``cred``.

    Dispatches on ``cred.provider`` — each built-in provider module
    exposes a module-level ``resolve(cred)`` function. Returns None
    when the secret can't be read (file missing, env var unset, etc.)
    rather than raising, so callers can report "credential not
    resolvable" without a traceback in their logs.
    """
    from . import claude_cli, codex_cli, env_pool, openclaw, user_config

    resolvers = {
        codex_cli.PROVIDER_NAME: codex_cli.resolve,
        claude_cli.PROVIDER_NAME: claude_cli.resolve,
        env_pool.PROVIDER_NAME: env_pool.resolve,
        user_config.PROVIDER_NAME: user_config.resolve,
        openclaw.PROVIDER_NAME: openclaw.resolve,
    }
    resolver = resolvers.get(cred.provider)
    if resolver is None:
        return None
    try:
        return resolver(cred)
    except Exception as exc:
        log.warning("resolve failed for %s (%s): %s", cred.id, cred.provider, exc)
        return None
