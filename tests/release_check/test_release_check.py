"""Tier-1 release-check gate tests — a passing and a deliberately-failing
fixture per deterministic gate.

The module is loaded by path so the test does not depend on a particular
package layout for ``scripts/``. Lives under tests/ (which the release-check
leak gate and the identity scan both skip), so the deliberate leak/literal
fixtures below do not flag the test file itself.
"""

import builtins
import importlib.util
import sys
from pathlib import Path

_MOD = Path(__file__).resolve().parents[2] / "scripts" / "release_check" / "release_check.py"
_spec = importlib.util.spec_from_file_location("release_check_under_test", _MOD)
rc = importlib.util.module_from_spec(_spec)
# Register before exec so the module's @dataclass can resolve cls.__module__
# via sys.modules (the standard importlib-by-path requirement).
sys.modules[_spec.name] = rc
_spec.loader.exec_module(rc)


# --- maturity ----------------------------------------------------------------
def _write_pkg(root, classifier="4 - Beta", status="Beta", license_ok=True):
    (root / "README.md").write_text(
        f"# TokenPak\n**Status:** {status} — APIs may change.\n"
        "Licensed under the Apache License 2.0.\n",
        encoding="utf-8",
    )
    lic = "Apache License\nVersion 2.0, January 2004\n" if license_ok else "MIT License\n"
    (root / "LICENSE").write_text(lic, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tokenpak"\n'
        "classifiers = [\n"
        f'    "Development Status :: {classifier}",\n'
        '    "License :: OSI Approved :: Apache Software License",\n'
        "]\n",
        encoding="utf-8",
    )


def test_maturity_pass(tmp_path):
    _write_pkg(tmp_path, classifier="4 - Beta", status="Beta")
    assert rc.gate_maturity(tmp_path).ok


def test_maturity_pass_when_readme_declares_no_marker(tmp_path):
    # Adapted 2026-07-19 (ADAPT-INTO-CURRENT-REPAIR): match-if-declared — a
    # README with no maturity marker contradicts nothing; only a declared
    # marker that mismatches the classifier (or an unknown marker) fails.
    _write_pkg(tmp_path, classifier="5 - Production/Stable", status="Beta")
    (tmp_path / "README.md").write_text(
        "# TokenPak\nLicensed under the Apache License 2.0.\n", encoding="utf-8"
    )
    assert rc.gate_maturity(tmp_path).ok


def test_maturity_fail_production_stable(tmp_path):
    # the anchoring incident: README Beta but classifier Production/Stable
    _write_pkg(tmp_path, classifier="5 - Production/Stable", status="Beta")
    r = rc.gate_maturity(tmp_path)
    assert not r.ok


# --- license -----------------------------------------------------------------
def test_license_pass(tmp_path):
    _write_pkg(tmp_path, license_ok=True)
    assert rc.gate_license(tmp_path).ok


def test_license_fail_non_apache(tmp_path):
    _write_pkg(tmp_path, license_ok=False)
    assert not rc.gate_license(tmp_path).ok


# --- leak (delta-style shared scanner) --------------------------------------
def test_leak_gate_allows_public_fleet_and_openclaw_forms(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "x.md").write_text(
        "Run `tokenpak fleet` with caller `openclaw:main`.\n", encoding="utf-8"
    )
    result = rc.gate_leak(tmp_path, changed=["docs/x.md"])
    assert result.ok, result.messages


def test_leak_gate_flags_ticket_and_path(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "x.md").write_text("Tracked in TSR-7; logs under /home/sue/run.\n", encoding="utf-8")
    result = rc.gate_leak(tmp_path, changed=["docs/x.md"])
    assert not result.ok
    assert len(result.messages) == 2


def test_leak_gate_fails_when_base_is_unavailable(tmp_path):
    result = rc.gate_leak(tmp_path, base=None, changed=None)
    assert not result.ok
    assert "cannot resolve" in result.messages[0]


def test_leak_gate_scans_extensionless_public_files(tmp_path):
    (tmp_path / "Makefile").write_text("Tracked in TSR-7.\n", encoding="utf-8")
    result = rc.gate_leak(tmp_path, changed=["Makefile"])
    assert not result.ok


def test_leak_gate_fails_when_changed_file_is_unavailable(tmp_path):
    result = rc.gate_leak(tmp_path, changed=["docs/missing.md"])
    assert not result.ok
    assert "unavailable" in result.messages[0]


def test_leak_gate_fails_when_changed_file_cannot_be_read(tmp_path, monkeypatch):
    target = tmp_path / "Makefile"
    target.write_text("release: all\n", encoding="utf-8")
    original_open = builtins.open

    def denied_open(file, *args, **kwargs):
        if Path(file) == target:
            raise PermissionError("read denied")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denied_open)
    result = rc.gate_leak(tmp_path, changed=["Makefile"])
    assert not result.ok
    assert "scanner failed" in result.messages[0]


def test_leak_gate_excludes_only_the_canonical_scanner_self_register(tmp_path):
    scanner = tmp_path / "scripts" / "release_gate" / "check_release_leaks.py"
    scanner.parent.mkdir(parents=True)
    scanner.write_text('PATTERNS = [r"TSR-[0-9]"]\n', encoding="utf-8")
    result = rc.gate_leak(
        tmp_path,
        changed=["scripts/release_gate/check_release_leaks.py"],
    )
    assert result.ok, result.messages

    sibling = scanner.with_name("another_gate.py")
    sibling.write_text("Tracked in TSR-7.\n", encoding="utf-8")
    result = rc.gate_leak(tmp_path, changed=["scripts/release_gate/another_gate.py"])
    assert not result.ok


# --- help-verbs (pure core) --------------------------------------------------
def test_help_verbs_all_resolve():
    assert rc.check_help_verbs([("serve", True), ("config", True)]) == []


def test_help_verbs_detects_phantom():
    assert rc.check_help_verbs([("serve", True), ("ghost", False)]) == ["ghost"]


def test_live_cli_has_no_phantom_verbs():
    # integration: the real parser must expose no unresolved verb. Guards the
    # ancestor-dispatch case (e.g. `openclaw refresh-models` has no own func but
    # is dispatched by the openclaw handler — must count as resolved).
    verbs = rc.collect_cli_verbs()
    assert verbs, "expected the live CLI to expose verbs"
    assert rc.check_help_verbs(verbs) == []


# --- tokenpak-literal regression --------------------------------------------
def test_tokenpak_literal_pass_when_baselined(tmp_path):
    pkg = tmp_path / "tokenpak"
    pkg.mkdir()
    (pkg / "legacy.py").write_text('HOME = "~/.tokenpak/config.yaml"\n', encoding="utf-8")
    r = rc.gate_tokenpak_literal(tmp_path, allowed={"tokenpak/legacy.py"})
    assert r.ok


def test_tokenpak_literal_fail_on_new_offender(tmp_path):
    pkg = tmp_path / "tokenpak"
    pkg.mkdir()
    (pkg / "newmod.py").write_text('p = "~/.tokenpak/new.db"\n', encoding="utf-8")
    r = rc.gate_tokenpak_literal(tmp_path, allowed=set())
    assert not r.ok


# --- orchestrator exit code --------------------------------------------------
def test_main_single_gate_exit_zero_on_clean(tmp_path):
    _write_pkg(tmp_path, classifier="4 - Beta", status="Beta")
    assert rc.main(["--root", str(tmp_path), "--gate", "maturity"]) == 0


def test_main_single_gate_exit_one_on_incident(tmp_path):
    _write_pkg(tmp_path, classifier="5 - Production/Stable", status="Beta")
    assert rc.main(["--root", str(tmp_path), "--gate", "maturity"]) == 1
