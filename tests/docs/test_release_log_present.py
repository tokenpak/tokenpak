# SPDX-License-Identifier: Apache-2.0
"""The version being shipped must have a release log.

v1.15.0 shipped with no release log at all, and nothing objected. The control
was real and documented; what was missing was anything that evaluated it, so it
read as satisfied for as long as nobody looked. That is the same shape as a gate
whose ingest never runs.

Scope is deliberately the *current* version only. Most historical releases have
no log either, and asserting over every tag would demand a backfill of records
nobody can now reconstruct honestly -- it would fail loudly and be silenced,
which is worse than not existing. Guarding `__version__` makes the next release
carry a log without inventing evidence for old ones.

`status` is not required to be `complete` here: the log is opened before
publication and closed after it, so at the moment this test runs on a release PR
the correct state is `in-progress`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import tokenpak

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_LOG_DIR = REPO_ROOT / "docs" / "release-log"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_ALLOWED_STATUS = {"in-progress", "complete"}


def _log_path() -> Path:
    return RELEASE_LOG_DIR / f"v{tokenpak.__version__}.md"


def _frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def test_release_log_exists_for_the_shipped_version():
    path = _log_path()
    assert path.is_file(), (
        f"no release log for the version being shipped: expected {path.relative_to(REPO_ROOT)}. "
        "Open one before the release PR merges — it is the record of what shipped, and after "
        "the tag is public nobody reconstructs it accurately."
    )


def test_release_log_frontmatter_agrees_with_the_package():
    fields = _frontmatter(_log_path().read_text(encoding="utf-8"))
    assert fields, "release log has no YAML frontmatter"
    assert fields.get("version") == tokenpak.__version__, (
        f"release log says version {fields.get('version')!r}, package says "
        f"{tokenpak.__version__!r} — a copied log that was never re-pointed"
    )


@pytest.mark.parametrize("field", ["release_type", "status", "owner"])
def test_release_log_declares_its_governance_fields(field):
    fields = _frontmatter(_log_path().read_text(encoding="utf-8"))
    assert fields.get(field), f"release log frontmatter is missing {field!r}"


def test_release_log_status_is_a_known_state():
    status = _frontmatter(_log_path().read_text(encoding="utf-8")).get("status")
    assert status in _ALLOWED_STATUS, (
        f"release log status {status!r} is not one of {sorted(_ALLOWED_STATUS)}"
    )
