# SPDX-License-Identifier: Apache-2.0
"""``tokenpak adapter scaffold`` — codegen for new provider integrations.

Implements the MVP slice of the Phase 4 spec
(``docs/internal/specs/phase4-adapter-scaffold-spec-2026-04-25.md``):

  - CLI command + 11 flags (parsed in :mod:`tokenpak.cli._impl`).
  - Deterministic templates (NO LLM codegen). The MVP fully supports
    the OpenAI-Chat-compatible + Bearer-auth combination (Pattern A
    in Standard #23 §2.4) — covers Mistral / Groq / Together /
    DeepSeek / Cohere / OpenRouter-style providers. Other family /
    auth combinations emit a clear "MVP does not yet templatize this
    pattern" message pointing at the reference implementation.
  - Generated artifacts: CredentialProvider class, offline contract
    test file, fixture JSONs, docs stub, paste-ready follow-up issue
    text.
  - Five non-negotiable guardrails enforced before any write
    (:mod:`._guardrails`).

Entry point for programmatic callers: :func:`scaffold`.
"""

from __future__ import annotations

from ._config import ScaffoldError, ScaffoldParams, parse_optional_dep_list
from ._generator import GeneratedArtifact, generate_artifacts
from ._guardrails import GuardrailViolation, check_artifacts
from ._register import RegisterError, apply_register_patch
from ._writer import WriteResult, write_artifacts


def scaffold(params: ScaffoldParams) -> WriteResult:
    """Run the full scaffold pipeline: classify → generate → guardrail-check → write.

    The high-level orchestration. CLI calls this once after parsing
    flags. Programmatic callers can build their own
    :class:`ScaffoldParams` and invoke this directly for tests / dry
    runs / preview.

    On guardrail failure, raises :class:`GuardrailViolation` BEFORE
    any file is touched.
    """
    artifacts = generate_artifacts(params)
    check_artifacts(artifacts)
    return write_artifacts(artifacts, dry_run=params.dry_run)


__all__ = [
    "GeneratedArtifact",
    "GuardrailViolation",
    "RegisterError",
    "ScaffoldError",
    "ScaffoldParams",
    "WriteResult",
    "apply_register_patch",
    "check_artifacts",
    "generate_artifacts",
    "parse_optional_dep_list",
    "scaffold",
    "write_artifacts",
]
