# SPDX-License-Identifier: Apache-2.0
"""Documented example payloads must state the version we actually ship.

The docs site advertised 1.14.0 while 1.15.0 was on PyPI. That was the second
occurrence of the same class — a version literal copied into a doc example,
correct on the day it was written and silently wrong at the next release. The
audit found one instance; a structural sweep found five, spread across four
files and three different stale values (1.0.0, 0.1.1, 1.1.0).

Bumping the literals fixes the instances. This test fixes the class.

It checks *example payloads* specifically, by parsing them, rather than
grepping for the word "version". That distinction matters: `openapi.yaml`
carries `info.version`, which is the version of the API document and is
correctly independent of the shipped package. A regex flags it; a structural
check does not, so nobody has to maintain an exclusion for it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

import tokenpak

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
_SEMVER = re.compile(r"^\d+\.\d+\.\d+")


def _is_historical(path: Path) -> bool:
    """Release logs state the version they released. That is correct forever."""
    return "release-log" in path.parts or "changelog" in path.name.lower()


def _doc_files() -> list[Path]:
    patterns = ("*.md", "*.yaml", "*.yml")
    return sorted(p for pattern in patterns for p in DOCS.rglob(pattern) if not _is_historical(p))


def _versions_in_markdown(text: str) -> Iterator[str]:
    """Top-level `version` of every parseable ```json block."""
    for block in _JSON_FENCE.findall(text):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            # Illustrative blocks with elisions are not claims we can check.
            continue
        if isinstance(payload, dict):
            value = payload.get("version")
            if isinstance(value, str) and _SEMVER.match(value):
                yield value


def _versions_in_examples(node: Any) -> Iterator[str]:
    """`version` inside any `example`/`examples` block of an OpenAPI document.

    Walks to find `example:` keys rather than every `version:` key, so the
    document's own `info.version` is out of scope by construction.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("example", "examples"):
                for found in _version_values(value):
                    yield found
            else:
                yield from _versions_in_examples(value)
    elif isinstance(node, list):
        for item in node:
            yield from _versions_in_examples(item)


def _version_values(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        value = node.get("version")
        if isinstance(value, str) and _SEMVER.match(value):
            yield value
        for sub in node.values():
            yield from _version_values(sub)
    elif isinstance(node, list):
        for item in node:
            yield from _version_values(item)


def test_docs_tree_is_present() -> None:
    """A silently empty sweep would pass every assertion below."""
    files = _doc_files()
    assert DOCS.is_dir(), f"expected a docs tree at {DOCS}"
    assert len(files) > 20, f"only {len(files)} doc files found — sweep looks broken"


def test_sweep_actually_reads_version_examples() -> None:
    """Guard the guard: the parser must find versioned examples somewhere.

    Without this, a refactor that broke JSON-fence detection would turn the
    whole suite green while checking nothing.
    """
    total = 0
    for doc in _doc_files():
        text = doc.read_text(encoding="utf-8", errors="replace")
        total += len(list(_versions_in_markdown(text)))
        if doc.suffix in (".yaml", ".yml"):
            try:
                total += len(list(_versions_in_examples(yaml.safe_load(text))))
            except yaml.YAMLError:
                pass
    assert total >= 5, f"expected the docs to carry versioned examples, found {total}"


@pytest.mark.parametrize("doc", _doc_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_example_version_matches_shipped(doc: Path) -> None:
    shipped = tokenpak.__version__
    text = doc.read_text(encoding="utf-8", errors="replace")

    found = set(_versions_in_markdown(text))
    if doc.suffix in (".yaml", ".yml"):
        try:
            found |= set(_versions_in_examples(yaml.safe_load(text)))
        except yaml.YAMLError:
            pass

    stale = sorted(v for v in found if v != shipped)
    assert not stale, (
        f"{doc.relative_to(REPO_ROOT)} shows version(s) {stale} in an example "
        f"payload but TokenPak ships {shipped}. Update the example; if the file "
        f"is a historical record, teach _is_historical() about it."
    )
