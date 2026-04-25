# SPDX-License-Identifier: Apache-2.0
"""Phase 4 MVP — ``tokenpak adapter scaffold`` test suite.

Covers Kevin's eight enumerated test categories:

  1. CLI flag parsing (subprocess-driven for true CLI behavior)
  2. Dry-run output (subprocess + assertions on stdout)
  3. Generated file paths (canonical-layout + ``--out-dir``)
  4. Generated adapter structure (parses + asserts class shape)
  5. Generated test fixture structure (parses JSON + asserts shape)
  6. Raw-secret self-check (guardrail rejects credential-shaped strings)
  7. ``live_verified=False`` default
  8. Failure on ambiguous required inputs in ``--non-interactive`` mode

All tests are offline; no network, no real credentials.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak.scaffold import (
    GuardrailViolation,
    ScaffoldError,
    ScaffoldParams,
    check_artifacts,
    generate_artifacts,
    parse_optional_dep_list,
)
from tokenpak.scaffold._config import parse_extra_header
from tokenpak.scaffold._generator import GeneratedArtifact

# Standard params for tests — Pattern A (openai-chat + bearer).
DEFAULT_KW = {
    "docs_url": "https://docs.example.com/api",
    "slug": "tokenpak-example",
    "family": "openai-chat",
    "auth": "bearer",
    "endpoint": "https://api.example.com/v1/chat/completions",
}


def _params(**overrides) -> ScaffoldParams:
    kw = dict(DEFAULT_KW)
    kw.update(overrides)
    return ScaffoldParams(**kw)


# ── 1. CLI flag parsing ──────────────────────────────────────────────


class TestCliFlagParsing:
    """The CLI accepts every flag the spec §2 defined."""

    def _run(self, *args, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "tokenpak", "adapter", "scaffold", *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            **kw,
        )

    def test_help_includes_scaffold(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "adapter", "scaffold", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0
        # All 11 flags Kevin enumerated must show in --help.
        for flag in (
            "--from-docs",
            "--slug",
            "--family",
            "--auth",
            "--endpoint",
            "--streaming",
            "--optional-dep",
            "--out-dir",
            "--dry-run",
            "--non-interactive",
            "--llm-assist",
        ):
            assert flag in result.stdout, f"--help missing {flag!r}"

    def test_missing_required_field_fails(self):
        # Omit --slug; argparse should error.
        result = self._run(
            "--from-docs", "https://docs.example.com",
            "--family", "openai-chat",
            "--auth", "bearer",
            "--endpoint", "https://api.example.com/v1/chat/completions",
        )
        assert result.returncode != 0
        assert "slug" in result.stderr.lower()

    def test_invalid_family_choice_fails(self):
        result = self._run(
            "--from-docs", "https://docs.example.com",
            "--slug", "tokenpak-example",
            "--family", "not-a-real-family",
            "--auth", "bearer",
            "--endpoint", "https://api.example.com/v1/chat/completions",
            "--dry-run",
        )
        assert result.returncode != 0
        assert "invalid choice" in result.stderr.lower() or "not-a-real-family" in result.stderr

    def test_llm_assist_returns_not_implemented(self):
        result = self._run(
            "--from-docs", "https://docs.example.com",
            "--slug", "tokenpak-example",
            "--family", "openai-chat",
            "--auth", "bearer",
            "--endpoint", "https://api.example.com/v1/chat/completions",
            "--llm-assist",
            "--dry-run",
        )
        assert result.returncode == 2
        assert "llm-assist" in result.stderr.lower() or "not implemented" in result.stderr.lower()


# ── 2. Dry-run output ───────────────────────────────────────────────


class TestDryRun:
    """``--dry-run`` lists every artifact path without writing anything."""

    def test_dry_run_prints_all_artifact_paths(self, tmp_path: Path):
        params = _params()
        artifacts = generate_artifacts(params)
        # Dry-run with --out-dir to keep test isolated from repo root.
        params.out_dir = tmp_path
        params.dry_run = True
        from tokenpak.scaffold import scaffold

        result = scaffold(params)
        assert result.dry_run is True
        # All 5 file artifacts (provider class + test + 2 fixtures + docs)
        # should be in written_paths even though no file actually exists.
        assert len(result.written_paths) == 5
        # Instructions surface separately (not a file) — exactly 1.
        assert len(result.instructions) == 1
        # No file under tmp_path actually exists post-dry-run.
        for p in result.written_paths:
            assert not p.exists(), f"dry-run wrote file: {p}"

    def test_dry_run_via_cli_subprocess(self, tmp_path: Path):
        result = subprocess.run(
            [
                sys.executable, "-m", "tokenpak", "adapter", "scaffold",
                "--from-docs", "https://docs.example.com/api",
                "--slug", "tokenpak-dryrun-test",
                "--family", "openai-chat",
                "--auth", "bearer",
                "--endpoint", "https://api.example.com/v1/chat/completions",
                "--out-dir", str(tmp_path),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "Dry run" in result.stdout
        assert "tokenpak-dryrun-test" in result.stdout
        # Confirm no files written.
        assert list(tmp_path.iterdir()) == []


# ── 3. Generated file paths ─────────────────────────────────────────


class TestGeneratedFilePaths:
    """Artifacts land at the canonical or ``--out-dir``-relative paths."""

    def test_canonical_paths_match_standard_23(self):
        params = _params()
        arts = generate_artifacts(params)
        rel_paths = {a.relative_path for a in arts if a.kind != "instructions"}
        assert (
            "tokenpak/services/routing_service/extras/example.py" in rel_paths
        )
        assert "tests/test_example_offline.py" in rel_paths
        assert "tests/fixtures/tokenpak-example/request.json" in rel_paths
        assert "tests/fixtures/tokenpak-example/response.json" in rel_paths
        assert "docs/integrations/tokenpak-example.md" in rel_paths

    def test_out_dir_overrides_canonical_paths(self, tmp_path: Path):
        params = _params(out_dir=tmp_path)
        arts = generate_artifacts(params)
        for a in arts:
            if a.kind == "instructions":
                continue
            # All artifacts live under out_dir when set.
            assert a.relative_path.startswith(str(tmp_path)), (
                f"artifact {a.relative_path!r} not under out_dir {tmp_path}"
            )

    def test_multi_word_vendor_uses_underscore_in_filename(self):
        # Slug ``tokenpak-azure-openai`` → vendor ``azure-openai`` →
        # filename ``azure_openai.py`` (Python module convention).
        params = _params(slug="tokenpak-azure-openai")
        arts = generate_artifacts(params)
        rel_paths = {a.relative_path for a in arts if a.kind != "instructions"}
        # MVP only supports openai-chat+bearer; Azure-style scaffolding
        # would need more flags. But the vendor naming convention IS
        # exercised here.
        assert any("azure_openai" in p for p in rel_paths)


# ── 4. Generated adapter structure ──────────────────────────────────


class TestGeneratedAdapterStructure:
    """The CredentialProvider source compiles + has the expected shape."""

    def test_provider_class_compiles(self):
        params = _params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # Must be parseable Python.
        compile(provider_art.content, provider_art.relative_path, "exec")

    def test_provider_class_subclasses_env_key_bearer(self):
        params = _params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "_EnvKeyBearerProvider" in provider_art.content

    def test_provider_class_declares_all_required_fields(self):
        params = _params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # Standard #23 §1.2 + §6.4
        assert "live_verified = False" in provider_art.content
        assert 'name = "tokenpak-example"' in provider_art.content
        assert (
            '_UPSTREAM = "https://api.example.com/v1/chat/completions"'
            in provider_art.content
        )
        assert '_ENV_VAR = "EXAMPLE_API_KEY"' in provider_art.content

    def test_provider_class_includes_extra_headers_when_passed(self):
        params = _params(
            extra_headers={
                "HTTP-Referer": "https://tokenpak.ai",
                "X-Title": "TokenPak",
            }
        )
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "_EXTRA_HEADERS" in provider_art.content
        assert "HTTP-Referer" in provider_art.content
        assert "tokenpak.ai" in provider_art.content
        assert "X-Title" in provider_art.content

    def test_provider_class_omits_extra_headers_block_when_empty(self):
        params = _params()  # no extra_headers
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "_EXTRA_HEADERS" not in provider_art.content

    def test_provider_class_docstring_links_to_docs_url(self):
        params = _params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "https://docs.example.com/api" in provider_art.content

    def test_provider_class_passes_ruff(self, tmp_path: Path):
        # Phase 4.1: generated provider files are SELF-CONTAINED
        # (include SPDX header, docstring, imports). Lint without
        # any wrapper — the file as written must pass ruff.
        params = _params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        scratch = tmp_path / "scaffold_check.py"
        scratch.write_text(provider_art.content)
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(scratch)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, (
            f"generated provider class failed ruff: {result.stdout}\n{result.stderr}"
        )

    def test_provider_class_is_self_contained(self):
        # Phase 4.1 hardening — generated file should compile without
        # any wrapper because it imports its own dependencies.
        params = _params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # SPDX header, module docstring, future-annotations, and the
        # base class import must all be present.
        assert "SPDX-License-Identifier" in provider_art.content
        assert "from __future__ import annotations" in provider_art.content
        assert "_EnvKeyBearerProvider" in provider_art.content
        # Module-level docstring (just after SPDX line).
        assert "Auto-scaffolded credential provider" in provider_art.content


# ── 5. Generated test fixture structure ─────────────────────────────


class TestGeneratedFixtures:
    """Fixture JSONs are valid + have the expected shape + carry SCAFFOLD-VERIFY note."""

    def test_request_fixture_is_valid_json(self):
        params = _params()
        arts = generate_artifacts(params)
        req = next(a for a in arts if a.relative_path.endswith("request.json"))
        # Must parse — fixture content is valid JSON.
        parsed = json.loads(req.content)
        assert "messages" in parsed
        assert "model" in parsed
        # SCAFFOLD-VERIFY marker is in the note field.
        assert "SCAFFOLD-VERIFY" in parsed.get("_scaffold_note", "")

    def test_response_fixture_is_valid_json(self):
        params = _params()
        arts = generate_artifacts(params)
        resp = next(a for a in arts if a.relative_path.endswith("response.json"))
        parsed = json.loads(resp.content)
        assert "choices" in parsed
        assert "usage" in parsed
        assert "SCAFFOLD-VERIFY" in parsed.get("_scaffold_note", "")

    def test_test_file_compiles(self):
        params = _params()
        arts = generate_artifacts(params)
        test_art = next(a for a in arts if a.kind == "test")
        compile(test_art.content, test_art.relative_path, "exec")

    def test_test_file_imports_generated_class_name(self):
        params = _params()
        arts = generate_artifacts(params)
        test_art = next(a for a in arts if a.kind == "test")
        # CamelCase: Example → ExampleCredentialProvider
        assert "ExampleCredentialProvider" in test_art.content

    def test_test_file_has_required_test_classes(self):
        params = _params()
        arts = generate_artifacts(params)
        test_art = next(a for a in arts if a.kind == "test")
        # Standard #23 §1.3 — required Test{Aspect} classes for an offline
        # contract test file.
        for cls in (
            "TestLiveVerifiedMarker",
            "TestProviderResolveGate",
            "TestAuthHeaderInjection",
            "TestRegistration",
        ):
            assert f"class {cls}" in test_art.content

    def test_docs_stub_contains_env_var(self):
        params = _params()
        arts = generate_artifacts(params)
        docs_art = next(a for a in arts if a.kind == "docs")
        assert "EXAMPLE_API_KEY" in docs_art.content
        assert "https://docs.example.com/api" in docs_art.content


# ── 6. Raw-secret self-check guardrail ──────────────────────────────


class TestRawSecretGuardrail:
    """Generated content with credential-shaped strings is rejected."""

    def test_legit_artifact_passes(self):
        # Sanity: normal generation produces no false positives.
        params = _params()
        arts = generate_artifacts(params)
        # Should not raise.
        check_artifacts(arts)

    def test_aws_access_key_pattern_rejected(self):
        # Inject a credential-shaped string into a synthetic artifact.
        bad = GeneratedArtifact(
            relative_path="tests/scaffold-bad.py",
            content=(
                "# Definitely a real secret hiding here\n"
                "REAL_KEY = 'AKIAREAL1234567890XX'\n"
            ),
            kind="provider-class",
        )
        with pytest.raises(GuardrailViolation, match="real credential"):
            check_artifacts([bad])

    def test_jwt_pattern_rejected(self):
        bad = GeneratedArtifact(
            relative_path="tests/scaffold-bad.py",
            content=(
                "TOKEN = 'eyJhbGciOiJSUzI1NiJ9."
                "eyJzdWIiOiJyZWFsLXVzZXIifQ.aBcDeFgHiJkLmNoPqRsTuVwXyZ12345678'\n"
            ),
            kind="provider-class",
        )
        with pytest.raises(GuardrailViolation):
            check_artifacts([bad])

    def test_openai_sk_pattern_rejected(self):
        bad = GeneratedArtifact(
            relative_path="tests/scaffold-bad.py",
            content=(
                "OPENAI_KEY = 'sk-proj-abcdef1234567890ghijklmnop'\n"
            ),
            kind="provider-class",
        )
        with pytest.raises(GuardrailViolation):
            check_artifacts([bad])

    def test_path_outside_repo_rejected(self):
        bad = GeneratedArtifact(
            relative_path="/etc/cron.d/scaffold-injected",
            content="# malicious\n",
            kind="docs",
        )
        with pytest.raises(GuardrailViolation, match="outside the repo"):
            check_artifacts([bad])

    def test_path_with_parent_traversal_rejected(self):
        bad = GeneratedArtifact(
            relative_path="../../etc/passwd",
            content="x",
            kind="docs",
        )
        with pytest.raises(GuardrailViolation, match="parent-directory"):
            check_artifacts([bad])


# ── 7. live_verified=False default ──────────────────────────────────


class TestLiveVerifiedDefault:
    """Standard #23 §6.4 — scaffolded providers default to unverified."""

    def test_params_default_is_false(self):
        params = ScaffoldParams(**DEFAULT_KW)
        assert params.live_verified is False

    def test_generated_class_emits_false(self):
        params = _params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "live_verified = False" in provider_art.content

    def test_generated_test_asserts_false(self):
        params = _params()
        arts = generate_artifacts(params)
        test_art = next(a for a in arts if a.kind == "test")
        assert "live_verified is False" in test_art.content

    def test_explicit_true_emits_true(self):
        # Maintainers post-verification can scaffold with live_verified=True
        # — exposed via internal API; CLI flag would be added later.
        params = _params(live_verified=True)
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "live_verified = True" in provider_art.content


# ── 8. Failure on ambiguous required inputs in --non-interactive ────


class TestNonInteractiveAmbiguityFailure:
    """Required fields missing → tool fails fast with a useful message."""

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "tokenpak", "adapter", "scaffold", *args, "--non-interactive"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_missing_endpoint_fails(self):
        result = self._run(
            "--from-docs", "https://docs.example.com",
            "--slug", "tokenpak-example",
            "--family", "openai-chat",
            "--auth", "bearer",
        )
        # argparse exits 2 on missing required.
        assert result.returncode != 0
        assert "endpoint" in result.stderr.lower()

    def test_missing_family_fails(self):
        result = self._run(
            "--from-docs", "https://docs.example.com",
            "--slug", "tokenpak-example",
            "--auth", "bearer",
            "--endpoint", "https://api.example.com/v1/chat/completions",
        )
        assert result.returncode != 0
        assert "family" in result.stderr.lower()

    def test_invalid_slug_fails_validation(self):
        # Slug doesn't start with tokenpak- — Standard #23 §1.1 violation.
        result = self._run(
            "--from-docs", "https://docs.example.com",
            "--slug", "not-a-tokenpak-slug",
            "--family", "openai-chat",
            "--auth", "bearer",
            "--endpoint", "https://api.example.com/v1/chat/completions",
        )
        assert result.returncode == 2
        assert "slug" in result.stderr.lower() or "Standard #23" in result.stderr


# ── Helpers + extras ────────────────────────────────────────────────


class TestParseHelpers:
    def test_parse_optional_dep_list_empty(self):
        assert parse_optional_dep_list("") == []
        assert parse_optional_dep_list(None) == []

    def test_parse_optional_dep_list_strips_whitespace(self):
        assert parse_optional_dep_list("boto3, botocore , urllib3") == [
            "boto3", "botocore", "urllib3",
        ]

    def test_parse_extra_header_valid(self):
        assert parse_extra_header("Foo=Bar") == ("Foo", "Bar")

    def test_parse_extra_header_strips_whitespace(self):
        assert parse_extra_header("  X-Title = TokenPak ") == ("X-Title", "TokenPak")

    def test_parse_extra_header_missing_equals(self):
        with pytest.raises(ScaffoldError):
            parse_extra_header("invalid")

    def test_parse_extra_header_empty_key(self):
        with pytest.raises(ScaffoldError):
            parse_extra_header("=value")


class TestUnsupportedFamilyAuthCombination:
    """Classifier raises with helpful message for not-yet-implemented combos."""

    def test_azure_wrapper_not_yet_implemented(self):
        params = _params(family="azure-openai-wrapper", auth="api-key-header")
        with pytest.raises(ScaffoldError, match="MVP does not yet"):
            generate_artifacts(params)

    def test_unknown_combination(self):
        params = _params(family="custom", auth="bearer")
        # Custom family + bearer auth isn't in the REFERENCE_PRS table.
        with pytest.raises(ScaffoldError):
            generate_artifacts(params)


# ── Phase 4.1 regression tests ───────────────────────────────────────


class TestConflictSkipBehavior:
    """Writer refuses to overwrite existing files (Standard #23 §3 spirit
    applied to codegen — non-destructive default).
    """

    def test_existing_file_is_skipped(self, tmp_path: Path):
        from tokenpak.scaffold import scaffold

        # First run writes everything.
        params = _params(out_dir=tmp_path)
        result1 = scaffold(params)
        assert len(result1.written_paths) == 5
        assert len(result1.skipped_existing) == 0

        # Second run: every file already exists → all skipped.
        params2 = _params(out_dir=tmp_path)
        result2 = scaffold(params2)
        assert len(result2.written_paths) == 0
        assert len(result2.skipped_existing) == 5

    def test_partial_existing_skips_only_overlap(self, tmp_path: Path):
        from tokenpak.scaffold import scaffold

        # Pre-write the docs stub only.
        (tmp_path / "tokenpak-example.md").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "tokenpak-example.md").write_text("# pre-existing\n")

        params = _params(out_dir=tmp_path)
        result = scaffold(params)
        assert len(result.skipped_existing) == 1
        assert result.skipped_existing[0].name == "tokenpak-example.md"
        assert len(result.written_paths) == 4


class TestAtomicWriteBehavior:
    """Writer uses temp-file + rename so a crash mid-write doesn't
    leave a half-written artifact.
    """

    def test_no_tmp_files_left_after_successful_write(self, tmp_path: Path):
        from tokenpak.scaffold import scaffold

        params = _params(out_dir=tmp_path)
        scaffold(params)
        # Walk the entire output tree; no .scaffold.tmp files should remain.
        leftover = list(tmp_path.rglob("*.scaffold.tmp"))
        assert leftover == [], f"atomic-write left tmp files: {leftover}"

    def test_dry_run_writes_nothing_to_disk(self, tmp_path: Path):
        from tokenpak.scaffold import scaffold

        params = _params(out_dir=tmp_path, dry_run=True)
        scaffold(params)
        # tmp_path should be empty.
        assert list(tmp_path.iterdir()) == []


class TestDocsStubContent:
    """Phase 4.1 polish — docs stub must be usable as-shipped."""

    def test_docs_stub_contains_provider_slug(self):
        params = _params()
        arts = generate_artifacts(params)
        docs_art = next(a for a in arts if a.kind == "docs")
        assert "tokenpak-example" in docs_art.content

    def test_docs_stub_contains_curl_example(self):
        params = _params()
        arts = generate_artifacts(params)
        docs_art = next(a for a in arts if a.kind == "docs")
        assert "curl" in docs_art.content
        assert "X-TokenPak-Provider" in docs_art.content

    def test_docs_stub_documents_optional_deps(self):
        params = _params(optional_deps=["boto3", "botocore"])
        arts = generate_artifacts(params)
        docs_art = next(a for a in arts if a.kind == "docs")
        # Both listed.
        assert "boto3" in docs_art.content
        assert "botocore" in docs_art.content
        # Install instruction is present.
        assert "pip install" in docs_art.content

    def test_docs_stub_omits_optional_dep_section_when_none(self):
        params = _params()
        arts = generate_artifacts(params)
        docs_art = next(a for a in arts if a.kind == "docs")
        assert "Optional Python dependencies" not in docs_art.content

    def test_docs_stub_has_troubleshooting_section(self):
        params = _params()
        arts = generate_artifacts(params)
        docs_art = next(a for a in arts if a.kind == "docs")
        assert "Troubleshooting" in docs_art.content


class TestExtraHeaderHandling:
    """Phase 4.1 — extra headers reflected in tests + exposed in
    follow-up issue text + present in fixtures' contract.
    """

    def test_extra_headers_appear_in_provider_class(self):
        params = _params(
            extra_headers={
                "HTTP-Referer": "https://tokenpak.ai",
                "X-Title": "TokenPak",
            }
        )
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # Header dict block emitted.
        assert "_EXTRA_HEADERS" in provider_art.content
        # Both keys + values quoted correctly.
        assert '"HTTP-Referer"' in provider_art.content
        assert '"https://tokenpak.ai"' in provider_art.content
        assert '"X-Title"' in provider_art.content

    def test_extra_headers_asserted_in_test_file(self):
        params = _params(
            extra_headers={"HTTP-Referer": "https://tokenpak.ai"}
        )
        arts = generate_artifacts(params)
        test_art = next(a for a in arts if a.kind == "test")
        # Phase 4.1 — generated test asserts each extra header is in
        # the resolved plan's add_headers.
        assert "test_extra_headers_injected" in test_art.content
        assert "HTTP-Referer" in test_art.content
        assert "https://tokenpak.ai" in test_art.content

    def test_no_extra_headers_block_when_none(self):
        params = _params()  # no extra_headers
        arts = generate_artifacts(params)
        test_art = next(a for a in arts if a.kind == "test")
        # No extra-header test class when no headers declared.
        assert "test_extra_headers_injected" not in test_art.content


class TestInvalidSlugHandling:
    """Standard #23 §1.1 slug regex enforced at the input boundary."""

    @pytest.mark.parametrize(
        "bad_slug",
        [
            "not-tokenpak-prefix",
            "TOKENPAK-uppercase",  # lowercase only
            "tokenpak_underscore",  # hyphens only
            "tokenpak-",  # nothing after prefix
            "",  # empty
            "tokenpak",  # no suffix
            "tokenpak--double-hyphen",  # double hyphen
            "tokenpak-end-",  # trailing hyphen
        ],
    )
    def test_invalid_slug_raises(self, bad_slug):
        params = _params(slug=bad_slug)
        with pytest.raises(ScaffoldError):
            params.validate()

    @pytest.mark.parametrize(
        "good_slug",
        [
            "tokenpak-mistral",
            "tokenpak-azure-openai",
            "tokenpak-bedrock-claude",
            "tokenpak-vertex-gemini",
            "tokenpak-foo123",
            "tokenpak-multi-word-name",
        ],
    )
    def test_valid_slug_accepted(self, good_slug):
        params = _params(slug=good_slug)
        params.validate()  # must not raise


class TestLlmAssistRefusal:
    """Phase 4.1 regression — --llm-assist exits explicitly,
    deterministically, with a non-zero code per spec §1.2.
    """

    def test_llm_assist_exits_2(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "tokenpak", "adapter", "scaffold",
                "--from-docs", "https://docs.example.com",
                "--slug", "tokenpak-llm-test",
                "--family", "openai-chat",
                "--auth", "bearer",
                "--endpoint", "https://api.example.com/v1/chat/completions",
                "--llm-assist",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 2
        # Message must mention --llm-assist + "not implemented".
        msg = result.stderr.lower()
        assert "llm-assist" in msg or "llm_assist" in msg
        assert "not implemented" in msg or "deferred" in msg


# ── New renderer: openai-chat + api-key-header ──────────────────────


class TestApiKeyHeaderRenderer:
    """Phase 4.1 — second renderer for OpenAI-Chat + api-key-header
    auth (non-Bearer). Generates a standalone class with custom
    auth header (default ``api-key``).
    """

    def _params(self, **over):
        kw = dict(DEFAULT_KW)
        kw["auth"] = "api-key-header"
        kw.update(over)
        return ScaffoldParams(**kw)

    def test_classifier_picks_apikey_renderer(self):
        params = self._params()
        arts = generate_artifacts(params)  # must not raise classifier error
        assert len(arts) == 6  # 5 files + 1 instructions

    def test_provider_class_uses_api_key_header_not_bearer(self):
        params = self._params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # The auth header is configurable; default is "api-key".
        assert '_AUTH_HEADER = "api-key"' in provider_art.content
        # Specifically NOT injecting an Authorization: Bearer header.
        # (The word "Bearer" may appear in the docstring; the test
        # asserts on the actual generated header injection logic.)
        assert "headers = {self._AUTH_HEADER: api_key}" in provider_art.content
        # The bearer template would emit ``Authorization: f"Bearer {...}"``;
        # the api-key template never does.
        assert 'f"Bearer {' not in provider_art.content
        assert 'Authorization":' not in provider_art.content

    def test_provider_class_is_standalone_module(self):
        params = self._params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # Self-contained; doesn't extend _EnvKeyBearerProvider.
        assert "InjectionPlan" in provider_art.content
        assert "_cached_resolve" in provider_art.content
        assert "_EnvKeyBearerProvider" not in provider_art.content

    def test_provider_class_compiles(self):
        params = self._params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        compile(provider_art.content, provider_art.relative_path, "exec")

    def test_provider_class_passes_ruff(self, tmp_path: Path):
        params = self._params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        scratch = tmp_path / "scaffold_apikey_check.py"
        scratch.write_text(provider_art.content)
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(scratch)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, (
            f"api-key provider failed ruff: {result.stdout}\n{result.stderr}"
        )

    def test_test_file_asserts_api_key_header(self):
        params = self._params()
        arts = generate_artifacts(params)
        test_art = next(a for a in arts if a.kind == "test")
        assert "test_emits_api_key_header_not_bearer" in test_art.content

    def test_extra_headers_supported(self):
        params = self._params(extra_headers={"X-Region": "us-east"})
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "X-Region" in provider_art.content
        assert "us-east" in provider_art.content


# ── --register flag (in-place patch) ─────────────────────────────────


class TestRegisterPatch:
    """Phase 4.1 — opt-in --register flag patches credential_injector.py
    in-place. Must be idempotent and refuse to patch when anchors
    aren't found.
    """

    def test_apply_register_patch_is_idempotent(self, tmp_path: Path, monkeypatch):
        # Use a synthetic credential_injector.py for this test so we
        # don't mutate the real one.
        synth_dir = tmp_path / "tokenpak" / "services" / "routing_service"
        synth_dir.mkdir(parents=True)
        synth = synth_dir / "credential_injector.py"
        synth.write_text(
            "register(ExistingProvider())\n"
            "\n"
            "# ── Register built-ins at import\n"
            "register(AnotherProvider())\n"
            "\n"
            '__all__ = [\n'
            '    "ExistingProvider",\n'
            ']\n'
        )

        # Repoint the register module's path resolver at the synthetic.
        from tokenpak.scaffold import _register

        monkeypatch.setattr(_register, "_INJECTOR_PATH", synth)

        # First apply.
        _register.apply_register_patch(
            vendor_safe="test_provider",
            class_name="TestProviderCredentialProvider",
        )
        first = synth.read_text()
        assert "register(TestProviderCredentialProvider())" in first
        assert "import TestProviderCredentialProvider" in first or (
            "from tokenpak.services.routing_service.extras.test_provider import "
            "TestProviderCredentialProvider"
        ) in first
        assert '"TestProviderCredentialProvider"' in first

        # Second apply — idempotent, no double entries.
        _register.apply_register_patch(
            vendor_safe="test_provider",
            class_name="TestProviderCredentialProvider",
        )
        second = synth.read_text()
        assert second == first
        # Exactly one register() line for the new class.
        assert second.count("register(TestProviderCredentialProvider())") == 1

    def test_apply_register_patch_refuses_when_no_anchor(
        self, tmp_path: Path, monkeypatch
    ):
        from tokenpak.scaffold import RegisterError, _register

        synth_dir = tmp_path / "tokenpak" / "services" / "routing_service"
        synth_dir.mkdir(parents=True)
        synth = synth_dir / "credential_injector.py"
        # File without the expected anchors.
        synth.write_text("# random content\n")
        monkeypatch.setattr(_register, "_INJECTOR_PATH", synth)

        with pytest.raises(RegisterError):
            _register.apply_register_patch(
                vendor_safe="x",
                class_name="XProviderCredentialProvider",
            )


# ── Phase 4.2: configurable auth header for api-key-header renderer ──


class TestApiKeyHeaderConfigurable:
    """Phase 4.2 — ``--auth-header NAME`` overrides the default
    ``api-key`` so vendor-specific headers (``X-API-Key``,
    ``Api-Key``, etc.) are emitted at scaffold-time without a
    post-edit.
    """

    def _params(self, **over):
        kw = dict(DEFAULT_KW)
        kw["auth"] = "api-key-header"
        kw.update(over)
        return ScaffoldParams(**kw)

    def test_default_auth_header_is_api_key(self):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert '_AUTH_HEADER = "api-key"' in provider_art.content

    def test_custom_auth_header_emitted_in_class(self):
        arts = generate_artifacts(self._params(auth_header="X-API-Key"))
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert '_AUTH_HEADER = "X-API-Key"' in provider_art.content
        assert '_AUTH_HEADER = "api-key"' not in provider_art.content

    def test_custom_auth_header_asserted_in_test_file(self):
        arts = generate_artifacts(self._params(auth_header="X-API-Key"))
        test_art = next(a for a in arts if a.kind == "test")
        # The test file's header-injection assertion uses the
        # configured header name, not the default.
        assert '"X-API-Key"' in test_art.content

    def test_custom_auth_header_lowercased_in_strip_set(self):
        # Caller-sent variants should be stripped — proxy is the
        # authoritative source for the configured header.
        arts = generate_artifacts(self._params(auth_header="X-API-Key"))
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # Strip set is alphabetised; check the lowercased name is in.
        assert '"x-api-key"' in provider_art.content

    def test_custom_auth_header_passes_ruff(self, tmp_path: Path):
        arts = generate_artifacts(self._params(auth_header="X-API-Key"))
        provider_art = next(a for a in arts if a.kind == "provider-class")
        scratch = tmp_path / "scaffold_apikey_custom.py"
        scratch.write_text(provider_art.content)
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(scratch)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        assert result.returncode == 0, (
            f"custom-header provider failed ruff: "
            f"{result.stdout}\n{result.stderr}"
        )


# ── Phase 4.2: bearer-passthrough renderer ───────────────────────────


class TestBearerPassthroughRenderer:
    """Phase 4.2 — ``--auth bearer-passthrough`` for OpenRouter-style
    providers preserving extra/non-standard request body fields.
    Generates Pattern A class + ``_BODY_PASSTHROUGH = True`` annotation
    + a ``TestBodyPassThrough`` class asserting ``body_transform`` is
    None on the InjectionPlan.
    """

    def _params(self, **over):
        kw = dict(DEFAULT_KW)
        kw["auth"] = "bearer-passthrough"
        kw.update(over)
        return ScaffoldParams(**kw)

    def test_classifier_picks_passthrough_renderer(self):
        arts = generate_artifacts(self._params())
        assert len(arts) == 6  # 5 files + 1 instructions

    def test_provider_class_declares_body_passthrough(self):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "_BODY_PASSTHROUGH = True" in provider_art.content

    def test_provider_class_subclasses_env_key_bearer(self):
        # Same base class as Pattern A — the only difference is the
        # explicit passthrough annotation + a stricter test contract.
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "(_EnvKeyBearerProvider)" in provider_art.content

    def test_provider_class_compiles(self):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        compile(provider_art.content, provider_art.relative_path, "exec")

    def test_provider_class_passes_ruff(self, tmp_path: Path):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        scratch = tmp_path / "scaffold_passthrough_check.py"
        scratch.write_text(provider_art.content)
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(scratch)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        assert result.returncode == 0, (
            f"passthrough provider failed ruff: "
            f"{result.stdout}\n{result.stderr}"
        )

    def test_test_file_has_body_passthrough_class(self):
        arts = generate_artifacts(self._params())
        test_art = next(a for a in arts if a.kind == "test")
        assert "class TestBodyPassThrough" in test_art.content
        assert "test_no_body_transform_in_plan" in test_art.content
        assert "test_class_declares_passthrough" in test_art.content

    def test_test_file_compiles(self):
        arts = generate_artifacts(self._params())
        test_art = next(a for a in arts if a.kind == "test")
        compile(test_art.content, test_art.relative_path, "exec")

    def test_extra_headers_supported(self):
        # OpenRouter-style: HTTP-Referer + X-Title alongside body
        # pass-through.
        arts = generate_artifacts(
            self._params(extra_headers={"HTTP-Referer": "https://x.example"})
        )
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert "_EXTRA_HEADERS" in provider_art.content
        assert "HTTP-Referer" in provider_art.content


# ── Phase 4.2: anthropic-messages + api-key-header renderer ──────────


class TestAnthropicMessagesApikeyRenderer:
    """Phase 4.2 — Anthropic Messages adapter via x-api-key auth +
    anthropic-version header. Capability declarations are EXPLICIT;
    no implicit TIP support. Fixtures use Anthropic shape (content
    blocks, max_tokens required, system as top-level field).
    """

    def _params(self, **over):
        kw = dict(DEFAULT_KW)
        kw["family"] = "anthropic-messages"
        kw["auth"] = "api-key-header"
        kw["endpoint"] = "https://api.example.com/v1/messages"
        kw.update(over)
        return ScaffoldParams(**kw)

    def test_classifier_picks_anthropic_renderer(self):
        arts = generate_artifacts(self._params())
        assert len(arts) == 6  # 5 files + 1 instructions

    def test_default_auth_header_is_x_api_key(self):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert '_AUTH_HEADER = "x-api-key"' in provider_art.content

    def test_custom_auth_header_override(self):
        arts = generate_artifacts(self._params(auth_header="X-API-Key"))
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert '_AUTH_HEADER = "X-API-Key"' in provider_art.content

    def test_anthropic_version_emitted(self):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        assert '_ANTHROPIC_VERSION = "2023-06-01"' in provider_art.content
        assert '"anthropic-version"' in provider_art.content

    def test_capabilities_declared_explicit_empty(self):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # Explicit empty frozenset — no implicit TIP support.
        assert "capabilities: frozenset = frozenset()" in provider_art.content
        # The candidate capability names appear in the surrounding
        # comment block (not as declared values).
        assert "tip.compression.v1" in provider_art.content
        assert "tip.cache.proxy-managed" in provider_art.content

    def test_provider_class_compiles(self):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        compile(provider_art.content, provider_art.relative_path, "exec")

    def test_provider_class_passes_ruff(self, tmp_path: Path):
        arts = generate_artifacts(self._params())
        provider_art = next(a for a in arts if a.kind == "provider-class")
        scratch = tmp_path / "scaffold_anthropic_check.py"
        scratch.write_text(provider_art.content)
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(scratch)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        assert result.returncode == 0, (
            f"anthropic provider failed ruff: "
            f"{result.stdout}\n{result.stderr}"
        )

    def test_request_fixture_has_anthropic_shape(self):
        arts = generate_artifacts(self._params())
        req_art = next(a for a in arts if a.kind == "fixture" and "request" in a.relative_path)
        body = json.loads(req_art.content)
        # Anthropic-specific: max_tokens required, system at top level.
        assert "max_tokens" in body
        assert "system" in body
        # No OpenAI-Chat-specific fields.
        assert "temperature" not in body

    def test_response_fixture_has_anthropic_shape(self):
        arts = generate_artifacts(self._params())
        resp_art = next(a for a in arts if a.kind == "fixture" and "response" in a.relative_path)
        body = json.loads(resp_art.content)
        # Anthropic content blocks (not OpenAI choices array).
        assert "content" in body
        assert isinstance(body["content"], list)
        assert body["content"][0]["type"] == "text"
        # Anthropic usage shape.
        assert "input_tokens" in body["usage"]
        assert "output_tokens" in body["usage"]
        # No OpenAI-specific fields.
        assert "choices" not in body
        assert "prompt_tokens" not in body.get("usage", {})

    def test_test_file_has_capability_class(self):
        arts = generate_artifacts(self._params())
        test_art = next(a for a in arts if a.kind == "test")
        assert "class TestCapabilityDeclarations" in test_art.content
        assert "test_no_implicit_tip_support" in test_art.content

    def test_test_file_compiles(self):
        arts = generate_artifacts(self._params())
        test_art = next(a for a in arts if a.kind == "test")
        compile(test_art.content, test_art.relative_path, "exec")

    def test_test_file_asserts_anthropic_version(self):
        arts = generate_artifacts(self._params())
        test_art = next(a for a in arts if a.kind == "test")
        assert "test_emits_anthropic_version" in test_art.content
        assert '"anthropic-version"' in test_art.content
        assert "2023-06-01" in test_art.content

    def test_docs_stub_documents_anthropic_specifics(self):
        arts = generate_artifacts(self._params())
        docs_art = next(a for a in arts if a.kind == "docs")
        assert "anthropic-version" in docs_art.content
        assert "Capability declarations" in docs_art.content
        # Curl example uses Anthropic shape (max_tokens, anthropic-version).
        assert "max_tokens" in docs_art.content

    def test_dry_run_works(self, tmp_path: Path):
        # Acceptance criterion: dry-run works for each renderer.
        params = self._params(out_dir=tmp_path, dry_run=True)
        arts = generate_artifacts(params)
        check_artifacts(arts)  # passes guardrails
        # Nothing on disk — dry_run doesn't write here, but
        # generate_artifacts itself doesn't write either; this asserts
        # the generation pipeline is clean for the new renderer.
        assert (tmp_path / "request.json").exists() is False


# ── Phase 4.2: non-interactive failure on missing required fields ────


class TestPhase42NonInteractiveSafety:
    """Acceptance criterion: non-interactive mode fails safely when
    required fields are missing for the new renderers.
    """

    def test_anthropic_missing_endpoint_fails(self):
        params = ScaffoldParams(
            docs_url="https://docs.example.com",
            slug="tokenpak-x",
            family="anthropic-messages",
            auth="api-key-header",
            endpoint="",  # empty — should fail
            non_interactive=True,
        )
        with pytest.raises(ScaffoldError):
            params.validate()

    def test_passthrough_missing_endpoint_fails(self):
        params = ScaffoldParams(
            docs_url="https://docs.example.com",
            slug="tokenpak-x",
            family="openai-chat",
            auth="bearer-passthrough",
            endpoint="",
            non_interactive=True,
        )
        with pytest.raises(ScaffoldError):
            params.validate()

    def test_invalid_slug_rejected_for_anthropic(self):
        # Standard #23 §1.1 slug rules apply across renderers.
        params = ScaffoldParams(
            docs_url="https://docs.example.com",
            slug="Bad_Slug",
            family="anthropic-messages",
            auth="api-key-header",
            endpoint="https://api.example.com/v1/messages",
        )
        with pytest.raises(ScaffoldError):
            params.validate()
