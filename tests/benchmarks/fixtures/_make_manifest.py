#!/usr/bin/env python3
"""Regenerate MANIFEST.json with SHA-256 hashes of every fixture in this directory.

Run only when (a) bumping bench-suite MINOR/MAJOR to add fixtures, or (b)
introducing the manifest for the first time. The manifest is treated as
canonical at runtime; modifying a fixture without regenerating + bumping the
suite version is a methodology violation.

See ~/vault/01_PROJECTS/tokenpak/standards/24-benchmarking-standard.md §6.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE_VERSION = (HERE.parent / "SUITE_VERSION").read_text().strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    files = sorted(p for p in HERE.iterdir() if p.is_file() and p.name not in {"MANIFEST.json", "_make_manifest.py"})
    manifest = {
        "suite_version": SUITE_VERSION,
        "fixtures": {p.name: {"sha256": sha256(p), "size_bytes": p.stat().st_size} for p in files},
    }
    out = HERE / "MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(manifest['fixtures'])} fixtures)")


if __name__ == "__main__":
    main()
