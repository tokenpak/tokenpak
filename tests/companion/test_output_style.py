# SPDX-License-Identifier: Apache-2.0
"""Output-style directive: resolution, rendering, and managed-section safety.

The directive is written once in ``tokenpak.companion._style`` and rendered
into two hosts.  These tests pin the shared-source property, explicit opt-in,
and the AGENTS.md section-boundary behavior the merge and uninstall paths
depend on.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.companion import _style, launcher
from tokenpak.companion.codex import agents_md
from tokenpak.companion.codex.uninstall import clean_agents_md
from tokenpak.companion.config import CompanionConfig

MARKER = "# TokenPak Companion"
HEADING = "## Output style"

# The companion doctor warns at 80% of 32 KiB for the whole AGENTS.md file.  The
# managed section is one contributor among the user's own content, so it carries
# a tighter budget of its own.
SECTION_MAX_BYTES = 6 * 1024


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_style_defaults_to_standard_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("TOKENPAK_COMPANION_STYLE", raising=False)
    assert _style.resolve() == _style.STANDARD


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("lean", _style.LEAN),
        ("standard", _style.STANDARD),
        ("LEAN", _style.LEAN),
        ("  Standard  ", _style.STANDARD),
    ],
)
def test_style_normalizes_case_and_whitespace(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", raw)
    assert _style.resolve() == expected


@pytest.mark.parametrize("raw", ["", "bogus", "verbose", "0"])
def test_unknown_style_falls_back_to_default_rather_than_raising(monkeypatch, raw) -> None:
    """An unparseable style must never be able to block a session launch."""
    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", raw)
    assert _style.resolve() == _style.DEFAULT


def test_explicit_argument_beats_environment(monkeypatch) -> None:
    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", "standard")
    assert _style.resolve("lean") == _style.LEAN


def test_directive_is_empty_for_standard() -> None:
    assert _style.directive("standard") == ""
    assert HEADING in _style.directive("lean")


def test_config_reads_style_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", "standard")
    assert CompanionConfig.from_env().style == _style.STANDARD


# ---------------------------------------------------------------------------
# Both hosts render one source
# ---------------------------------------------------------------------------


def _write_prompt(tmp_path: Path, style: str | None = None) -> str:
    kwargs = {} if style is None else {"style": style}
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", **kwargs)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        return launcher._write_system_prompt(cfg)


def test_claude_and_codex_render_identical_directive_text(tmp_path) -> None:
    """One constant, two hosts — the two surfaces cannot drift apart."""
    prompt = Path(_write_prompt(tmp_path, "lean")).read_text()
    codex = agents_md.generate_agents_md("lean")
    body = _style.directive("lean")
    assert body in prompt
    assert body in codex


def test_unchanged_launches_omit_directive(tmp_path, monkeypatch) -> None:
    """Both hosts preserve native style unless lean is explicitly selected."""
    monkeypatch.delenv("TOKENPAK_COMPANION_STYLE", raising=False)
    assert HEADING not in Path(_write_prompt(tmp_path)).read_text()
    assert HEADING not in agents_md.generate_agents_md()


def test_claude_prompt_omits_directive_under_standard(tmp_path) -> None:
    assert HEADING not in Path(_write_prompt(tmp_path, "standard")).read_text()


def test_codex_content_omits_directive_under_standard() -> None:
    assert HEADING not in agents_md.generate_agents_md("standard")


@pytest.mark.parametrize("style", ["lean", "standard"])
def test_mcp_tool_names_survive_every_style(tmp_path, style) -> None:
    """The style block must never displace the tool inventory."""
    prompt = Path(_write_prompt(tmp_path, style)).read_text()
    codex = agents_md.generate_agents_md(style)
    for tool in (
        "estimate_tokens",
        "check_budget",
        "load_capsule",
        "prune_context",
        "journal_read",
        "journal_write",
        "session_info",
    ):
        assert tool in prompt, f"claude prompt missing tool: {tool}"
        assert tool in codex, f"codex AGENTS.md missing tool: {tool}"


def test_directive_declares_its_own_exceptions() -> None:
    """Lean must not silently apply to surfaces governed by other rules."""
    body = _style.directive("lean")
    for exception in ("release notes", "commit messages", "documentation"):
        assert exception in body
    assert "override this default" in body


def test_directive_forbids_trading_substance_for_brevity() -> None:
    body = _style.directive("lean")
    assert "Brevity is about prose, not substance." in body


def test_managed_section_stays_within_budget() -> None:
    size = len(agents_md.generate_agents_md("lean").encode("utf-8"))
    assert size <= SECTION_MAX_BYTES, f"managed section {size} B exceeds {SECTION_MAX_BYTES} B"


# ---------------------------------------------------------------------------
# AGENTS.md section boundary — merge and uninstall
# ---------------------------------------------------------------------------

_BEFORE = "# House rules\n\nKeep the tree green.\n"
_AFTER = "# Local overrides\n\nPrefer ripgrep.\n"


def test_style_heading_does_not_split_the_managed_section(tmp_path, monkeypatch) -> None:
    """``## Output style`` is a subsection, not a section boundary.

    ``_merge_agents`` ends the managed section at the next top-level ``# ``
    heading.  A regression to ``#`` here would strand the style block outside
    the section, where reinstall duplicates it and uninstall leaves it behind.
    """
    path = tmp_path / "AGENTS.md"
    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", "lean")

    # A first install appends the section at the end, so give it a following
    # top-level heading before reinstalling — that is the replace path where a
    # mis-levelled heading would strand the block.
    path.write_text(_BEFORE)
    agents_md._install_agents_md(target=str(tmp_path))
    path.write_text(path.read_text().rstrip() + "\n\n" + _AFTER)

    agents_md._install_agents_md(target=str(tmp_path))
    content = path.read_text()

    following = _AFTER.splitlines()[0]
    assert content.index(MARKER) < content.index(following), "fixture did not set up a follower"
    section = content[content.index(MARKER) : content.index(following)]
    assert HEADING in section, "style block escaped the managed section"
    assert content.count(HEADING) == 1


def test_install_preserves_user_content_on_both_sides(tmp_path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text(_BEFORE + "\n" + _AFTER)

    agents_md._install_agents_md(target=str(tmp_path))
    content = path.read_text()

    assert "Keep the tree green." in content
    assert "Prefer ripgrep." in content


def test_reinstall_is_idempotent(tmp_path, monkeypatch) -> None:
    """Repeated launches must not stack duplicate style blocks."""
    path = tmp_path / "AGENTS.md"
    path.write_text(_BEFORE)
    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", "lean")

    agents_md._install_agents_md(target=str(tmp_path))
    first = path.read_text()
    agents_md._install_agents_md(target=str(tmp_path))
    second = path.read_text()

    assert first == second
    assert second.count(HEADING) == 1
    assert second.count(MARKER) == 1


def test_switching_style_off_removes_the_block_on_reinstall(tmp_path, monkeypatch) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text(_BEFORE)

    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", "lean")
    agents_md._install_agents_md(target=str(tmp_path))
    assert HEADING in path.read_text()

    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", "standard")
    agents_md._install_agents_md(target=str(tmp_path))
    content = path.read_text()

    assert HEADING not in content
    assert MARKER in content
    assert "Keep the tree green." in content


def test_uninstall_removes_the_style_block_with_the_section(tmp_path, monkeypatch) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text(_BEFORE + "\n" + _AFTER)
    monkeypatch.setenv("TOKENPAK_COMPANION_STYLE", "lean")
    agents_md._install_agents_md(target=str(tmp_path))

    removed, _ = clean_agents_md(path)
    content = path.read_text()

    assert removed is True
    assert HEADING not in content
    assert MARKER not in content
    assert "Keep the tree green." in content
    assert "Prefer ripgrep." in content
