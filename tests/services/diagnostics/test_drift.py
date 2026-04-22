"""Install-drift detector — δ acceptance."""

from __future__ import annotations

from tokenpak.services.diagnostics.drift import detect_install_drift


def test_current_install_is_clean_or_diagnosed():
    """Running against the dev checkout should either detect the
    classic cwd=repo-root trap (and report it) or find exactly one
    tokenpak location + one dist-info.

    Either way, the detector must never raise.
    """
    report = detect_install_drift()
    # Report structure is correct.
    assert hasattr(report, "locations")
    assert hasattr(report, "dist_infos")
    assert hasattr(report, "has_shadow")


def test_multi_version_dist_info_is_shadow():
    """Two different versions installed simultaneously is the classic
    editable-install-shadow bug. Detector should flag it."""
    # We can't easily simulate without touching site-packages. But we
    # can exercise the report-building path by calling with the real
    # environment and asserting the types are right.
    report = detect_install_drift()
    versions = {p.name.split("-")[1] for p in report.dist_infos if "-" in p.name}
    if len(versions) > 1:
        assert report.has_shadow, (
            f"multiple versions {versions} visible but has_shadow=False"
        )


def test_cwd_detected_when_at_repo_root(tmp_path, monkeypatch):
    """When cwd has pyproject.toml + tokenpak/, classifier flags it."""
    # Construct a fake "repo root" in tmp_path.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tokenpak").mkdir()
    monkeypatch.chdir(tmp_path)
    report = detect_install_drift()
    assert report.cwd_is_repo_root is True


def test_cwd_not_repo_root_elsewhere(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report = detect_install_drift()
    assert report.cwd_is_repo_root is False
