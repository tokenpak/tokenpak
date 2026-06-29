"""Regression tests for the full-tree public-leak release gate.

Exercises scripts/release_gate/check_release_leaks.py end-to-end via its CLI:

  * an untouched file carrying an internal reference FAILS the gate
    (the blind spot the per-PR delta gate cannot catch);
  * legitimate public ``openclaw`` / ``fleet`` surfaces at their allowlisted
    paths PASS;
  * a clean shipped tree PASSES;
  * the ``--dist`` mode extracts an sdist, strips the version prefix, and still
    applies the path-scoped allowlist correctly.

These fixtures intentionally contain the forbidden strings. They live under
``tests/`` (excluded from the delta gate and never shipped in the wheel/sdist),
so they cannot trip either gate themselves.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "scripts" / "release_gate" / "check_release_leaks.py"


def _write(root: Path, relpath: str, content: str) -> None:
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _run_tree(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--tree", str(tree)],
        capture_output=True,
        text=True,
    )


def _run_dist(dist: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--dist", str(dist)],
        capture_output=True,
        text=True,
    )


def test_scanner_exists():
    assert SCANNER.is_file(), f"scanner missing at {SCANNER}"


def test_clean_tree_passes(tmp_path):
    _write(tmp_path, "tokenpak/proxy/server.py", "def serve():\n    return 'ok'\n")
    _write(tmp_path, "README.md", "# TokenPak\n\nCut LLM costs.\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"clean tree should pass:\n{res.stdout}\n{res.stderr}"


def test_untouched_private_path_leak_fails(tmp_path):
    # An untouched file with a private home path — the exact class the delta
    # gate misses when no PR touches the file.
    _write(tmp_path, "tokenpak/proxy/cache.py", 'CACHE_DIR = "/home/sue/.cache"\n')
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "private-path leak must fail the gate"
    assert "tokenpak/proxy/cache.py" in res.stdout


def test_agent_name_leak_fails(tmp_path):
    _write(tmp_path, "tokenpak/core/notes.py", "# last reviewed by Sue\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "agent-name leak must fail the gate"
    assert "tokenpak/core/notes.py" in res.stdout


def test_internal_task_id_leak_fails(tmp_path):
    _write(tmp_path, "tokenpak/core/x.py", "# tracked in TSR-1234\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "internal task-ID leak must fail the gate"


def test_internal_vault_path_leak_fails(tmp_path):
    # Internal vault paths (~/vault/<NN>_<FOLDER>) must not leak into shipped
    # docstrings/comments — the class scrubbed from the package in a recent
    # public-identity hygiene pass.
    _write(
        tmp_path,
        "tokenpak/proxy/notes.py",
        "# spec lives at ~/vault/01_PROJECTS/tokenpak/initiatives/x.md\nX = 1\n",
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "internal vault-path leak must fail the gate"
    assert "tokenpak/proxy/notes.py" in res.stdout


def test_internal_vault_path_various_numbered_folders_fail(tmp_path):
    # Any two-digit numbered top-level vault folder is internal: 00_kevin,
    # 06_RUNTIME, 03_AGENT_PACKS, ... — all caught by the single path pattern.
    for i, ref in enumerate(
        ("~/vault/00_kevin/y.md", "~/vault/06_RUNTIME/scripts/z.sh")
    ):
        _write(tmp_path, f"tokenpak/core/v{i}.py", f"# see {ref}\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "all numbered vault folders must fail the gate"


def test_vault_path_no_false_positive_on_user_surfaces(tmp_path):
    # The pattern is deliberately narrow — it requires the ~/vault/<NN>_ shape.
    # Legitimate surfaces must NOT trip it:
    #   * ~/vault/.tokenpak  -> a dotfile dir, not a numbered folder
    #   * bare ~/vault       -> a generic path with no numbered folder
    #   * bare 01_PROJECTS / 03_AGENT_PACKS (no ~/vault/ prefix) -> keeps the
    #     lesson_ingest vault-schema feature clean WITHOUT needing an allowlist.
    _write(
        tmp_path,
        "tokenpak/companion/memory/lesson_ingest.py",
        "# data dir: ~/vault/.tokenpak\n"
        "VAULT = '~/vault'\n"
        "PACKS = '03_AGENT_PACKS'  # vault-schema folder name (feature)\n"
        "SUB = '01_PROJECTS'\n",
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 0, (
        f"narrow vault pattern must not false-positive:\n{res.stdout}"
    )


def test_allowlisted_openclaw_path_passes(tmp_path):
    # `openclaw` is the legitimate, non-renamable provider/module name in the
    # OpenClaw integration subsystem.
    _write(
        tmp_path,
        "tokenpak/integrations/openclaw/provider.py",
        'NAME = "openclaw"\n\n\ndef connect_openclaw():\n    return NAME\n',
    )
    _write(
        tmp_path,
        "tokenpak/sdk/openclaw.py",
        '"""OpenClaw SDK surface."""\nMODULE = "tokenpak.sdk.openclaw"\n',
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"allowlisted openclaw must pass:\n{res.stdout}"


def test_allowlisted_fleet_path_passes(tmp_path):
    # `fleet` is the user-facing multi-instance-proxy CLI feature here.
    _write(
        tmp_path,
        "tokenpak/cli/fleet.py",
        'def fleet():\n    """Manage the tokenpak fleet."""\n    return True\n',
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"allowlisted fleet must pass:\n{res.stdout}"


def test_bare_openclaw_outside_allowlist_fails(tmp_path):
    # Same token, NON-allowlisted path -> still caught. Proves the allowlist is
    # path-scoped, not a blanket exemption.
    _write(tmp_path, "tokenpak/proxy/misc.py", "# the openclaw agent fleet\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "bare openclaw outside allowlist must fail"


def test_bare_fleet_outside_allowlist_fails(tmp_path):
    _write(tmp_path, "tokenpak/proxy/runtime.py", "# the agent fleet worker loop\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "internal-sense fleet outside allowlist must fail"


def test_top_level_tests_dir_excluded(tmp_path):
    # Mirrors the delta gate: top-level tests/ is a dev surface, not scanned.
    _write(tmp_path, "tests/test_thing.py", "# authored by Sue, see TSR-01\n")
    _write(tmp_path, "tokenpak/proxy/server.py", "OK = True\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"top-level tests/ must be excluded:\n{res.stdout}"


def test_in_package_tests_subpackage_is_scanned(tmp_path):
    # tokenpak/tests/ ships in the wheel and IS scanned (NOT excluded), except
    # at its specific allowlisted paths.
    _write(tmp_path, "tokenpak/tests/test_misc.py", "# the openclaw agent fleet\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "in-package tokenpak/tests/ must be scanned"
    assert "tokenpak/tests/test_misc.py" in res.stdout


def test_build_manifests_excluded(tmp_path):
    # Auto-generated path listings enumerate legitimate file paths and must not
    # false-positive.
    _write(
        tmp_path,
        "tokenpak.egg-info/SOURCES.txt",
        "tokenpak/sdk/openclaw.py\ntokenpak/cli/fleet.py\n",
    )
    _write(
        tmp_path,
        "tokenpak-9.9.9.dist-info/RECORD",
        "tokenpak/sdk/openclaw.py,sha256=abc,100\n",
    )
    _write(tmp_path, "tokenpak/proxy/server.py", "OK = True\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"build manifests must be excluded:\n{res.stdout}"


def _make_sdist(dist_dir: Path, files: dict[str, str], name: str = "tokenpak-9.9.9"):
    """Write a minimal sdist tarball whose members are <name>/<repo-path>."""
    dist_dir.mkdir(parents=True, exist_ok=True)
    tar_path = dist_dir / f"{name}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for relpath, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"{name}/{relpath}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return tar_path


def test_dist_mode_strips_prefix_and_detects_leak(tmp_path):
    dist = tmp_path / "dist"
    _make_sdist(dist, {"tokenpak/proxy/cache.py": 'D = "/home/sue/x"\n'})
    res = _run_dist(dist)
    assert res.returncode == 1, "sdist leak must be detected"
    # path reported repo-relative (version prefix stripped)
    assert "tokenpak/proxy/cache.py" in res.stdout


def test_dist_mode_allowlist_applies_after_prefix_strip(tmp_path):
    dist = tmp_path / "dist"
    _make_sdist(
        dist,
        {
            "tokenpak/integrations/openclaw/provider.py": 'N = "openclaw"\n',
            "README.md": "# TokenPak\n",
        },
    )
    res = _run_dist(dist)
    assert res.returncode == 0, f"allowlist must apply to sdist paths:\n{res.stdout}"


# ──────────────────────────────────────────────────────────────────────────
# Internal standards-citation rules: Std NN / §N / Standards Delta, plus the
# Apache-2.0 §N legal allowlist and the release-gate masking. Matrix mirrors the
# delta gate (identity-language-check.yml); the parity test at the end proves
# the two registers stay in sync.
# ──────────────────────────────────────────────────────────────────────────


def test_std_out_of_range_leak_fails(tmp_path):
    # Std 41 is outside the old Std 2x/3x range and previously shipped.
    _write(tmp_path, "tokenpak/orchestration/x.py", "# per Std 41 dispatch contract\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "Std-number outside 20-39 must now fail"


def test_std_low_leak_fails(tmp_path):
    _write(tmp_path, "tokenpak/core/x.py", "# per Std 02 product constitution\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "low Std number must fail"


def test_section_ref_leak_fails(tmp_path):
    _write(tmp_path, "tokenpak/core/x.py", "# see §4.1 for the lane contract\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "section reference must fail"


def test_standards_delta_leak_fails(tmp_path):
    _write(tmp_path, "tokenpak/core/x.py", "# transcribed from Standards Delta v0\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "Standards Delta reference must fail"


def test_apache_legal_section_passes(tmp_path):
    # The Apache-2.0 §N license-section citation is legitimate legal text and is
    # allowlisted (exact literal). This is the README/long-description trademark
    # notice that must remain public.
    _write(
        tmp_path,
        "README.md",
        "Not licensed under Apache-2.0 (Apache-2.0 §6 grants no trademark rights).\n",
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"Apache-2.0 legal section citation must pass:\n{res.stdout}"


def test_apache_legal_section_multidigit_passes(tmp_path):
    # The allowlist matches the full section number, so a multi-digit / dotted
    # section masks cleanly (no partial-mask leak).
    _write(tmp_path, "README.md", "see Apache-2.0 §10.2 hypothetical\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"multi-digit Apache section must pass:\n{res.stdout}"


def test_legal_section_space_form_passes(tmp_path):
    # A space-form legal citation (e.g. "§ 230") is not the internal no-space
    # form and must not match.
    _write(tmp_path, "docs_pkg/legal.md", "under § 230 of the Act\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"space-form section citation must pass:\n{res.stdout}"


def test_iso_standard_number_passes(tmp_path):
    # "Std 14001" is a long number; \bStd [0-9]{2}\b requires exactly two digits
    # at a word boundary, so it does not match.
    _write(tmp_path, "tokenpak/core/x.py", "# ISO Std 14001 environmental\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"ISO Std 14001 must not false-positive:\n{res.stdout}"


def test_bare_section_sign_passes(tmp_path):
    # A bare section sign with no trailing digit is not the §N citation form.
    _write(tmp_path, "tokenpak/core/x.py", "# the § sign by itself\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"bare section sign must pass:\n{res.stdout}"


def test_relgate_impl_std_section_masked(tmp_path):
    # Release-gate implementation files cite the standards they implement; §N /
    # Std NN there are masked.
    _write(
        tmp_path,
        "scripts/release_gate/foo.py",
        "# Std 30 §7 (R7) release-gate snapshot rule\nX = 1\n",
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"release-gate Std/section citation must pass:\n{res.stdout}"


def test_snapshot_std_section_masked(tmp_path):
    # Snapshot governance metadata (CI step names) cite standards sections.
    _write(
        tmp_path,
        "tokenpak/_snapshots/workflow-steps.json",
        '{"name": "Sdist / wheel purity (Std 30 §13.1 R9)"}\n',
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"snapshot Std/section citation must pass:\n{res.stdout}"


def test_section_outside_relgate_paths_fails(tmp_path):
    # The release-gate mask is path-scoped: the SAME §N on a normal shipped path
    # is still caught.
    _write(tmp_path, "tokenpak/proxy/x.py", "# internal §7 note\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "section ref outside release-gate paths must fail"


def test_agent_name_still_fails_on_relgate_path(tmp_path):
    # The release-gate mask covers ONLY §N / Std NN; an agent name on the same
    # path is still caught.
    _write(tmp_path, "scripts/release_gate/foo.py", "# last reviewed by Suki\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "agent name must still fail on a release-gate path"
    assert "scripts/release_gate/foo.py" in res.stdout


def test_private_path_still_fails_on_relgate_path(tmp_path):
    _write(tmp_path, "scripts/release_gate/foo.py", 'CACHE = "/home/sue/.cache"\n')
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "private path must still fail on a release-gate path"


def test_task_id_still_fails_on_relgate_path(tmp_path):
    _write(tmp_path, "scripts/release_gate/foo.py", "# tracked in TSR-1234\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "task ID must still fail on a release-gate path"


def test_pattern_register_parity_with_delta_gate():
    # Mechanical parity: the full-tree register and the delta gate
    # (identity-language-check.yml) MUST carry the identical forbidden-pattern set
    # (SYNC OBLIGATION). Guards against the two scanners drifting.
    import re as _re

    scanner_src = SCANNER.read_text(encoding="utf-8")
    # resolve the named-constant entries to their regex literals
    consts = dict(_re.findall(r'^([A-Z_]+) = (r?"[^"]*")', scanner_src, _re.M))
    py_entries = []
    block = _re.search(r"PATTERNS: list\[str\] = \[(.*?)\n\]", scanner_src, _re.S).group(1)
    for tok in _re.findall(r"(r\"[^\"]*\"|[A-Z_]+)", block):
        if tok in consts:
            py_entries.append(consts[tok])
        elif tok.startswith('r"'):
            py_entries.append(tok)
    py_set = {eval(e) for e in py_entries}  # noqa: S307 - test-only literal eval

    yml = (REPO_ROOT / ".github" / "workflows" / "identity-language-check.yml").read_text(
        encoding="utf-8"
    )
    yblock = _re.search(r"patterns=\((.*?)\n\s*\)", yml, _re.S).group(1)
    y_set = set(_re.findall(r"'([^']*)'", yblock))

    assert py_set == y_set, (
        "scanner register and delta gate drifted:\n"
        f"  only in scanner: {py_set - y_set}\n"
        f"  only in delta gate: {y_set - py_set}"
    )
