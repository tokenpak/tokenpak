# SPDX-License-Identifier: Apache-2.0
"""Recall guidance stays dense and consistent across companion surfaces."""

from tokenpak.companion.codex.agents_md import _AGENTS_CONTENT
from tokenpak.companion.launcher import _SYSTEM_PROMPT


def test_guidance_requires_retrieval_and_single_store_persistence():
    for guidance in (_AGENTS_CONTENT, _SYSTEM_PROMPT):
        normalized = " ".join(guidance.split())
        assert "For prior work, retrieve before answering." in normalized
        assert "native memory" in normalized
        assert "batch" in normalized
        assert "Persist each fact once" in normalized
