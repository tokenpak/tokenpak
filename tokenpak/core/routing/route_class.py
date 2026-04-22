"""``RouteClass`` — the canonical taxonomy for classifying LLM requests.

A ``RouteClass`` is assigned exactly once per request, at the top of the
pipeline, by :class:`tokenpak.services.routing_service.classifier.RouteClassifier`.
Every downstream component reads the class from ``PipelineContext.route_class``
and branches on it (via the :class:`Policy` they resolve) rather than
re-deriving "is this Claude Code?" from headers or URLs.

The class names encode two axes:

1. **Client identity** — which binary/SDK sent the request
   (``claude_code``, ``anthropic_sdk``, ``openai_sdk``, ``generic``).
2. **Consumption mode** (Claude Code only, for now) — how the user is
   driving the client (``tui`` interactive, ``cli`` scripted, ``sdk``
   programmatic, ``cron`` automated).

Unknown or unidentifiable traffic lands on :data:`RouteClass.GENERIC`,
which has the most conservative Policy (no byte_preserve promises, no
injection, DLP warn). The classifier never raises; it only assigns.
"""

from __future__ import annotations

from enum import Enum


class RouteClass(str, Enum):
    """Canonical taxonomy of request sources.

    Inherits from ``str`` so it's naturally JSON-serialisable + readable
    in telemetry rows and YAML preset filenames.
    """

    # Claude Code consumption modes. All share byte_preserve body_handling
    # (OAuth billing constraint) but differ on injection budget + TUI
    # display hooks.
    CLAUDE_CODE_TUI = "claude-code-tui"
    CLAUDE_CODE_CLI = "claude-code-cli"
    CLAUDE_CODE_TMUX = "claude-code-tmux"
    CLAUDE_CODE_SDK = "claude-code-sdk"
    CLAUDE_CODE_IDE = "claude-code-ide"
    CLAUDE_CODE_CRON = "claude-code-cron"

    # Direct SDK consumers — full pipeline eligibility.
    ANTHROPIC_SDK = "anthropic-sdk"
    OPENAI_SDK = "openai-sdk"

    # Catch-all. Conservative defaults.
    GENERIC = "generic"

    @property
    def is_claude_code(self) -> bool:
        """True when this class belongs to the Claude Code family.

        Centralised so no other subsystem re-implements the check. Every
        site that used to do ``"claude-code" in target_url`` asks this
        question instead.
        """
        return self.value.startswith("claude-code-")


__all__ = ["RouteClass"]
