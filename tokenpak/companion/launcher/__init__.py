"""Companion launcher — package form.

Backwards-compatible with the historical ``companion/launcher.py`` file.
All public names are re-exported from ``_impl`` so existing imports
(``from tokenpak.companion.launcher import launch``) keep working.

Phase 2 reshape per DECISION-P2-03: file → package with backwards-compat
re-export. The reshape gives launcher its own package home for future
growth (per-platform launch strategies, shared config helpers) without
breaking any existing caller.
"""

from __future__ import annotations

from ._impl import launch

__all__ = ["launch"]
