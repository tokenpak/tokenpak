"""Tests for the hashed private-term register (placeholder words only)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "release_gate" / "private_terms.py"

sys.path.insert(0, str(SCRIPT.parent))
import private_terms as pt  # noqa: E402


def _register(name_word: str, handle_word: str) -> dict[str, frozenset[str]]:
    return {
        "name": frozenset({pt.term_hash(name_word)}),
        "handle": frozenset({pt.term_hash(handle_word)}),
    }


def test_term_hash_is_case_insensitive_and_salted():
    assert pt.term_hash("Example") == pt.term_hash("eXAMPLE")
    assert len(pt.term_hash("example")) == 8
    assert pt.term_hash("example") != pt.term_hash("example2")


def test_find_private_terms_whole_words_only():
    reg = _register("zorblax", "zorblax99")
    assert [h.kind for h in pt.find_private_terms("hi Zorblax!", reg)] == ["name"]
    assert [h.kind for h in pt.find_private_terms("host zorblax99 here", reg)] == ["handle"]
    assert pt.find_private_terms("zorblaxes and my_zorblax_x", reg) == []
    assert pt.find_private_terms("nothing to see", reg) == []


def test_shipped_register_holds_only_hex_hashes():
    for hashes in pt.PRIVATE_TERMS.values():
        assert hashes
        for h in hashes:
            assert len(h) == 8 and int(h, 16) >= 0


def test_cli_hash_mode():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--hash", "example"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == pt.term_hash("example")


def test_cli_scan_mode_clean_file(tmp_path):
    f = tmp_path / "clean.md"
    f.write_text("nothing private here\n", encoding="utf-8")
    res = subprocess.run([sys.executable, str(SCRIPT), str(f)], capture_output=True, text=True)
    assert res.returncode == 0


def test_tracked_files_carry_no_private_terms():
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    hits = []
    for rel in filter(None, out.split("\0")):
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.split("\n"), 1):
            for hit in pt.find_private_terms(line):
                hits.append(f"{rel}:{i}: {hit.kind}")
    assert not hits, "private terms in tracked files:\n" + "\n".join(hits[:20])
