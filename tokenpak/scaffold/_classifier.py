# SPDX-License-Identifier: Apache-2.0
"""Decision tree mapping (family, auth) → renderer.

The MVP implements one renderer for the high-confidence Pattern A
case (OpenAI-Chat-compatible + Bearer-auth, with optional extra
headers — covers Mistral / Groq / Together / DeepSeek / Cohere /
OpenRouter-style providers).

For other family / auth combinations, the classifier raises a
:class:`ScaffoldError` with a clear message pointing at the spec
and the reference implementation. Future MVP slices can fill in
those branches incrementally without changing the classifier's
contract.
"""

from __future__ import annotations

from ._config import ScaffoldError, ScaffoldParams

# Reference implementations the maintainer should consult when their
# (family, auth) combination isn't yet templated.
REFERENCE_PRS = {
    ("openai-chat", "bearer"): (
        "Pattern A — OpenAI-Chat-compatible + Bearer auth. "
        "Reference: tokenpak-mistral, tokenpak-groq, tokenpak-together, "
        "tokenpak-deepseek, tokenpak-cohere, tokenpak-openrouter. "
        "PR #27 + #34 + #36."
    ),
    ("openai-responses", "oauth-token-file"): (
        "Pattern B — OpenAI-Responses + file-OAuth (Codex). "
        "Reference: tokenpak-openai-codex. PR #27."
    ),
    ("anthropic-messages", "oauth-token-file"): (
        "Pattern C — Anthropic Messages + file-OAuth + merge_headers. "
        "Reference: tokenpak-claude-code. PR #27."
    ),
    ("azure-openai-wrapper", "api-key-header"): (
        "Pattern E (api-key) — Azure OpenAI deployment-routed. "
        "Reference: tokenpak-azure-openai. PR #32."
    ),
    ("bedrock-wrapper", "sigv4"): (
        "Pattern E (SigV4) — AWS Bedrock for Claude. "
        "Reference: tokenpak-bedrock-claude. PR #33."
    ),
    ("vertex-wrapper", "oauth-adc"): (
        "Pattern E (OAuth-ADC) — Vertex AI Gemini. "
        "Reference: tokenpak-vertex-gemini. PR #34."
    ),
    ("custom", "custom"): (
        "Pattern F — provider-native custom envelope. NEW FormatAdapter "
        "required. No reference implementation yet (placeholder for "
        "Cohere v1 / IBM watsonx / Replicate)."
    ),
    ("cli-bridge", "custom"): (
        "Pattern G — CLI/bridge adapter (Codex subprocess). Reference: "
        "preserved on branch feat/codex-companion-bridge (closed PR #26)."
    ),
}


def renderer_name(params: ScaffoldParams) -> str:
    """Pick the renderer for this (family, auth) combination.

    Returns the renderer key that :mod:`._templates` dispatches on.
    Raises :class:`ScaffoldError` with a spec-pointing message when
    the combination isn't yet templatized in the MVP.
    """
    key = (params.family, params.auth)

    if key == ("openai-chat", "bearer"):
        return "openai_chat_bearer"
    if key == ("openai-chat", "api-key-header"):
        return "openai_chat_apikey"

    # Combination is recognised but not implemented in MVP:
    if key in REFERENCE_PRS:
        raise ScaffoldError(
            f"Scaffold MVP does not yet templatize the "
            f"(family={params.family!r}, auth={params.auth!r}) combination.\n"
            f"\n"
            f"Reference: {REFERENCE_PRS[key]}\n"
            f"\n"
            f"Until this branch is implemented, copy the reference "
            f"provider class manually and adapt — the spec at "
            f"docs/internal/specs/phase4-adapter-scaffold-spec-2026-04-25.md "
            f"§6 covers what each pattern's template should generate."
        )

    raise ScaffoldError(
        f"Unknown (family={params.family!r}, auth={params.auth!r}) "
        f"combination. The classifier's decision tree only covers the "
        f"family+auth pairs documented in spec §6 and listed in "
        f"REFERENCE_PRS. If your provider doesn't fit any pattern, "
        f"use --family custom --auth custom (then human review required)."
    )
