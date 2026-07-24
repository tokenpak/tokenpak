# SPDX-License-Identifier: Apache-2.0
"""Tests for the env-resolution load-order specification helper.

All tests are hermetic: a temp TokenPak home (via TOKENPAK_HOME), a temp cwd,
and an injected environ mapping. No real home, no network, no secret values
(placeholders only). The legacy-fallback layer is asserted OFF unless its
opt-in flag is explicitly set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenpak.config import load_order
from tokenpak.config.load_order import Layer, LoadOrderResolver


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- precedence / describe ---------------------------------------------------


def test_precedence_is_ordered_highest_first():
    order = load_order.precedence()
    assert order[0] is Layer.CLI_FLAG
    assert order[-1] is Layer.DEFAULT
    # strictly increasing rank
    ranks = [layer.value for layer in order]
    assert ranks == sorted(ranks)


def test_describe_returns_rows_in_order():
    rows = load_order.describe()
    assert rows[0][0] == 1 and rows[0][1] == "CLI_FLAG"
    assert rows[-1][1] == "DEFAULT"
    assert all(len(r) == 3 for r in rows)


# --- dotenv parsing ----------------------------------------------------------


def test_parse_dotenv_handles_comments_quotes_export_and_blank_lines():
    text = (
        "# a comment\n"
        "\n"
        "export FOO=bar\n"
        'QUOTED="hello world"\n'
        "SINGLE='x'\n"
        "  SPACED = trimmed  \n"
        "MALFORMED_NO_EQUALS\n"
    )
    parsed = load_order.parse_dotenv(text)
    assert parsed["FOO"] == "bar"
    assert parsed["QUOTED"] == "hello world"
    assert parsed["SINGLE"] == "x"
    assert parsed["SPACED"] == "trimmed"
    assert "MALFORMED_NO_EQUALS" not in parsed


# --- TC-L-01 .. TC-L-12 (precedence cases) -----------------------------------


def test_tcl01_process_env_beats_dotenv_and_config(tmp_path):
    # TC-L-01: key in env AND project .env AND config -> env wins.
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    _write(cwd / ".env", "K=from_dotenv\n")
    resolver = LoadOrderResolver(
        environ={"K": "from_env"},
        cwd=cwd,
        home=home,
        legacy_home=tmp_path / "legacy",
        config_lookup=lambda k: "from_config" if k == "K" else None,
    )
    res = resolver.resolve("K")
    assert res.value == "from_env"
    assert res.layer is Layer.PROCESS_ENV


def test_tcl02_project_dotenv_beats_user_dotenv(tmp_path):
    # TC-L-02: project .env beats user .env (more-specific wins).
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    _write(cwd / ".env", "K=project\n")
    _write(home / ".env", "K=user\n")
    resolver = LoadOrderResolver(environ={}, cwd=cwd, home=home, legacy_home=tmp_path / "legacy")
    res = resolver.resolve("K")
    assert res.value == "project"
    assert res.layer is Layer.PROJECT_DOTENV


def test_tcl03_user_dotenv_when_no_project(tmp_path):
    # TC-L-03: only user .env -> user .env wins.
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir(parents=True)
    _write(home / ".env", "K=user\n")
    resolver = LoadOrderResolver(environ={}, cwd=cwd, home=home, legacy_home=tmp_path / "legacy")
    res = resolver.resolve("K")
    assert res.value == "user"
    assert res.layer is Layer.USER_DOTENV


def test_tcl04_config_file_when_no_env_or_dotenv(tmp_path):
    # TC-L-04: only config -> config wins.
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir(parents=True)
    resolver = LoadOrderResolver(
        environ={},
        cwd=cwd,
        home=home,
        legacy_home=tmp_path / "legacy",
        config_lookup=lambda k: "cfg" if k == "K" else None,
    )
    res = resolver.resolve("K")
    assert res.value == "cfg"
    assert res.layer is Layer.USER_CONFIG


def test_tcl05_default_when_key_nowhere(tmp_path):
    # TC-L-05: key nowhere -> default.
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir(parents=True)
    resolver = LoadOrderResolver(environ={}, cwd=cwd, home=home, legacy_home=tmp_path / "legacy")
    res = resolver.resolve("K", default="fallback")
    assert res.value == "fallback"
    assert res.layer is Layer.DEFAULT
    assert res.found is False


def test_tcl07_unknown_key_resolves_without_exception(tmp_path):
    # TC-L-07: unknown TOKENPAK_* in env resolves to raw value, no crash.
    resolver = LoadOrderResolver(
        environ={"TOKENPAK_BOGUS": "1"},
        cwd=tmp_path,
        home=tmp_path / "home",
        legacy_home=tmp_path / "legacy",
    )
    res = resolver.resolve("TOKENPAK_BOGUS")
    assert res.value == "1"
    assert res.layer is Layer.PROCESS_ENV


def test_tcl08_type_coercion_after_selection():
    # TC-L-08: int/bool coercion applied post-selection.
    assert load_order.coerce("8766", "int") == 8766
    assert load_order.coerce("1", "bool") is True
    assert load_order.coerce("off", "bool") is False
    assert load_order.coerce("a, b ,c", "csv") == ["a", "b", "c"]
    assert load_order.coerce(None, "int") is None


def test_tcl09_openclaw_fallback_off_by_default(tmp_path):
    # TC-L-09: key only in legacy .env, flag unset -> falls through to default.
    home = tmp_path / "home"
    legacy = tmp_path / "legacy"
    cwd = tmp_path / "proj"
    cwd.mkdir(parents=True)
    _write(legacy / ".env", "K=from_legacy\n")
    resolver = LoadOrderResolver(environ={}, cwd=cwd, home=home, legacy_home=legacy)
    res = resolver.resolve("K", default="def")
    assert res.value == "def"
    assert res.layer is Layer.DEFAULT


@pytest.mark.skip(reason="layer 5 build gated — legacy .env reader is HELD (design 3.6)")
def test_tcl10_openclaw_fallback_gated_when_flag_set(tmp_path):
    # TC-L-10: HELD — the legacy fallback reader is build-gated.
    home = tmp_path / "home"
    legacy = tmp_path / "legacy"
    _write(legacy / ".env", "K=from_legacy\n")
    resolver = LoadOrderResolver(
        environ={"TOKENPAK_OPENCLAW_FALLBACK": "1"},
        cwd=tmp_path / "proj",
        home=home,
        legacy_home=legacy,
    )
    res = resolver.resolve("K")
    assert res.value == "from_legacy"
    assert res.layer is Layer.LEGACY_DOTENV


def test_legacy_layer_explicitly_enabled_reads_legacy_env(tmp_path):
    # Companion to TC-L-09: with the opt-in explicitly passed, layer 5 IS the
    # winner — verifying the spec's *position* (below first-class .env) is
    # implemented, while the default-off contract (TC-L-09) is what ships.
    home = tmp_path / "home"
    legacy = tmp_path / "legacy"
    cwd = tmp_path / "proj"
    cwd.mkdir(parents=True)
    _write(legacy / ".env", "K=from_legacy\n")
    resolver = LoadOrderResolver(
        environ={}, cwd=cwd, home=home, legacy_home=legacy, openclaw_fallback=True
    )
    res = resolver.resolve("K")
    assert res.value == "from_legacy"
    assert res.layer is Layer.LEGACY_DOTENV


def test_tcl11_dotenv_never_clobbers_higher_layer(tmp_path):
    # TC-L-11: env-set K + different .env K -> env wins; .env value never seen.
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    _write(cwd / ".env", "K=dotenv_value\n")
    resolver = LoadOrderResolver(
        environ={"K": "env_value"}, cwd=cwd, home=home, legacy_home=tmp_path / "legacy"
    )
    assert resolver.get("K") == "env_value"


def test_provenance_maps_many_keys(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    _write(cwd / ".env", "B=proj\n")
    resolver = LoadOrderResolver(
        environ={"A": "env"}, cwd=cwd, home=home, legacy_home=tmp_path / "legacy"
    )
    prov = resolver.provenance(["A", "B", "C"])
    assert prov["A"].layer is Layer.PROCESS_ENV
    assert prov["B"].layer is Layer.PROJECT_DOTENV
    assert prov["C"].layer is Layer.DEFAULT


def test_resolver_creates_no_files_and_no_dirs(tmp_path):
    # Read-only invariant: resolving never writes to home/cwd/legacy.
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    legacy = tmp_path / "legacy"
    cwd.mkdir(parents=True)
    resolver = LoadOrderResolver(environ={"K": "v"}, cwd=cwd, home=home, legacy_home=legacy)
    resolver.resolve("K")
    resolver.resolve("MISSING")
    assert not home.exists()
    assert not legacy.exists()
    assert list(cwd.iterdir()) == []
