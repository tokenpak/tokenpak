# SPDX-License-Identifier: Apache-2.0
"""Recall guidance stays dense and consistent across companion surfaces."""

from tokenpak.companion.codex.agents_md import _AGENTS_CONTENT
from tokenpak.companion.launcher import _SYSTEM_PROMPT


def test_guidance_is_fact_directed_and_single_store_persistence():
    for guidance in (_AGENTS_CONTENT, _SYSTEM_PROMPT):
        normalized = " ".join(guidance.split())
        assert "current source" in normalized
        assert "current context lacks" in normalized
        assert "native memory" in normalized
        assert "Handoff Pak" in normalized
        assert "Persist each fact once" in normalized
        assert "For prior work, retrieve before answering." not in normalized
