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
        # Generated source MUST lint clean per spec §5.
        params = _params()
        arts = generate_artifacts(params)
        provider_art = next(a for a in arts if a.kind == "provider-class")
        # Wrap with import-block + blank line so ruff's I001 rule
        # (sorted imports + blank line before code) is satisfied;
        # the generated CLASS itself is what we're testing.
        wrapper = (
            "from __future__ import annotations\n"
            "\n"
            "from tokenpak.services.routing_service.credential_injector import (\n"
            "    _EnvKeyBearerProvider,\n"
            ")\n"
            "\n"
            "\n"
        )
        scratch = tmp_path / "scaffold_check.py"
        scratch.write_text(wrapper + provider_art.content)
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
