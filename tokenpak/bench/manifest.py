# SPDX-License-Identifier: Apache-2.0
"""Fixture manifest verification.

The `--quick` runner refuses to start if any fixture's SHA-256 has drifted
from the manifest. Modifying a fixture in place without bumping the suite
version is a methodology violation per Standard 24 §6.3.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent / "tests" / "benchmarks"
FIXTURES_DIR = BENCH_ROOT / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "MANIFEST.json"
SUITE_VERSION_PATH = BENCH_ROOT / "SUITE_VERSION"


@dataclass(frozen=True)
class FixtureSet:
    suite_version: str
    fixtures_dir: Path
    files: dict[str, str]  # name -> sha256


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_and_verify() -> FixtureSet:
    """Load MANIFEST.json and verify every listed fixture's hash matches.

    Raises RuntimeError on any drift.
    """
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest missing: {MANIFEST_PATH}")
    if not SUITE_VERSION_PATH.exists():
        raise RuntimeError(f"SUITE_VERSION missing: {SUITE_VERSION_PATH}")

    manifest = json.loads(MANIFEST_PATH.read_text())
    suite_version = SUITE_VERSION_PATH.read_text().strip()

    if manifest.get("suite_version") != suite_version:
        raise RuntimeError(
            f"manifest suite_version {manifest.get('suite_version')!r} "
            f"!= SUITE_VERSION file {suite_version!r}"
        )

    drift: list[str] = []
    for name, expected in manifest["fixtures"].items():
        path = FIXTURES_DIR / name
        if not path.exists():
            drift.append(f"  {name}: missing")
            continue
        actual = _sha256(path)
        if actual != expected["sha256"]:
            drift.append(
                f"  {name}: sha256 drift\n"
                f"    expected: {expected['sha256']}\n"
                f"    actual:   {actual}"
            )

    if drift:
        msg = (
            "fixture drift detected — refusing to run benchmark.\n"
            "modifying a fixture in place without bumping the suite version is a\n"
            "methodology violation per Standard 24 §6.3.\n\n"
            + "\n".join(drift)
        )
        raise RuntimeError(msg)

    return FixtureSet(
        suite_version=suite_version,
        fixtures_dir=FIXTURES_DIR,
        files={k: v["sha256"] for k, v in manifest["fixtures"].items()},
    )


def fixture_path(name: str) -> Path:
    p = FIXTURES_DIR / name
    if not p.exists():
        raise RuntimeError(f"fixture not found: {p}")
    return p
