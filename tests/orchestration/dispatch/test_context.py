"""Tests for the Dispatch ContextProvider (P-CONTEXT-01, Standards Delta v0 §5.9).

Covers LocalContextProvider determinism, gitignore-aware filtering, per-station
size + token budget enforcement (skip-not-truncate + recorded), and the
PaidContextProvider stub boundary.
"""

from __future__ import annotations

import pytest

# Dispatch is pydantic-native; skip cleanly on slim installs that lack it
# rather than erroring at collection time (mirrors the other dispatch tests).
pytest.importorskip("pydantic")

from tokenpak.orchestration.dispatch.context import (
    ContextBudget,
    ContextBundle,
    ContextProvider,
    LocalContextProvider,
    PaidContextProvider,
)
from tokenpak.orchestration.dispatch.context.provider import (
    ContextSource,
    GitignoreFilter,
    SkipReason,
    estimate_tokens,
)
from tokenpak.orchestration.dispatch.models.common import (
    ManifestPermissions,
    QualityRequirements,
)
from tokenpak.orchestration.dispatch.models.enums import (
    AutonomyMode,
    ManifestStatus,
)
from tokenpak.orchestration.dispatch.models.manifest import DispatchManifest
from tokenpak.orchestration.dispatch.models.route import RouteStation

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _manifest() -> DispatchManifest:
    return DispatchManifest(
        id="manifest_test01",
        job_id="job_test01",
        route_id="route.code_task.v1",
        goal="exercise the context provider",
        permissions=ManifestPermissions(autonomy_mode=AutonomyMode.DRAFT),
        quality_requirements=QualityRequirements(
            test_required=True,
            review_required=True,
            docs_required=False,
            evidence_required=False,
        ),
        status=ManifestStatus.ACTIVE,
    )


def _station() -> RouteStation:
    return RouteStation(
        id="build",
        required_role="builder",
        required_capabilities=["code_drafting"],
        output_schema="station_result.v1",
    )


def _build_repo(tmp_path):
    """Create a small fake repo tree with a .gitignore. Returns the root."""

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('app')\n", encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text("def util():\n    return 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")

    # An ignored build artifact + an ignored directory.
    (tmp_path / "secret.log").write_text("SHOULD NOT APPEAR\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.bin").write_text("artifact\n", encoding="utf-8")

    (tmp_path / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_identical_inputs(tmp_path):
    """Identical inputs → identical ContextBundle (§5.9 guarantee)."""

    root = _build_repo(tmp_path)
    manifest, station = _manifest(), _station()

    provider_a = LocalContextProvider(root)
    provider_b = LocalContextProvider(root)

    bundle_a = provider_a.build_context(manifest, station)
    bundle_b = provider_b.build_context(manifest, station)

    assert isinstance(bundle_a, ContextBundle)
    # Full structural equality (pydantic __eq__) — byte-for-byte deterministic.
    assert bundle_a == bundle_b
    assert bundle_a.model_dump() == bundle_b.model_dump()

    # And the file ordering itself is stable/sorted within the repo scan.
    paths = [f.path for f in bundle_a.files]
    assert paths == sorted(paths)


def test_local_provider_satisfies_protocol():
    """LocalContextProvider is a structural ContextProvider; the stub is too."""

    assert isinstance(LocalContextProvider(), ContextProvider)


# ---------------------------------------------------------------------------
# gitignore filtering
# ---------------------------------------------------------------------------


def test_gitignore_excludes_ignored_files(tmp_path):
    """A .gitignore-matched file/dir is excluded from the scan and recorded."""

    root = _build_repo(tmp_path)
    bundle = LocalContextProvider(root).build_context(_manifest(), _station())

    paths = {f.path for f in bundle.files}
    assert "src/app.py" in paths
    assert "src/util.py" in paths
    assert "README.md" in paths
    # *.log and the build/ directory are gitignored — absent from the scan.
    assert "secret.log" not in paths
    assert not any(p.startswith("build/") for p in paths)
    # The .gitignore file itself is included (it is not self-ignored).
    assert ".gitignore" in paths


def test_gitignore_filters_declared_files_too(tmp_path):
    """An explicitly-declared file that is gitignored is still excluded (§5.9)."""

    root = _build_repo(tmp_path)
    provider = LocalContextProvider(
        root,
        explicit_files=["secret.log"],
        enable_repo_scan=False,
    )
    bundle = provider.build_context(_manifest(), _station())

    assert not any(f.path == "secret.log" for f in bundle.files)
    skipped = {s.path: s.reason for s in bundle.skipped}
    assert skipped.get("secret.log") == SkipReason.GITIGNORED


def test_gitignore_negation_reinstates():
    """Negation (!pat) re-includes a previously-ignored path (last match wins)."""

    from tokenpak.orchestration.dispatch.context.provider import _GitignoreRule

    flt = GitignoreFilter([_GitignoreRule("*.log"), _GitignoreRule("!keep.log")])
    assert flt.is_ignored("debug.log") is True
    assert flt.is_ignored("keep.log") is False


def test_git_dir_always_pruned(tmp_path):
    """.git/ is always ignored regardless of .gitignore content."""

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    bundle = LocalContextProvider(tmp_path).build_context(_manifest(), _station())
    assert not any(f.path.startswith(".git/") for f in bundle.files)
    assert any(f.path == "keep.py" for f in bundle.files)


# ---------------------------------------------------------------------------
# Size budget
# ---------------------------------------------------------------------------


def test_size_budget_enforced(tmp_path):
    """Oversized candidate set is truncated; skipped files recorded (§5.9)."""

    root = tmp_path
    # Two ~200-byte files; budget only admits one.
    (root / "a.txt").write_text("a" * 200, encoding="utf-8")
    (root / "b.txt").write_text("b" * 200, encoding="utf-8")

    budget = ContextBudget(size_budget_bytes=250, token_budget=10_000_000)
    bundle = LocalContextProvider(root, budget=budget).build_context(
        _manifest(), _station()
    )

    assert bundle.truncated is True
    assert len(bundle.files) == 1
    assert bundle.total_size_bytes <= 250
    # The excluded file is recorded with the size-budget reason.
    skip_reasons = {s.path: s.reason for s in bundle.skipped}
    assert SkipReason.SIZE_BUDGET_EXCEEDED in skip_reasons.values()
    # Deterministic: a.txt (sorts first) is the one kept.
    assert bundle.files[0].path == "a.txt"


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------


def test_token_budget_enforced(tmp_path):
    """Token budget stops file inclusion; skipped recorded with token reason."""

    root = tmp_path
    # ~400 chars => ~100 tokens each at 4 chars/token.
    (root / "a.txt").write_text("x" * 400, encoding="utf-8")
    (root / "b.txt").write_text("y" * 400, encoding="utf-8")

    # Budget admits one file's tokens (100) but not two (200).
    budget = ContextBudget(size_budget_bytes=10_000_000, token_budget=120)
    bundle = LocalContextProvider(root, budget=budget).build_context(
        _manifest(), _station()
    )

    assert bundle.truncated is True
    assert len(bundle.files) == 1
    assert bundle.token_estimate <= 120
    skip_reasons = [s.reason for s in bundle.skipped]
    assert SkipReason.TOKEN_BUDGET_EXCEEDED in skip_reasons


def test_estimate_tokens_deterministic():
    """Token estimate is a pure, reproducible function (no network/LLM)."""

    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2  # ceil(5/4)
    assert estimate_tokens("x" * 400) == 100


# ---------------------------------------------------------------------------
# Source breakdown + precedence
# ---------------------------------------------------------------------------


def test_sources_breakdown_and_explicit_precedence(tmp_path):
    """sources breakdown reports per-source counts; explicit dedups vs scan."""

    root = _build_repo(tmp_path)
    provider = LocalContextProvider(
        root,
        explicit_files=["src/app.py"],  # also found by the scan
        manual_attachments=["README.md"],  # also found by the scan
    )
    bundle = provider.build_context(_manifest(), _station())

    # app.py is attributed to the highest-precedence source (explicit), not scan.
    by_path = {f.path: f.source for f in bundle.files}
    assert by_path["src/app.py"] == ContextSource.EXPLICIT
    assert by_path["README.md"] == ContextSource.MANUAL_ATTACHMENT
    # No path appears twice.
    assert len(by_path) == len(bundle.files)
    # The scan's duplicate of an explicit/manual file is recorded as DUPLICATE.
    dup_paths = {s.path for s in bundle.skipped if s.reason == SkipReason.DUPLICATE}
    assert {"src/app.py", "README.md"} <= dup_paths
    # Breakdown counts only contributing sources.
    assert bundle.sources.get("explicit") == 1
    assert bundle.sources.get("manual_attachment") == 1
    assert "repo_scan" in bundle.sources


def test_no_repo_scan_when_disabled(tmp_path):
    """enable_repo_scan=False yields no repo_scan-sourced files."""

    root = _build_repo(tmp_path)
    provider = LocalContextProvider(
        root, explicit_files=["src/app.py"], enable_repo_scan=False
    )
    bundle = provider.build_context(_manifest(), _station())
    assert [f.path for f in bundle.files] == ["src/app.py"]
    assert "repo_scan" not in bundle.sources


def test_scan_suffix_filter(tmp_path):
    """scan_suffixes restricts the scan to the given extensions."""

    root = _build_repo(tmp_path)
    provider = LocalContextProvider(root, scan_suffixes={".py"})
    bundle = provider.build_context(_manifest(), _station())
    assert all(f.path.endswith(".py") for f in bundle.files)
    assert any(f.path == "src/app.py" for f in bundle.files)
    assert not any(f.path == "README.md" for f in bundle.files)


# ---------------------------------------------------------------------------
# PaidContextProvider stub
# ---------------------------------------------------------------------------


def test_paid_provider_raises_on_instantiation():
    """PaidContextProvider raises NotImplementedError when instantiated (§5.9)."""

    with pytest.raises(NotImplementedError):
        PaidContextProvider()


def test_paid_provider_build_context_raises():
    """Even if construction is bypassed, build_context raises NotImplementedError."""

    stub = PaidContextProvider.__new__(PaidContextProvider)
    with pytest.raises(NotImplementedError):
        stub.build_context(_manifest(), _station())
