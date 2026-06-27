# SPDX-License-Identifier: Apache-2.0
"""Tests for the vault-index protected-path classification.

Numbered top-level vault sections (``NN_<name>/``) and the
credentials/secrets/private subtrees must classify as ``protected`` so the
index builder keeps them verbatim (``must_keep=True``, never compressed).

The section prefix is matched structurally — no specific folder name is
encoded in the shipped pattern list. These tests use neutral example paths
only, so the test module itself stays free of any concrete folder name.
"""

import re

from tokenpak.compression.core import _PROTECTED_PATTERNS, _classify


class TestProtectedPatternClassification:
    def test_numbered_top_level_section_is_protected(self):
        for rel in ("00_alpha/notes.md", "03_beta/x.txt", "12_archive/y.json"):
            risk_class, must_keep = _classify(rel)
            assert (risk_class, must_keep) == ("protected", True), rel

    def test_credential_subtrees_are_protected(self):
        for rel in (
            "agents/list.md",
            "team/credentials/key.json",
            "shared/secrets/token.txt",
            "user/private/diary.md",
        ):
            risk_class, must_keep = _classify(rel)
            assert (risk_class, must_keep) == ("protected", True), rel

    def test_unnumbered_narrative_path_is_not_protected(self):
        # No leading numbered prefix and no sensitive subtree → not protected.
        for rel in ("inbox/daily.md", "docs/readme.md"):
            risk_class, must_keep = _classify(rel)
            assert must_keep is False, rel
            assert risk_class != "protected", rel

    def test_single_digit_prefix_does_not_trigger_section_rule(self):
        # The numbered-section rule requires a two-digit prefix; a single
        # digit must not be treated as a protected vault section.
        risk_class, must_keep = _classify("7_scratch/file.md")
        assert must_keep is False
        assert risk_class != "protected"


class TestProtectedPatternsAreNeutral:
    def test_patterns_encode_no_specific_folder_identity(self):
        """The shipped pattern list must not name any personal folder.

        Only the neutral semantic subtree names are permitted as alpha
        tokens; the numbered-section rule is purely structural.
        """
        joined = " ".join(_PROTECTED_PATTERNS)
        alpha_tokens = set(re.findall(r"[a-z]{3,}", joined))
        allowed = {"agents", "credentials", "secrets", "private"}
        assert alpha_tokens <= allowed, f"unexpected identity tokens: {alpha_tokens - allowed}"
