"""Feature flag and bypass policies for the optimization pipeline.

The pipeline is gated on ``TOKENPAK_OPTIMIZATION_PIPELINE``. Three values:

    unset / "0" / "off"     — pipeline disabled (default; safe)
    "observe" / "1" / "on"  — pipeline runs in observe-only mode
    "apply"                 — reserved for a future mutate mode; treated as observe here

Observe-only is the only mode this module supports. Anything stronger MUST
be gated by an additional flag in the stage that wants to mutate.
"""

from __future__ import annotations

import os
from typing import Tuple

ENV_FLAG = "TOKENPAK_OPTIMIZATION_PIPELINE"

MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_APPLY = "apply"

_TRUTHY_OBSERVE = {"1", "on", "observe", "true", "yes"}
_TRUTHY_APPLY = {"apply"}


def read_mode(env: dict | None = None) -> str:
    """Read the pipeline mode from env (or a passed dict, for tests)."""
    source = env if env is not None else os.environ
    raw = source.get(ENV_FLAG, "")
    val = (raw or "").strip().lower()
    if val in _TRUTHY_OBSERVE:
        return MODE_OBSERVE
    if val in _TRUTHY_APPLY:
        # Defensive: even if a future deployer sets "apply", THIS module
        # downgrades to observe. Stages that want to mutate must check
        # their own flag in addition.
        return MODE_OBSERVE
    return MODE_OFF


def is_pipeline_enabled(env: dict | None = None) -> bool:
    """True when the pipeline should run (in observe-only mode)."""
    return read_mode(env) != MODE_OFF


def bypass_reasons(
    *,
    method: str,
    path: str,
    body: bytes,
) -> Tuple[bool, str]:
    """Preflight bypass check.

    Returns (bypass, reason). When bypass=True the pipeline should not run
    against this request at all. Used by integration sites that want to
    short-circuit before building the context.

    Conservative defaults — any signal that the request is not a normal
    inference call routes it around the pipeline.
    """
    if method.upper() != "POST":
        return True, "method-not-post"
    if not body:
        return True, "empty-body"
    # Reserve known control-plane / health paths
    lowered = path.lower()
    for marker in ("/health", "/stats", "/ready", "/version", "/metrics"):
        if marker in lowered:
            return True, f"control-path:{marker.lstrip('/')}"
    return False, ""
