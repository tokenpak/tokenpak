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

from tokenpak.companion.launcher._impl import (
    _write_mcp_json as _write_mcp_json_impl,
)
from tokenpak.companion.launcher._impl import (
    _write_settings_json as _write_settings_json_impl,
)
from tokenpak.companion.launcher._impl import (
    _write_system_prompt as _write_system_prompt_impl,
)

from ._impl import launch, launch_codex


def regenerate_config() -> dict:
    """Rewrite companion config files (settings.json, mcp.json, system
    prompt) from the current ``CompanionConfig.from_env()``.

    Used by ``tokenpak integrate claude-code``. Returns the paths of
    the files written so the caller can surface them.
    """
    from pathlib import Path

    from tokenpak.companion.config import CompanionConfig

    cfg = CompanionConfig.from_env()
    run_dir = Path(cfg.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "mcp": _write_mcp_json_impl(cfg, run_dir),
        "settings": _write_settings_json_impl(cfg, run_dir),
    }
    prompt = _write_system_prompt_impl(cfg, run_dir)
    if prompt is not None:
        paths["system_prompt"] = prompt
    return paths


__all__ = ["launch", "launch_codex", "regenerate_config"]
