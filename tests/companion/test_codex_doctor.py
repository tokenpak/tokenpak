# SPDX-License-Identifier: Apache-2.0
"""Doctor WARN tier + Std 56 skills-path checks (regression-repair packet).

Scope note: this pins the *in-scope* doctor surface restored by the
regression repair — the skills-installed check on the canonical
``$HOME/.agents/skills`` location, the legacy-orphan WARN row, and the
runner's PASS/WARN/FAIL normalization where WARN never fails the exit
code.  It deliberately does not exercise the broader ``codex features``
real-state probes: those depend on a ``tests/fixtures/codex`` sample that
is outside this packet's ``expected_files_changed`` scope.
"""
from __future__ import annotations

from pathlib import Path

from tokenpak.companion.codex import doctor
from tokenpak.companion.codex import skills_installer as si


def _fake_bundled(tmp_path: Path, names: tuple[str, ...] = ("alpha", "beta")) -> Path:
    bundled = tmp_path / "bundled"
    for name in names:
        (bundled / name).mkdir(parents=True)
        (bundled / name / "SKILL.md").write_text(f"# {name}\n")
    return bundled


# ── status normalization ──────────────────────────────────────────────


def test_status_of_binary_and_warn():
    assert doctor._status_of(True) == "PASS"
    assert doctor._status_of(False) == "FAIL"
    assert doctor._status_of(doctor._WARN) == "WARN"


# ── skills-installed check targets the canonical .agents/skills path ───


def test_check_skills_installed_uses_agents_not_codex_path(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(si, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))

    # Canonical target absent → FAIL, and the detail names the .agents path.
    ok, detail = doctor.check_skills_installed()
    assert ok is False
    assert ".agents" in detail
    assert ".codex/skills" not in detail

    # Install into the canonical path → PASS.
    si.install_skills(target_dir=home / ".agents" / "skills")
    ok, detail = doctor.check_skills_installed()
    assert ok is True


# ── legacy-orphan WARN row ─────────────────────────────────────────────


def test_legacy_orphans_pass_when_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(si, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    monkeypatch.setattr(si, "_LEGACY_TARGET", tmp_path / "codex" / "skills")
    raw, detail = doctor._check_skills_legacy_orphans()
    assert raw is True
    assert doctor._status_of(raw) == "PASS"


def test_legacy_orphans_warn_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(si, "_BUNDLED_SKILLS", _fake_bundled(tmp_path))
    legacy = tmp_path / "codex" / "skills"
    monkeypatch.setattr(si, "_LEGACY_TARGET", legacy)
    si.install_skills(target_dir=legacy)

    raw, detail = doctor._check_skills_legacy_orphans()
    assert raw == doctor._WARN
    assert doctor._status_of(raw) == "WARN"
    assert "~/.codex/skills" in detail


# ── runner treats WARN as advisory, FAIL as fatal ──────────────────────


def test_run_warn_does_not_fail_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "CHECKS",
        [
            ("all good", lambda: (True, "ok")),
            ("advisory", lambda: (doctor._WARN, "heads up")),
        ],
    )
    rc = doctor.run()
    out = capsys.readouterr().out
    assert rc == 0
    assert "[WARN]" in out
    assert "1 warning" in out


def test_run_fail_sets_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "CHECKS",
        [
            ("all good", lambda: (True, "ok")),
            ("advisory", lambda: (doctor._WARN, "heads up")),
            ("broken", lambda: (False, "nope")),
        ],
    )
    rc = doctor.run()
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL]" in out
    assert "1 failed" in out


def test_run_exception_in_check_is_fail(monkeypatch, capsys):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "CHECKS", [("x", _boom)])
    rc = doctor.run()
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL]" in out
    assert "kaboom" in out
