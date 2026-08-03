"""TIP7-002: Install-footprint extras split — validation checker.

Two test classes:
1. PyprojectHeavyDepCheck — asserts heavy packages are absent from
   [project.dependencies] after TIP7-001 lands. Run this as the
   post-demotion gate to confirm the pyproject change is correct.
2. ExtrasGuardSmokeTest — asserts guarded import sites still import or
   fail gracefully when the dep is absent. Uses unittest.mock.patch so no
   actual uninstall is needed.

Usage:
    pytest tests/test_extras_import_guard.py -v --tb=short

Or as a standalone checker (no pytest required):
    python3 tests/test_extras_import_guard.py

Standards cited: 02 §9 (Dependencies), 02 §10 (Formatting), 02 §11
(Commit Hygiene), 10 §2 A5
"""

from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

# tomllib is stdlib only on Python 3.11+. On 3.10 it doesn't exist, so the
# slim release test gate must skip cleanly there. This test file's purpose
# (post-demotion guarded-import smoke) is Python-version-independent —
# running it on 3.11/3.12/3.13 is sufficient coverage of the invariant.
tomllib = pytest.importorskip(
    "tomllib", reason="tomllib is stdlib in Python 3.11+; this test runs on 3.11/3.12/3.13"
)

import types
import unittest
import warnings
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).parent.parent


def _load_pyproject() -> dict:
    return tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())


def _direct_deps(data: dict) -> list[str]:
    return [
        d.split("[")[0].split(">=")[0].split("<=")[0].split("==")[0].split(";")[0].strip()
        for d in data["project"].get("dependencies", [])
    ]


# ---------------------------------------------------------------------------
# Class 1 — pyproject.toml post-demotion gate
# ---------------------------------------------------------------------------

# Packages that MUST NOT appear in [project.dependencies] after TIP7-001.
FORBIDDEN_IN_CORE = {
    "sentence-transformers": "tokenpak[retrieval]",
    "tree-sitter-languages": "tokenpak[code-compression]",
    "scipy": "tokenpak[intelligence]",
    "pandas": "tokenpak[data]",
    "llmlingua": "tokenpak[compression]",
    "litellm": "tokenpak[integrations-litellm]",
}

# Packages that MUST appear in [project.optional-dependencies] after TIP7-001.
REQUIRED_EXTRAS = {
    "retrieval": ["sentence-transformers"],
    "code-compression": ["tree-sitter-languages"],
    "intelligence": ["scipy"],
    "data": ["pandas"],
    "compression": ["llmlingua"],
    "integrations-litellm": ["litellm"],
    "full": [],  # meta-extra — just assert it exists
}


class PyprojectHeavyDepCheck(unittest.TestCase):
    """Asserts pyproject.toml matches the target extras structure.

    These tests FAIL before TIP7-001 merges (expected — see validation matrix
    §7 for the pre-demotion baseline). They are the pass/fail gate for QA.
    Mark with @unittest.skip("pre-TIP7-001") if you need to run the suite
    before Trix's change lands.
    """

    @classmethod
    def setUpClass(cls):
        cls.data = _load_pyproject()
        cls.direct = _direct_deps(cls.data)
        cls.opt = cls.data["project"].get("optional-dependencies", {})

    def test_heavy_packages_not_in_direct_deps(self):
        """No heavy ML/data package should appear in [project.dependencies]."""
        violations = [
            f"{pkg} (should be in {extra})"
            for pkg, extra in FORBIDDEN_IN_CORE.items()
            if pkg in self.direct
        ]
        self.assertFalse(
            violations,
            "Heavy packages still in [project.dependencies]:\n  " + "\n  ".join(violations),
        )

    def test_extras_declared(self):
        """All required extras exist in [project.optional-dependencies]."""
        missing = [name for name in REQUIRED_EXTRAS if name not in self.opt]
        self.assertFalse(
            missing,
            "Missing extras in [project.optional-dependencies]: " + ", ".join(missing),
        )

    def test_retrieval_extra_contains_sentence_transformers(self):
        st_deps = self.opt.get("retrieval", [])
        found = any("sentence-transformers" in d for d in st_deps)
        self.assertTrue(found, "retrieval extra must list sentence-transformers")

    def test_code_compression_extra_contains_tree_sitter(self):
        cc_deps = self.opt.get("code-compression", [])
        found = any("tree-sitter-languages" in d for d in cc_deps)
        self.assertTrue(found, "code-compression extra must list tree-sitter-languages")

    def test_intelligence_extra_contains_scipy(self):
        intel_deps = self.opt.get("intelligence", [])
        found = any("scipy" in d for d in intel_deps)
        self.assertTrue(found, "intelligence extra must list scipy")

    def test_data_extra_contains_pandas(self):
        data_deps = self.opt.get("data", [])
        found = any("pandas" in d for d in data_deps)
        self.assertTrue(found, "data extra must list pandas")

    def test_compression_extra_contains_llmlingua(self):
        comp_deps = self.opt.get("compression", [])
        found = any("llmlingua" in d for d in comp_deps)
        self.assertTrue(found, "compression extra must list llmlingua")

    def test_integrations_litellm_extra_contains_litellm(self):
        ll_deps = self.opt.get("integrations-litellm", [])
        found = any("litellm" in d for d in ll_deps)
        self.assertTrue(found, "integrations-litellm extra must list litellm")

    def test_full_meta_extra_references_all_feature_extras(self):
        full_deps = self.opt.get("full", [])
        for name in (
            "retrieval",
            "code-compression",
            "intelligence",
            "data",
            "compression",
            "integrations-litellm",
        ):
            found = any(name in d for d in full_deps)
            self.assertTrue(found, f"full meta-extra must reference [{name}]")


# ---------------------------------------------------------------------------
# Class 2 — ImportError guard smoke tests (mock-based, runnable pre-split)
# ---------------------------------------------------------------------------


class ExtrasGuardSmokeTest(unittest.TestCase):
    """Smoke-test that guarded import sites handle missing deps gracefully.

    These tests do NOT require the extra to be uninstalled — they use
    unittest.mock.patch to simulate an ImportError from the heavy dep and
    then reimport the module under test to verify behaviour.

    All tests should pass both before and after TIP7-001. They validate that
    the graceful-degradation pattern works correctly.
    """

    def _reload_with_missing(self, module_name: str, dep_to_block: str) -> types.ModuleType:
        """Reimport `module_name` with `dep_to_block` replaced by a stub that
        raises ImportError, using sys.modules manipulation.
        """
        # Remove the target module and its dep from sys.modules so they reimport.
        for key in list(sys.modules):
            if key == module_name or key.startswith(module_name + "."):
                del sys.modules[key]
        for key in list(sys.modules):
            if key == dep_to_block or key.startswith(dep_to_block + "."):
                del sys.modules[key]

        # Inject a fake dep that raises ImportError on import.
        fake = types.ModuleType(dep_to_block)

        def _raise(*a, **kw):
            raise ImportError(f"No module named '{dep_to_block}'")

        fake.__spec__ = None

        # Patch sys.modules so the target dep is "broken".
        with patch.dict(sys.modules, {dep_to_block: None}):
            mod = importlib.import_module(module_name)
        # Restore: re-remove so next test starts clean.
        for key in list(sys.modules):
            if key == module_name or key.startswith(module_name + "."):
                del sys.modules[key]
        return mod

    def test_span_extractor_loads_without_sentence_transformers(self):
        """span_extractor must import cleanly even if sentence-transformers is absent."""
        mod = self._reload_with_missing(
            "tokenpak.compression.span_extractor",
            "sentence_transformers",
        )
        self.assertFalse(
            mod._CROSS_ENCODER_AVAILABLE,
            "_CROSS_ENCODER_AVAILABLE must be False when sentence-transformers is absent",
        )

    def test_code_treesitter_loads_without_tree_sitter_languages(self):
        """code_treesitter must import cleanly even if tree-sitter-languages is absent."""
        mod = self._reload_with_missing(
            "tokenpak.compression.processors.code_treesitter",
            "tree_sitter_languages",
        )
        self.assertFalse(
            mod._TS_AVAILABLE,
            "_TS_AVAILABLE must be False when tree-sitter-languages is absent",
        )

    def test_scipy_has_no_current_runtime_import_site(self):
        """scipy is extra-only until a real source import site returns."""
        offenders: list[str] = []
        for source_file in (_REPO_ROOT / "tokenpak").rglob("*.py"):
            text = source_file.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import scipy", "from scipy")):
                    offenders.append(str(source_file.relative_to(_REPO_ROOT)))
                    break

        self.assertFalse(
            offenders,
            "scipy should have no current tokenpak source import sites: " + ", ".join(offenders),
        )

    def test_litellm_proxy_returns_error_without_litellm(self):
        """litellm ProxyHandler must return a 500 JSON error (not raise) when litellm absent."""
        import asyncio

        from tokenpak.sdk.integrations.litellm.proxy import ProxyHandler

        handler = ProxyHandler()

        # proxy.py supports plain dict input for testing (see handle() docstring).
        # Include "tokenpak" key so the handler reaches the litellm import check.
        req_dict = {
            "model": "gpt-4",
            "tokenpak": {},
            "messages": [{"role": "user", "content": "hi"}],
        }

        # Block litellm during the actual handle() call.
        loop = asyncio.new_event_loop()
        try:
            with patch.dict(sys.modules, {"litellm": None}):
                result = loop.run_until_complete(handler.handle(req_dict))
        finally:
            loop.close()

        # _json_error returns {"error": {"status": <N>, "message": <str>}}
        error_block = result.get("error", {})
        self.assertEqual(
            error_block.get("status"),
            500,
            f"Expected 500 error when litellm is absent, got: {result}",
        )
        self.assertIn(
            "litellm",
            error_block.get("message", "").lower(),
            "Error message should mention litellm",
        )


# ---------------------------------------------------------------------------
# Class 3 — recovery-UX message tests
# ---------------------------------------------------------------------------


class ExtrasRecoveryMessageTest(unittest.TestCase):
    """Assert every heavy-extra guard names the exact `pip install tokenpak[<extra>]`
    recovery path when its dependency is absent.

    These pin the recovery-UX contract: each live missing-extra surface
    must point the user at the canonical tokenpak extra, not a bare upstream
    `pip install <pkg>`. Mock-based — no extra needs to be uninstalled.
    """

    def _capture_import_warnings(self, module_name: str, dep_to_block: str):
        """Reimport `module_name` with `dep_to_block` blocked, returning
        (module, [warning_message, ...]) for the import-time guard warnings.
        """
        for key in list(sys.modules):
            if key == module_name or key.startswith(module_name + "."):
                del sys.modules[key]
        for key in list(sys.modules):
            if key == dep_to_block or key.startswith(dep_to_block + "."):
                del sys.modules[key]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with patch.dict(sys.modules, {dep_to_block: None}):
                mod = importlib.import_module(module_name)
        messages = [str(w.message) for w in caught]

        for key in list(sys.modules):
            if key == module_name or key.startswith(module_name + "."):
                del sys.modules[key]
        return mod, messages

    def test_vector_local_warning_names_retrieval_extra(self):
        """LocalVectorRetriever warns with `pip install tokenpak[retrieval]`."""
        mod, _ = self._capture_import_warnings(
            "tokenpak.vault.retrieval.vector_local", "sentence_transformers"
        )
        self.assertFalse(mod._ST_AVAILABLE)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mod.LocalVectorRetriever()
        joined = " ".join(str(w.message) for w in caught)
        self.assertIn(
            "pip install tokenpak[retrieval]",
            joined,
            f"vector_local guard must name the retrieval extra; got: {joined!r}",
        )

    def test_span_extractor_warning_names_retrieval_extra(self):
        """span_extractor warns with `pip install tokenpak[retrieval]` at import."""
        mod, messages = self._capture_import_warnings(
            "tokenpak.compression.span_extractor", "sentence_transformers"
        )
        self.assertFalse(mod._CROSS_ENCODER_AVAILABLE)
        joined = " ".join(messages)
        self.assertIn(
            "pip install tokenpak[retrieval]",
            joined,
            f"span_extractor guard must name the retrieval extra; got: {joined!r}",
        )

    def test_code_treesitter_warning_names_code_compression_extra(self):
        """code_treesitter warns with `pip install tokenpak[code-compression]` at import."""
        mod, messages = self._capture_import_warnings(
            "tokenpak.compression.processors.code_treesitter", "tree_sitter_languages"
        )
        self.assertFalse(mod._TS_AVAILABLE)
        joined = " ".join(messages)
        self.assertIn(
            "pip install tokenpak[code-compression]",
            joined,
            f"code_treesitter guard must name the code-compression extra; got: {joined!r}",
        )

    def test_llmlingua_runtime_error_names_compression_extra(self):
        """LLMLinguaEngine.compact raises naming `pip install tokenpak[compression]`."""
        from tokenpak.compression.engines.llmlingua import LLMLinguaEngine

        with patch.dict(sys.modules, {"llmlingua": None}):
            engine = LLMLinguaEngine()
        self.assertFalse(engine._available, "engine must degrade when llmlingua is absent")
        with self.assertRaises(RuntimeError) as ctx:
            engine.compact("hello world")
        self.assertIn(
            "pip install tokenpak[compression]",
            str(ctx.exception),
            "LLMLingua runtime error must name the compression extra",
        )

    def test_litellm_proxy_error_names_integrations_litellm_extra(self):
        """ProxyHandler 500 error names `pip install tokenpak[integrations-litellm]`."""
        import asyncio

        from tokenpak.sdk.integrations.litellm.proxy import ProxyHandler

        handler = ProxyHandler()
        req_dict = {
            "model": "gpt-4",
            "tokenpak": {},
            "messages": [{"role": "user", "content": "hi"}],
        }

        loop = asyncio.new_event_loop()
        try:
            with patch.dict(sys.modules, {"litellm": None}):
                result = loop.run_until_complete(handler.handle(req_dict))
        finally:
            loop.close()

        message = result.get("error", {}).get("message", "")
        self.assertIn(
            "pip install tokenpak[integrations-litellm]",
            message,
            f"litellm proxy guard must name the integrations-litellm extra; got: {message!r}",
        )


# ---------------------------------------------------------------------------
# Standalone runner (no pytest required)
# ---------------------------------------------------------------------------


def _standalone():

    data = _load_pyproject()
    direct = _direct_deps(data)
    opt = data["project"].get("optional-dependencies", {})

    passed = 0
    failed = 0

    print("=== pyproject.toml heavy-dep checker ===\n")
    for pkg, extra in FORBIDDEN_IN_CORE.items():
        if pkg in direct:
            print(f"FAIL: {pkg!r} found in [project.dependencies] — should be in {extra}")
            failed += 1
        else:
            print(f"PASS: {pkg!r} not in [project.dependencies]")
            passed += 1

    print("\n=== extras declared ===\n")
    for name in REQUIRED_EXTRAS:
        if name in opt:
            print(f"PASS: [{name}] extra exists")
            passed += 1
        else:
            print(f"FAIL: [{name}] extra missing from [project.optional-dependencies]")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    import sys

    # Run standalone checker first (cheap, no pytest needed).
    failures = _standalone()

    # Then run the unittest suite.
    print("\n=== guard smoke tests ===\n")
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(ExtrasGuardSmokeTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(1 if (failures > 0 or not result.wasSuccessful()) else 0)
