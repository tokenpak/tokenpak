"""Regression tests for the structural pattern specs in the public-safety scan.

Focused on the ``vault-path`` spec: the product's own documented vault-index
default directory (``/vault/.tokenpak`` under a home directory) is product
surface and must not be reported as a private-path leak, while every other
path under a vault directory must still match.
"""

from __future__ import annotations

import re

import pytest

from scripts.release_gate.public_safety_scan import RELEASE_PATTERN_SPECS


def _compiled(label: str) -> re.Pattern[str]:
    for spec in RELEASE_PATTERN_SPECS:
        if spec.label == label:
            return re.compile(spec.regex, spec.flags)
    raise AssertionError(f"pattern spec {label!r} not found")


@pytest.fixture(scope="module")
def vault_pattern() -> re.Pattern[str]:
    return _compiled("vault-path")


@pytest.mark.parametrize(
    "text",
    [
        "index_path: ~/vault/.tokenpak",
        "index_path: ~/vault/.tokenpak/index.db",
        'default = "~/vault/.tokenpak"',
    ],
)
def test_documented_vault_index_default_is_allowed(vault_pattern, text):
    assert vault_pattern.search(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "see ~/vault/",
        "see ~/vault",
        "spec lives at ~/vault/01_PROJECTS/x.md",
        "path: /home/someone/vault/notes.md",
        "path: /Users/someone/vault/notes.md",
        "path: ~/vault/.tokenpak-extra",
        "path: ~/vault/.tokenpakother",
    ],
)
def test_other_vault_paths_still_match(vault_pattern, text):
    assert vault_pattern.search(text) is not None


def test_private_home_and_tool_state_specs_present():
    labels = {spec.label for spec in RELEASE_PATTERN_SPECS}
    assert {"private-home-path", "vault-path", "private-tool-state-path"} <= labels
