"""
conftest.py — benchmark path setup.

Ensures ~/Projects/tokenpak is on sys.path BEFORE any test is collected,
so the tokenpak package used is the one with the recipes/ directory.
"""

import sys
from pathlib import Path

# Canonical tokenpak source with recipes/ directory
PROJECTS_TOKENPAK = Path("~/Projects/tokenpak").expanduser()

if PROJECTS_TOKENPAK.exists():
    _path = str(PROJECTS_TOKENPAK)
    if _path not in sys.path:
        sys.path.insert(0, _path)
