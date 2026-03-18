"""Intent-driven few-shot example selection.

Selects intent-specific examples from ~/.tokenpak/examples when they are likely to
help and when token budget allows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SelectedExample:
    name: str
    content: str
    token_estimate: int


@dataclass(frozen=True)
class ExampleSelection:
    intent: str
    selected: tuple[SelectedExample, ...]
    used_tokens: int
    skipped_reason: str = ""


DEFAULT_CONFIG: dict[str, Any] = {
    "defaults": {
        "enabled": False,
        "max_examples": 0,
        "min_remaining_tokens": 500,
    },
    "intents": {
        "classify": {"enabled": True, "max_examples": 2, "min_remaining_tokens": 700},
        "extraction": {"enabled": True, "max_examples": 2, "min_remaining_tokens": 900},
        "translation": {"enabled": False, "max_examples": 0},
    },
}


class IntentExampleSelector:
    def __init__(
        self,
        *,
        examples_root: str | Path = "~/.tokenpak/examples",
        config_path: str | Path = "~/.tokenpak/examples/config.yaml",
    ) -> None:
        self.examples_root = Path(examples_root).expanduser()
        self.config_path = Path(config_path).expanduser()

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return DEFAULT_CONFIG
        try:
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return DEFAULT_CONFIG

    def _intent_cfg(self, intent: str) -> dict[str, Any]:
        cfg = self._load_config()
        defaults = cfg.get("defaults", {}) if isinstance(cfg, dict) else {}
        intents = cfg.get("intents", {}) if isinstance(cfg, dict) else {}
        intent_cfg = intents.get(intent, {}) if isinstance(intents, dict) else {}
        merged = dict(defaults)
        if isinstance(intent_cfg, dict):
            merged.update(intent_cfg)
        return merged

    def _candidate_files(self, intent: str, cfg: dict[str, Any]) -> list[Path]:
        intent_dir = self.examples_root / intent
        if not intent_dir.exists() or not intent_dir.is_dir():
            return []

        configured_files = cfg.get("files", [])
        if isinstance(configured_files, list) and configured_files:
            selected: list[Path] = []
            for name in configured_files:
                p = intent_dir / str(name)
                if p.exists() and p.is_file():
                    selected.append(p)
            return selected

        return sorted([p for p in intent_dir.glob("*.md") if p.is_file()])

    def select(
        self,
        *,
        intent: str,
        token_budget: int,
        reserved_tokens: int = 0,
    ) -> ExampleSelection:
        cfg = self._intent_cfg(intent)
        if not bool(cfg.get("enabled", False)):
            return ExampleSelection(intent=intent, selected=(), used_tokens=0, skipped_reason="intent_disabled")

        remaining = token_budget - reserved_tokens
        min_remaining = int(cfg.get("min_remaining_tokens", 500))
        if remaining < min_remaining:
            return ExampleSelection(
                intent=intent,
                selected=(),
                used_tokens=0,
                skipped_reason=f"tight_budget(remaining={remaining},min={min_remaining})",
            )

        candidates = self._candidate_files(intent, cfg)
        if not candidates:
            return ExampleSelection(intent=intent, selected=(), used_tokens=0, skipped_reason="no_examples")

        max_examples = max(0, int(cfg.get("max_examples", 0)))
        if max_examples == 0:
            return ExampleSelection(intent=intent, selected=(), used_tokens=0, skipped_reason="max_examples_zero")

        selected: list[SelectedExample] = []
        used = 0
        for p in candidates:
            if len(selected) >= max_examples:
                break
            content = p.read_text(encoding="utf-8").strip()
            if not content:
                continue
            est = self._estimate_tokens(content)
            if used + est > remaining:
                continue
            selected.append(SelectedExample(name=p.name, content=content, token_estimate=est))
            used += est

        return ExampleSelection(intent=intent, selected=tuple(selected), used_tokens=used)
