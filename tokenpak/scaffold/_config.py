# SPDX-License-Identifier: Apache-2.0
"""Scaffold input parameters + validation.

The :class:`ScaffoldParams` dataclass is the single source of truth
for what the generator + writer + guardrails see. CLI parsing builds
one of these; programmatic callers can build one directly.

Validation enforces Standard #23 §1 (slug shape, capability label
form) at the input boundary so the generator never has to defend
against malformed inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Standard #23 §1.1 — provider slug regex.
SLUG_RE = re.compile(r"^tokenpak-[a-z0-9]+(-[a-z0-9]+)*$")

# All families MVP can classify (decision tree from spec §6 + §6.8).
KNOWN_FAMILIES = frozenset({
    "openai-chat",
    "openai-responses",
    "anthropic-messages",
    "gemini",
    "azure-openai-wrapper",
    "bedrock-wrapper",
    "vertex-wrapper",
    "cli-bridge",
    "custom",
})

# All auth schemes MVP can classify (spec §3 + §6).
KNOWN_AUTHS = frozenset({
    "bearer",
    "api-key-header",
    "sigv4",
    "oauth-adc",
    "oauth-token-file",
    "custom",
})

KNOWN_STREAMING = frozenset({"same-url", "verb-suffix", "path-suffix", "none"})


class ScaffoldError(Exception):
    """Raised on invalid input. Caller catches + emits a CLI-friendly message."""


@dataclass
class ScaffoldParams:
    """All inputs the scaffold tool needs to produce its artifacts.

    Field shape mirrors the spec §3 input table. Validation happens
    in :meth:`validate` — call it after construction (the CLI does
    this; programmatic callers should too).
    """

    docs_url: str
    """Provider docs URL — required, used as docstring metadata.

    MVP does NOT fetch / parse this URL. It's recorded in the
    generated docstring + docs stub so reviewers can trace
    provenance. Future versions may use it for inference (spec §3).
    """

    slug: str
    """Provider slug per Standard #23 §1.1: ``tokenpak-<vendor>[-<product-or-family>]``."""

    family: str
    """Wire format family. One of :data:`KNOWN_FAMILIES`."""

    auth: str
    """Auth scheme. One of :data:`KNOWN_AUTHS`."""

    endpoint: str
    """Upstream URL (or URL template for body-aware routing)."""

    streaming: str = "same-url"
    """How streaming is signalled to the upstream. One of :data:`KNOWN_STREAMING`."""

    optional_deps: List[str] = field(default_factory=list)
    """Optional Python packages required (e.g. ``["boto3"]``). Empty list = no optional deps."""

    out_dir: Optional[Path] = None
    """Override the canonical output location.

    When None (default), artifacts go to their canonical homes:
    credential provider into
    ``tokenpak/services/routing_service/credential_injector.py``,
    test file into ``tests/``, fixtures under ``tests/fixtures/``,
    docs into ``docs/integrations/``. When set, all artifacts go
    under this directory as standalone files.
    """

    dry_run: bool = False
    """If True, generate + check + print but write nothing."""

    non_interactive: bool = False
    """If True, fail on any ambiguity instead of prompting.

    MVP doesn't currently prompt (all required fields must be
    provided as flags), so this flag is effectively a no-op for now.
    Reserved for v2 inference path.
    """

    live_verified: bool = False
    """Default per Standard #23 §6.4: scaffolded providers are
    contract-tested offline only until verified live.
    """

    extra_headers: dict = field(default_factory=dict)
    """Vendor-required extra headers (e.g. OpenRouter's
    ``HTTP-Referer`` + ``X-Title``). MVP supports passing these via
    ``--extra-header KEY=VALUE`` (repeatable).
    """

    def validate(self) -> None:
        """Raise :class:`ScaffoldError` on invalid input.

        Guards Standard #23 §1.1 slug rules + family/auth/streaming
        membership. Caller should handle the error and emit a
        user-friendly CLI message.
        """
        if not self.docs_url or not self.docs_url.strip():
            raise ScaffoldError("--from-docs URL is required")
        if not self.docs_url.startswith(("http://", "https://")):
            raise ScaffoldError(
                f"--from-docs must be an http(s) URL; got {self.docs_url!r}"
            )

        if not self.slug:
            raise ScaffoldError("--slug is required")
        if not SLUG_RE.match(self.slug):
            raise ScaffoldError(
                f"--slug must match {SLUG_RE.pattern!r} per Standard #23 §1.1; "
                f"got {self.slug!r}. Examples: tokenpak-mistral, tokenpak-azure-openai, "
                f"tokenpak-bedrock-claude."
            )

        if self.family not in KNOWN_FAMILIES:
            raise ScaffoldError(
                f"--family must be one of {sorted(KNOWN_FAMILIES)}; got {self.family!r}"
            )

        if self.auth not in KNOWN_AUTHS:
            raise ScaffoldError(
                f"--auth must be one of {sorted(KNOWN_AUTHS)}; got {self.auth!r}"
            )

        if self.streaming not in KNOWN_STREAMING:
            raise ScaffoldError(
                f"--streaming must be one of {sorted(KNOWN_STREAMING)}; "
                f"got {self.streaming!r}"
            )

        if not self.endpoint or not self.endpoint.strip():
            raise ScaffoldError("--endpoint is required")
        # Endpoint can be a static URL or a template like
        # ``https://{region}-host.example.com/...``. We don't enforce
        # http(s):// here because some templates use placeholders
        # before scheme-aware substitution.
        if not (
            self.endpoint.startswith(("http://", "https://"))
            or "{" in self.endpoint  # template placeholder
        ):
            raise ScaffoldError(
                f"--endpoint must be an http(s) URL or a URL template "
                f"with {{...}} placeholders; got {self.endpoint!r}"
            )

    @property
    def vendor(self) -> str:
        """Slug minus the ``tokenpak-`` prefix.

        ``tokenpak-mistral`` → ``mistral``;
        ``tokenpak-azure-openai`` → ``azure-openai``.
        """
        return self.slug[len("tokenpak-") :]

    @property
    def class_basename(self) -> str:
        """CamelCase form of the vendor for class naming.

        ``mistral`` → ``Mistral``;
        ``azure-openai`` → ``AzureOpenAI``;
        ``bedrock-claude`` → ``BedrockClaude``.
        """
        return "".join(part.capitalize() for part in self.vendor.split("-"))

    @property
    def env_var(self) -> str:
        """Convention: env var is ``<VENDOR>_API_KEY`` (uppercased, hyphens → underscores).

        ``mistral`` → ``MISTRAL_API_KEY``;
        ``azure-openai`` → ``AZURE_OPENAI_API_KEY``;
        ``deepseek`` → ``DEEPSEEK_API_KEY``.
        """
        return self.vendor.upper().replace("-", "_") + "_API_KEY"


def parse_optional_dep_list(raw: Optional[str]) -> List[str]:
    """Parse the ``--optional-dep`` comma-separated list into a clean list.

    Accepts ``None`` or ``""`` (returns empty list). Strips whitespace.
    """
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_extra_header(raw: str) -> tuple[str, str]:
    """Parse a single ``--extra-header KEY=VALUE`` argument into a tuple.

    Validates the ``KEY=VALUE`` shape; raises :class:`ScaffoldError`
    on malformed input.
    """
    if "=" not in raw:
        raise ScaffoldError(
            f"--extra-header must be KEY=VALUE; got {raw!r}"
        )
    key, _, value = raw.partition("=")
    key = key.strip()
    value = value.strip()
    if not key or not value:
        raise ScaffoldError(
            f"--extra-header KEY and VALUE both required; got {raw!r}"
        )
    return key, value
