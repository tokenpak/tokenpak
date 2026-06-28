#!/usr/bin/env python3
"""Assert the published package does not include development-only artifacts."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_BUILD_DIRS = (
    REPO_ROOT / "build",
    REPO_ROOT / "tokenpak.egg-info",
)
REQUIRED_PACKAGE_DATA = {
    "tokenpak/budget_config.yaml",
    "tokenpak/term_cards.json",
}
# Dispatch v0.1-alpha registry/schema package data declared in pyproject.toml
# [tool.setuptools.package-data]. The registry/routes/overlays directories are
# not Python packages (no __init__.py), so their data ships only as package-data
# globs of the parent ``tokenpak`` package. Each glob below MUST match at least
# one shipped file in both wheel and sdist — proving the declared globs are live
# and the Dispatch registry/schema files actually graduate into built artifacts.
REQUIRED_DISPATCH_DATA_GLOBS = (
    "tokenpak/orchestration/dispatch/registry/*.yaml",
    "tokenpak/orchestration/dispatch/registry/routes/*.yaml",
    "tokenpak/orchestration/dispatch/registry/overlays/*.yaml",
    "tokenpak/orchestration/dispatch/schemas/*.json",
)
PACKAGE_TEST_PATH_RE = re.compile(r"^tokenpak/(?:tests/|.+/tests/)")
BYTECODE_PATH_RE = re.compile(r"(^|/)__pycache__/|\.py[cod]$")


def _remove_generated_build_dirs() -> None:
    for path in GENERATED_BUILD_DIRS:
        if path.exists():
            shutil.rmtree(path)


def _run_build(outdir: Path) -> None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(outdir),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def _single_artifact(outdir: Path, pattern: str) -> Path:
    matches = sorted(outdir.glob(pattern))
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {pattern} artifact, found {len(matches)}")
    return matches[0]


def _wheel_names(wheel: Path) -> set[str]:
    with zipfile.ZipFile(wheel) as archive:
        return set(archive.namelist())


def _sdist_names(sdist: Path) -> set[str]:
    with tarfile.open(sdist, "r:gz") as archive:
        names = set()
        for member in archive.getmembers():
            name = member.name
            if "/" in name:
                name = name.split("/", 1)[1]
            names.add(name)
        return names


def _assert_required_data(names: set[str], artifact_label: str) -> None:
    missing = sorted(REQUIRED_PACKAGE_DATA - names)
    if missing:
        raise AssertionError(
            f"{artifact_label} is missing required package data: {', '.join(missing)}"
        )


def _matches_glob(name: str, glob: str) -> bool:
    """Match a posix archive path against a single-segment ``*`` glob.

    Unlike :func:`fnmatch.fnmatch`, the ``*`` here does not cross ``/``: the
    directory portion must match exactly and only the basename is wildcarded.
    This keeps ``registry/*.yaml`` from being satisfied by a file that actually
    lives in ``registry/routes/``.
    """
    glob_dir, _, glob_base = glob.rpartition("/")
    name_dir, _, name_base = name.rpartition("/")
    return name_dir == glob_dir and fnmatch.fnmatch(name_base, glob_base)


def _assert_required_dispatch_data(names: set[str], artifact_label: str) -> None:
    missing = [
        glob
        for glob in REQUIRED_DISPATCH_DATA_GLOBS
        if not any(_matches_glob(name, glob) for name in names)
    ]
    if missing:
        raise AssertionError(
            f"{artifact_label} is missing required Dispatch package data "
            f"(no shipped file matches): {', '.join(missing)}"
        )


def _assert_no_development_artifacts(names: set[str], artifact_label: str) -> None:
    offenders = sorted(
        name
        for name in names
        if name.startswith("tests/")
        or PACKAGE_TEST_PATH_RE.search(name)
        or BYTECODE_PATH_RE.search(name)
    )
    if offenders:
        preview = "\n".join(f"  - {name}" for name in offenders[:20])
        extra = "" if len(offenders) <= 20 else f"\n  ... and {len(offenders) - 20} more"
        raise AssertionError(
            f"{artifact_label} includes development-only artifacts:\n{preview}{extra}"
        )


def main() -> int:
    _remove_generated_build_dirs()
    try:
        with tempfile.TemporaryDirectory(prefix="tokenpak-dist-check-") as tmp:
            outdir = Path(tmp)
            _run_build(outdir)

            wheel_names = _wheel_names(_single_artifact(outdir, "*.whl"))
            sdist_names = _sdist_names(_single_artifact(outdir, "*.tar.gz"))
    finally:
        _remove_generated_build_dirs()

    _assert_required_data(wheel_names, "wheel")
    _assert_required_data(sdist_names, "sdist")
    _assert_required_dispatch_data(wheel_names, "wheel")
    _assert_required_dispatch_data(sdist_names, "sdist")
    _assert_no_development_artifacts(wheel_names, "wheel")
    _assert_no_development_artifacts(sdist_names, "sdist")
    print("distribution contents are clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
