# SPDX-License-Identifier: Apache-2.0
"""Orchestrate template rendering into a list of artifacts to write.

Single entry point: :func:`generate_artifacts`. Takes
:class:`ScaffoldParams`, picks the renderer via the classifier,
renders each artifact, returns the list. The list flows through
:mod:`._guardrails` (checked) then :mod:`._writer` (written).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from . import _classifier, _templates
from ._config import ScaffoldParams


@dataclass
class GeneratedArtifact:
    """One file the scaffold tool will write (or print in dry-run).

    ``relative_path`` is relative to the repo root for canonical
    layouts, OR relative to ``ScaffoldParams.out_dir`` when that's
    set.

    ``kind`` is a coarse tag for diagnostics + dry-run output
    formatting (provider class / test / fixture / docs / instructions).
    """

    relative_path: str
    content: str
    kind: str  # "provider-class" | "test" | "fixture" | "docs" | "instructions"


def generate_artifacts(params: ScaffoldParams) -> List[GeneratedArtifact]:
    """Render all artifacts for ``params``. Does not write anything."""
    params.validate()
    renderer = _classifier.renderer_name(params)
    if renderer == "openai_chat_bearer":
        return _generate_openai_chat_bearer(params)
    if renderer == "openai_chat_bearer_passthrough":
        return _generate_openai_chat_bearer_passthrough(params)
    if renderer == "openai_chat_apikey":
        return _generate_openai_chat_apikey(params)
    if renderer == "anthropic_messages_apikey":
        return _generate_anthropic_messages_apikey(params)
    # Classifier raises on unknown combinations; this is unreachable
    # but kept for defensive completeness.
    raise NotImplementedError(  # pragma: no cover
        f"renderer {renderer!r} not yet implemented in MVP"
    )


def _generate_openai_chat_bearer_passthrough(
    params: ScaffoldParams,
) -> List[GeneratedArtifact]:
    """Generate artifacts for Pattern A-passthrough.

    Same artifact set as Pattern A; provider-class + test-file
    renderers are passthrough-aware. Fixtures + docs stub are reused
    from Pattern A (the wire shape is identical OpenAI-Chat).
    """
    out: List[GeneratedArtifact] = []
    vendor_safe = params.vendor.replace("-", "_")

    out.append(
        GeneratedArtifact(
            relative_path=_provider_class_path(params),
            content=_templates.render_openai_chat_bearer_passthrough_provider_class(
                params
            ),
            kind="provider-class",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_test_path(params, vendor_safe),
            content=_templates.render_openai_chat_bearer_passthrough_test_file(
                params
            ),
            kind="test",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_fixture_path(params, "request.json"),
            content=_templates.render_request_fixture(params),
            kind="fixture",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_fixture_path(params, "response.json"),
            content=_templates.render_response_fixture(params),
            kind="fixture",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_docs_stub_path(params),
            content=_templates.render_docs_stub(params),
            kind="docs",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path="<stdout: follow-up issue>",
            content=_templates.render_followup_issue_text(params),
            kind="instructions",
        )
    )
    return out


def _generate_anthropic_messages_apikey(
    params: ScaffoldParams,
) -> List[GeneratedArtifact]:
    """Generate artifacts for Pattern A-anthropic.

    Anthropic Messages has a materially different request/response
    shape from OpenAI Chat, so fixtures + docs stub are
    Anthropic-specific (separate renderers in :mod:`._templates`).
    """
    out: List[GeneratedArtifact] = []
    vendor_safe = params.vendor.replace("-", "_")

    out.append(
        GeneratedArtifact(
            relative_path=_provider_class_path(params),
            content=_templates.render_anthropic_messages_apikey_provider_class(
                params
            ),
            kind="provider-class",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_test_path(params, vendor_safe),
            content=_templates.render_anthropic_messages_apikey_test_file(params),
            kind="test",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_fixture_path(params, "request.json"),
            content=_templates.render_anthropic_messages_request_fixture(params),
            kind="fixture",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_fixture_path(params, "response.json"),
            content=_templates.render_anthropic_messages_response_fixture(params),
            kind="fixture",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_docs_stub_path(params),
            content=_templates.render_anthropic_messages_docs_stub(params),
            kind="docs",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path="<stdout: follow-up issue>",
            content=_templates.render_followup_issue_text(params),
            kind="instructions",
        )
    )
    return out


def _generate_openai_chat_apikey(params: ScaffoldParams) -> List[GeneratedArtifact]:
    """Generate the artifact set for OpenAI-Chat + api-key-header auth."""
    out: List[GeneratedArtifact] = []
    vendor_safe = params.vendor.replace("-", "_")

    out.append(
        GeneratedArtifact(
            relative_path=_provider_class_path(params),
            content=_templates.render_openai_chat_apikey_provider_class(params),
            kind="provider-class",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_test_path(params, vendor_safe),
            content=_templates.render_openai_chat_apikey_test_file(params),
            kind="test",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_fixture_path(params, "request.json"),
            content=_templates.render_request_fixture(params),
            kind="fixture",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_fixture_path(params, "response.json"),
            content=_templates.render_response_fixture(params),
            kind="fixture",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path=_docs_stub_path(params),
            content=_templates.render_docs_stub(params),
            kind="docs",
        )
    )
    out.append(
        GeneratedArtifact(
            relative_path="<stdout: follow-up issue>",
            content=_templates.render_followup_issue_text(params),
            kind="instructions",
        )
    )
    return out


def _generate_openai_chat_bearer(params: ScaffoldParams) -> List[GeneratedArtifact]:
    """Generate the artifact set for Pattern A (openai-chat + bearer)."""
    out: List[GeneratedArtifact] = []
    vendor_safe = params.vendor.replace("-", "_")

    # 1. Provider class (Python source)
    out.append(
        GeneratedArtifact(
            relative_path=_provider_class_path(params),
            content=_templates.render_openai_chat_bearer_provider_class(params),
            kind="provider-class",
        )
    )

    # 2. Offline contract test file
    out.append(
        GeneratedArtifact(
            relative_path=_test_path(params, vendor_safe),
            content=_templates.render_openai_chat_bearer_test_file(params),
            kind="test",
        )
    )

    # 3. Request fixture
    out.append(
        GeneratedArtifact(
            relative_path=_fixture_path(params, "request.json"),
            content=_templates.render_request_fixture(params),
            kind="fixture",
        )
    )

    # 4. Response fixture
    out.append(
        GeneratedArtifact(
            relative_path=_fixture_path(params, "response.json"),
            content=_templates.render_response_fixture(params),
            kind="fixture",
        )
    )

    # 5. Docs stub
    out.append(
        GeneratedArtifact(
            relative_path=_docs_stub_path(params),
            content=_templates.render_docs_stub(params),
            kind="docs",
        )
    )

    # 6. Follow-up issue text — not a file written to disk; surfaced
    # in the writer's stdout summary. Kept in the artifact list with
    # a relative_path of "<stdout: follow-up issue>" so the writer
    # can dispatch on kind without a special-case path check.
    out.append(
        GeneratedArtifact(
            relative_path="<stdout: follow-up issue>",
            content=_templates.render_followup_issue_text(params),
            kind="instructions",
        )
    )

    return out


# ── Path resolution ──────────────────────────────────────────────────


def _provider_class_path(p: ScaffoldParams) -> str:
    """Where the CredentialProvider class should land.

    MVP behavior: when ``out_dir`` is None, write a STANDALONE file
    under ``tokenpak/services/routing_service/extras/<vendor>.py``.
    The maintainer then adds the import + ``register(...)`` call
    manually to ``credential_injector.py`` (instructions printed by
    the writer).

    This avoids the brittleness of regex-based insertion into
    ``credential_injector.py`` for MVP. v2 can add safe in-place
    insertion once we decide on stable insertion-point markers.

    When ``out_dir`` is set, write the standalone file under that
    directory.
    """
    vendor_safe = p.vendor.replace("-", "_")
    filename = f"{vendor_safe}.py"
    if p.out_dir is not None:
        return str(p.out_dir / filename)
    return f"tokenpak/services/routing_service/extras/{filename}"


def _test_path(p: ScaffoldParams, vendor_safe: str) -> str:
    """Where the offline-contract test file lands.

    Standard #23 §1.3 accepts both
    ``tests/test_phase{N}_{topic}.py`` (grouped pack) and
    ``tests/test_{provider}_{aspect}.py`` (single-provider focus).
    MVP picks the single-provider form for scaffolded providers
    since each scaffold run is for one provider.
    """
    if p.out_dir is not None:
        return str(p.out_dir / f"test_{vendor_safe}_offline.py")
    return f"tests/test_{vendor_safe}_offline.py"


def _fixture_path(p: ScaffoldParams, filename: str) -> str:
    """Fixture JSONs live under ``tests/fixtures/<slug>/``."""
    if p.out_dir is not None:
        return str(p.out_dir / "fixtures" / p.slug / filename)
    return f"tests/fixtures/{p.slug}/{filename}"


def _docs_stub_path(p: ScaffoldParams) -> str:
    """Docs stub lands in ``docs/integrations/<slug>.md``."""
    if p.out_dir is not None:
        return str(p.out_dir / f"{p.slug}.md")
    return f"docs/integrations/{p.slug}.md"
