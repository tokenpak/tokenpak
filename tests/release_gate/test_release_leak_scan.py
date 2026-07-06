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


def test_companion_guide_permission_tier_fleet_passes(tmp_path):
    _write(
        tmp_path,
        "tokenpak/companion/GUIDE.md",
        "\n".join(
            [
                "Runtime unattended bypass uses launcher *fleet mode*.",
                "Client config files are never modified by fleet mode.",
                "| `fleet` | persistent tier unchanged |",
                "tokenpak permissions show  # current tiers + fleet mode",
                "tokenpak permissions set fleet  # launcher fleet mode",
                "tokenpak permissions reset  # scoped reset + fleet off",
                "Fleet launches print a mandatory stderr banner.",
                "The env var remains the back-compat alias of fleet mode.",
                "",
            ]
        ),
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"companion guide permission-tier fleet usage must pass:\n{res.stdout}"


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
# Std / § / Standards-Delta register extension (leak-gate Std/§ scanner
# extension, 2026-06-28). Closes the gap that let internal ``Std NN`` (outside
# 20-39), no-space ``§N`` section refs, and the ``Standards Delta`` artifact
# name ship in the v1.10.0 wheel. Each case also implicitly checks the
# explicit per-surface cite-list masks (un-cited Std/§ must still trip) and the
# false-positive safety against the legal corpus.
# ──────────────────────────────────────────────────────────────────────────


def test_std_out_of_range_leak_fails(tmp_path):
    # Std 41 is outside the old 20-39 window AND carries a §16 section ref —
    # exactly the form that shipped in v1.10.0.
    _write(tmp_path, "tokenpak/core/x.py", "# per Std 41 §16 dispatch contract\nX = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "Std-out-of-range + section leak must fail"


def test_std_low_number_leak_fails(tmp_path):
    _write(tmp_path, "tokenpak/core/y.py", "# product constitution per Std 02\nY = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "low Std number leak must fail"


def test_section_ref_leak_fails(tmp_path):
    _write(tmp_path, "tokenpak/core/z.py", "# see §4.1 for the rule\nZ = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "bare section-ref leak must fail"


def test_standards_delta_leak_fails(tmp_path):
    _write(tmp_path, "tokenpak/core/d.py", "# transcribed from Standards Delta v0\nD = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "Standards Delta artifact-name leak must fail"


def test_fp_crypto_curve_passes(tmp_path):
    # NIST curve names contain "P-256" / "P-384" — must NOT match §/Std.
    _write(tmp_path, "tokenpak/crypto/keys.py", "# uses NIST P-256 and P-384 curves\nK = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"crypto curve names must pass:\n{res.stdout}"


def test_fp_iso_std_passes(tmp_path):
    # "Std 14001" is a 5-digit ISO number, not a 2-digit \bStd NN\b citation.
    _write(tmp_path, "tokenpak/quality/iso.py", "# conforms to ISO Std 14001\nI = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"ISO Std 14001 must pass:\n{res.stdout}"


def test_fp_legal_section_word_passes(tmp_path):
    _write(tmp_path, "tokenpak/legal/dmca.py", "# safe harbor under section 230\nL = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"legal 'section 230' prose must pass:\n{res.stdout}"


def test_fp_legal_section_sign_citation_passes(tmp_path):
    # The internal form is uniformly no-space ("§N"); a legal citation uses a
    # SPACE ("§ 512"), so the no-space §[0-9] pattern structurally cannot hit it.
    _write(tmp_path, "tokenpak/legal/cite.py", "# see 17 U.S.C. § 512(c)\nC = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"legal '§ 512' (space form) must pass:\n{res.stdout}"


def test_fp_bare_section_sign_passes(tmp_path):
    _write(tmp_path, "tokenpak/docs/sym.py", "# the § sign denotes a section\nS = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"bare § (no digit) must pass:\n{res.stdout}"


def test_relgate_impl_cited_std_section_passes(tmp_path):
    # scripts/release_gate/** legitimately implements Std 30 and cites §7.
    _write(tmp_path, "scripts/release_gate/foo.py", "# implements Std 30 §7 R7\nF = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"cited Std/§ on impl path must pass:\n{res.stdout}"


def test_relgate_impl_sentence_period_citation_passes(tmp_path):
    # A cited section followed by a sentence period ("§13.3.") must NOT
    # self-fail the gate.
    _write(tmp_path, "scripts/release_gate/bar.py", "# governed by Std 30 §13.3.\nB = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"sentence-period citation must pass:\n{res.stdout}"


def test_relgate_impl_uncited_std_still_fails(tmp_path):
    # Std 99 is NOT on the impl cite-list — the mask is explicit, not blanket.
    _write(tmp_path, "scripts/release_gate/baz.py", "# bogus Std 99 reference\nZ = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "un-cited Std on impl path must still fail"


def test_relgate_impl_uncited_subsection_still_fails(tmp_path):
    # §13 and §13.1-.4 are cited; §13.5 is NOT — the boundary keeps it tripping.
    _write(tmp_path, "scripts/release_gate/qux.py", "# stray §13.5 ref\nQ = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "un-cited sub-section on impl path must still fail"


def test_snapshot_cited_section_passes(tmp_path):
    # Generated snapshot metadata legitimately cites §7 (R7).
    _write(tmp_path, "tokenpak/_snapshots/x.json", '{"note": "api-snapshot-check §7 R7"}\n')
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"cited §7 in snapshot must pass:\n{res.stdout}"


def test_snapshot_uncited_section_still_fails(tmp_path):
    # §4 is NOT on the snapshot cite-list ({7, 13.3}) — must still trip.
    _write(tmp_path, "tokenpak/_snapshots/y.json", '{"note": "stray §4 ref"}\n')
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "un-cited §4 in snapshot must still fail"


def test_non_relgate_path_cited_section_still_fails(tmp_path):
    # The same §7 that is masked on a release-gate path is a leak elsewhere:
    # proves the cite-list is path-scoped, not global.
    _write(tmp_path, "tokenpak/proxy/leak.py", "# transcribed §7 note\nP = 1\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "§7 outside release-gate paths must still fail"


def test_fp_license_section_citation_passes(tmp_path):
    # Preserved legal/trademark text: the README trademark clause cites the
    # license section with a NO-SPACE "§6" ("Apache-2.0 §6 grants no trademark
    # rights") — the §[0-9] pattern matches it, so the exact Apache-2.0 §N
    # allowlist must let it pass. (The space-form "§ 512" FP test does not cover
    # this.)
    _write(
        tmp_path,
        "tokenpak/_meta/readme_snippet.md",
        "Brand assets are not licensed under Apache-2.0 (Apache-2.0 §6 grants "
        "no trademark rights).\n",
    )
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"Apache-2.0 license-section citation must pass:\n{res.stdout}"


def test_apache_license_multidigit_section_passes(tmp_path):
    # The allowlist matches the full section number, so a multi-digit / dotted
    # section masks cleanly (no partial-mask leak).
    _write(tmp_path, "tokenpak/_meta/readme_snippet.md", "see Apache-2.0 §10.2 hypothetical\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 0, f"multi-digit Apache section must pass:\n{res.stdout}"


def test_license_carveout_requires_apache_prefix(tmp_path):
    # The allowlist is narrow: a bare internal "§6" with no Apache-2.0 prefix is
    # still a leak and must still trip.
    _write(tmp_path, "tokenpak/_meta/note.md", "internal rule per §6 of the spec\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "bare §6 (no Apache-2.0 prefix) must still fail"


def test_non_apache_license_section_fails(tmp_path):
    # Scope ruling (2026-06-28): Apache-2.0 ONLY. The generic "<license-id> §N"
    # SPDX carve-out is NOT used, so a non-Apache license-section citation (e.g.
    # "MIT §6") is NOT allowlisted and still trips the §[0-9] rule. (Widening to
    # other SPDX identifiers is a separate Suki/Kevin policy decision.)
    _write(tmp_path, "tokenpak/_meta/note.md", "see MIT §6 for the clause\n")
    res = _run_tree(tmp_path)
    assert res.returncode == 1, "non-Apache license-section citation must fail (Apache-only allowlist)"


def test_pattern_register_parity_with_delta_gate():
    # Mechanical SYNC OBLIGATION check: the full-tree register (PATTERNS in
    # check_release_leaks.py) and the per-PR delta gate
    # (identity-language-check.yml `patterns=( ... )`) MUST carry the identical
    # forbidden-pattern set. Guards against the two scanners drifting. The YAML
    # extraction reads ONLY array-ENTRY lines (a lone quoted string), skipping
    # comment lines inside the block.
    import importlib.util
    import re as _re

    spec = importlib.util.spec_from_file_location("_crl_parity", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_crl_parity"] = mod
    spec.loader.exec_module(mod)
    py_set = set(mod.PATTERNS)

    yml = (REPO_ROOT / ".github" / "workflows" / "identity-language-check.yml").read_text(
        encoding="utf-8"
    )
    block = _re.search(r"patterns=\((.*?)\n\s*\)", yml, _re.S).group(1)
    y_set = set()
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        m = _re.fullmatch(r"'([^']*)'", stripped)  # entry line = a lone quoted string
        if m:
            y_set.add(m.group(1))

    assert py_set == y_set, (
        "scanner register and delta gate drifted:\n"
        f"  only in scanner: {py_set - y_set}\n"
        f"  only in delta gate: {y_set - py_set}"
    )



# Vault-path leak pattern (preserved from public PR #251 — must not regress).
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
