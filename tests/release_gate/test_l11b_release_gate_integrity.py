"""L11b release-gate integrity regressions.

Each test proves a previously fail-open / green-theater release gate now fails
CLOSED in the broken shape and passes in the fixed shape:

    H3 — scripts/check_cli_compat.py        (CLI breaking-change gate)
    H4 — scripts/check_config_migration.py  (config-default / failover gate)
    H6 — scripts/release_gate/gen_api_snapshot.py  (SDK import-error masking)
    H5 — scripts/generate_benchmarks_report.py + docs/BENCHMARKS.md  (synthetic
         benchmark labeling + build-host fingerprint)

Scripts are loaded by path (mirroring the other release_gate tests) so the suite
does not depend on a particular ``scripts/`` package layout.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(mod_name: str, *parts: str):
    path = _REPO_ROOT.joinpath(*parts)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ccc = _load("check_cli_compat", "scripts", "check_cli_compat.py")
ccm = _load("check_config_migration", "scripts", "check_config_migration.py")
gas = _load("gen_api_snapshot", "scripts", "release_gate", "gen_api_snapshot.py")
gbr = _load("generate_benchmarks_report", "scripts", "generate_benchmarks_report.py")


# ── H3: check_cli_compat fails closed ────────────────────────────────────────
def test_h3_passes_on_real_parser():
    """Baseline: the real CLI parser exposes every STABLE_COMMANDS entry."""
    commands = ccc.get_current_commands()
    missing = [c for c in ccc.STABLE_COMMANDS if c not in commands]
    assert not missing, f"stable commands missing from live parser: {missing}"


def test_h3_fails_closed_when_parser_unimportable(monkeypatch):
    """Mutate-to-red: a missing CLI source must HARD FAIL, not return
    STABLE_COMMANDS (the old fail-open branch)."""
    monkeypatch.setitem(sys.modules, "tokenpak._cli_core", None)
    with pytest.raises(SystemExit) as exc:
        ccc.get_current_commands()
    assert exc.value.code == 1


def test_h3_fails_closed_on_empty_parser(monkeypatch):
    """Mutate-to-red: a parser with no subcommands means the gate cannot verify
    anything and must fail closed."""
    monkeypatch.setattr(
        "tokenpak._cli_core.build_parser",
        lambda: argparse.ArgumentParser(),
    )
    with pytest.raises(SystemExit) as exc:
        ccc.get_current_commands()
    assert exc.value.code == 1


# ── H4: check_config_migration fails closed ──────────────────────────────────
def test_h4_passes_on_baseline():
    """Baseline: env-defaults and failover schema both pass on the current tree."""
    ccm.check_env_defaults()
    ccm.check_failover_config_schema()


def test_h4_env_defaults_fails_on_removed_key(monkeypatch):
    """Mutate-to-red: a registered default that no longer appears in the package
    source is a breaking change and must fail (old code's loop body was ``pass``
    so it could never fail)."""
    patched = dict(ccm.CONFIG_KEYS_WITH_DEFAULTS)
    patched["TOKENPAK_DEFINITELY_NOT_A_REAL_KEY_XYZ"] = "0"
    monkeypatch.setattr(ccm, "CONFIG_KEYS_WITH_DEFAULTS", patched)
    with pytest.raises(SystemExit) as exc:
        ccm.check_env_defaults()
    assert exc.value.code == 1


def test_h4_failover_fails_closed_on_import_error(monkeypatch):
    """Mutate-to-red: an unimportable failover module must HARD FAIL, not print
    'skipping' and return (the old fail-open ``except ImportError`` branch)."""
    monkeypatch.setitem(sys.modules, "tokenpak.proxy.failover", None)
    with pytest.raises(SystemExit) as exc:
        ccm.check_failover_config_schema()
    assert exc.value.code == 1


# ── H6: gen_api_snapshot stops masking real SDK errors ───────────────────────
def _mnfe(missing: str) -> ModuleNotFoundError:
    e = ModuleNotFoundError(f"No module named '{missing}'")
    e.name = missing
    return e


def test_h6_normalizes_genuine_thirdparty_sidecar_absence():
    """A genuine 'optional sidecar package not installed' MNFE is still
    normalized to the host-independent canonical string (preserved behavior)."""
    out = gas._format_import_error(_mnfe("crewai"), "tokenpak.sdk.crewai.examples.basic")
    assert out == "<IMPORT_ERROR: ModuleNotFoundError: No module named 'crewai_tokenpak'>"


def test_h6_surfaces_real_syntaxerror_in_sidecar():
    """Mutate-to-red: a real non-import exception inside a tokenpak.sdk.* module
    must surface honestly (drift the snapshot), not be masked as a missing
    sidecar."""
    out = gas._format_import_error(SyntaxError("invalid syntax"), "tokenpak.sdk.crewai.broken")
    assert "crewai_tokenpak" not in out
    assert "SyntaxError" in out


def test_h6_surfaces_firstparty_regression_in_sidecar():
    """Mutate-to-red: a first-party tokenpak.* ModuleNotFoundError raised while
    importing a sidecar is a real regression and must surface, not be masked."""
    out = gas._format_import_error(_mnfe("tokenpak.proxy.gone"), "tokenpak.sdk.crewai.x")
    assert "crewai_tokenpak" not in out
    assert "tokenpak.proxy.gone" in out


# ── H5: benchmark report is labeled synthetic and host-fingerprint-free ───────
def test_h5_synthetic_banner_is_honest():
    banner = gbr.SYNTHETIC_BANNER.lower()
    assert "synthetic" in banner
    assert "not a product benchmark" in banner


def test_h5_docs_benchmarks_labeled_synthetic_and_no_host_fingerprint():
    doc = (_REPO_ROOT / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
    assert "Synthetic placeholder" in doc, "BENCHMARKS.md must carry the synthetic banner"
    # Mutate-to-red: the previous doc embedded a build-host kernel string like
    # 'Linux 6.17.0-14-generic'. No host fingerprint may be published.
    assert not re.search(r"Linux \d+\.\d+", doc), (
        "build-host kernel string leaked into BENCHMARKS.md"
    )
    assert "uname" not in doc
