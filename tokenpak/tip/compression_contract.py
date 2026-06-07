# SPDX-License-Identifier: Apache-2.0
"""TIP compression contract — per-request compression policy and protected span vocabulary.

``CompressionPolicy`` describes how the optimization pipeline may compress
request content. ``ProtectedSpanType`` provides a canonical vocabulary of
content fragment types that must not be altered regardless of compression
level — these are the "non-negotiable fidelity" anchors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CompressionPolicy:
    """Per-request compression behavior contract.

    Recipe selection and fidelity constraints are expressed here so the
    proxy stage can apply route-appropriate compression without knowing
    adapter specifics.

    Fields:
    - ``recipe_ids``: ordered list of compression recipe identifiers to
      apply (e.g. ``["cp-git-diff-compression"]``). Empty = no compression.
    - ``target_ratio``: desired compression ratio (0.0–1.0). ``None`` means
      "apply recipe defaults".
    - ``protected_span_types``: content categories that MUST be preserved
      verbatim. Populated from ``ProtectedSpanType`` constants.
    - ``preserve_exact_blocks``: when True, code fences (``` blocks) and
      quoted exact outputs are treated as lossless zones.
    - ``bypass_reason``: if set, compression is skipped with this reason.
    """

    enabled: bool = True
    recipe_ids: List[str] = field(default_factory=list)
    target_ratio: Optional[float] = None
    protected_span_types: List[str] = field(default_factory=list)
    preserve_exact_blocks: bool = True
    bypass_reason: Optional[str] = None

    def is_active(self) -> bool:
        """True when compression is enabled with at least one recipe and not bypassed."""
        return self.enabled and bool(self.recipe_ids) and self.bypass_reason is None

    def with_bypass(self, reason: str) -> "CompressionPolicy":
        """Return a copy of this policy with compression bypassed for *reason*."""
        from dataclasses import replace
        return replace(self, bypass_reason=reason)


class ProtectedSpanType:
    """Canonical vocabulary of content fragment types that must survive compression.

    A compression stage MUST NOT alter text spans classified as any of
    these types when the active ``FidelityPolicy`` is ``LOSSLESS_REQUIRED``
    or ``SEMANTIC_SAFE``.

    Span detection logic belongs in proxy/optimization/protected_spans.py
    (Component D). This module only defines the canonical names.
    """

    FILE_PATH = "file_path"
    FUNCTION_SIGNATURE = "function_signature"
    CLASS_SIGNATURE = "class_signature"
    COMMAND = "command"
    EXIT_CODE = "exit_code"
    STACK_TRACE_FRAME = "stack_trace_frame"
    EXCEPTION_MESSAGE = "exception_message"
    JSON_SCHEMA = "json_schema"
    YAML_KEY = "yaml_key"
    CONFIG_VALUE = "config_value"
    LINE_NUMBER = "line_number"
    DIFF_HUNK_HEADER = "diff_hunk_header"
    DIFF_ADDED_REMOVED_LINES = "diff_added_removed_lines"
    URL = "url"
    CREDENTIAL_PLACEHOLDER = "credential_placeholder"

    ALL: frozenset[str] = frozenset({
        FILE_PATH,
        FUNCTION_SIGNATURE,
        CLASS_SIGNATURE,
        COMMAND,
        EXIT_CODE,
        STACK_TRACE_FRAME,
        EXCEPTION_MESSAGE,
        JSON_SCHEMA,
        YAML_KEY,
        CONFIG_VALUE,
        LINE_NUMBER,
        DIFF_HUNK_HEADER,
        DIFF_ADDED_REMOVED_LINES,
        URL,
        CREDENTIAL_PLACEHOLDER,
    })

    # Subset that is always protected regardless of fidelity policy
    ALWAYS_PROTECTED: frozenset[str] = frozenset({
        CREDENTIAL_PLACEHOLDER,
        JSON_SCHEMA,
    })


__all__ = ["CompressionPolicy", "ProtectedSpanType"]
