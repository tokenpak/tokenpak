"""Conftest for agentic tests — adds local module path so 'workflow' and
'workflow_performance' can be imported directly (they live in this directory)."""

import sys
from pathlib import Path

_here = Path(__file__).parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
