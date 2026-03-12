"""Instruction lookup table compression for repeated long instruction blocks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


TAG_PREFIX = "[INSTRUCTION:"
TAG_SUFFIX = "]"


@dataclass
class InstructionCompressionStats:
    replacements_by_id: dict[str, int] = field(default_factory=dict)
    tokens_saved_by_id: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens_saved(self) -> int:
        return sum(self.tokens_saved_by_id.values())


class InstructionTable:
    def __init__(
        self,
        path: str | Path | None = None,
        min_tokens: int = 100,
        min_occurrences: int = 2,
        manual_entries: dict[str, str] | None = None,
    ) -> None:
        self.path = Path(path or "~/.tokenpak/instruction_table.json").expanduser()
        self.min_tokens = max(1, int(min_tokens))
        self.min_occurrences = max(2, int(min_occurrences))
        self.data = self._load()
        if manual_entries:
            self._apply_manual_entries(manual_entries)

    def compress_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        context_budget_tight: bool = True,
        persist: bool = True,
    ) -> tuple[list[dict[str, Any]], InstructionCompressionStats]:
        out = [dict(m) for m in messages]
        stats = InstructionCompressionStats()

        # Observe blocks first so IDs can be assigned deterministically.
        for msg in out:
            self._observe_message(msg)

        # If budget is not tight, leave content expanded.
        if not context_budget_tight:
            if persist:
                self._save()
            return out, stats

        for idx, msg in enumerate(out):
            out[idx] = self._compress_message(msg, stats)

        if persist:
            self._save()
        return out, stats

    def expand_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = [dict(m) for m in messages]
        entries = self.data.get("entries", {})
        for i, msg in enumerate(out):
            content = msg.get("content")
            if isinstance(content, str):
                out[i]["content"] = self._expand_text(content, entries)
            elif isinstance(content, list):
                parts: list[Any] = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part2 = dict(part)
                        part2["text"] = self._expand_text(part2["text"], entries)
                        parts.append(part2)
                    else:
                        parts.append(part)
                out[i]["content"] = parts
        return out

    # ------------------------- internals -------------------------

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "next_id": 1, "entries": {}, "observed": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw.setdefault("version", 1)
                raw.setdefault("next_id", 1)
                raw.setdefault("entries", {})
                raw.setdefault("observed", {})
                return raw
        except Exception:
            pass
        return {"version": 1, "next_id": 1, "entries": {}, "observed": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _apply_manual_entries(self, manual_entries: dict[str, str]) -> None:
        entries = self.data.setdefault("entries", {})
        observed = self.data.setdefault("observed", {})
        for instruction_id, text in manual_entries.items():
            if not isinstance(text, str) or not text.strip():
                continue
            token_est = _estimate_tokens(text)
            h = _hash_text(text)
            entries[instruction_id] = {"text": text, "tokens": token_est, "seen_count": max(2, self.min_occurrences)}
            observed[h] = {
                "text": text,
                "tokens": token_est,
                "seen_count": max(2, self.min_occurrences),
                "id": instruction_id,
            }

    def _observe_message(self, msg: dict[str, Any]) -> None:
        content = msg.get("content")
        if isinstance(content, str):
            self._observe_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    self._observe_text(part["text"])

    def _observe_text(self, text: str) -> None:
        if not text or not text.strip():
            return
        tokens = _estimate_tokens(text)
        if tokens < self.min_tokens:
            return
        h = _hash_text(text)
        observed = self.data.setdefault("observed", {})
        item = observed.get(h)
        if not item:
            observed[h] = {"text": text, "tokens": tokens, "seen_count": 1, "id": None}
            return

        item["seen_count"] = int(item.get("seen_count", 0)) + 1
        if item.get("id"):
            return

        if item["seen_count"] >= self.min_occurrences:
            instruction_id = self._allocate_id()
            item["id"] = instruction_id
            self.data.setdefault("entries", {})[instruction_id] = {
                "text": item["text"],
                "tokens": item["tokens"],
                "seen_count": item["seen_count"],
            }

    def _compress_message(
        self,
        msg: dict[str, Any],
        stats: InstructionCompressionStats,
    ) -> dict[str, Any]:
        out = dict(msg)
        content = msg.get("content")
        if isinstance(content, str):
            out["content"] = self._compress_text(content, stats)
        elif isinstance(content, list):
            parts: list[Any] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part2 = dict(part)
                    part2["text"] = self._compress_text(part2["text"], stats)
                    parts.append(part2)
                else:
                    parts.append(part)
            out["content"] = parts
        return out

    def _compress_text(self, text: str, stats: InstructionCompressionStats) -> str:
        h = _hash_text(text)
        observed = self.data.get("observed", {})
        item = observed.get(h)
        if not item or not item.get("id"):
            return text
        instruction_id = str(item["id"])
        tag = f"{TAG_PREFIX}{instruction_id}{TAG_SUFFIX}"

        original_tokens = _estimate_tokens(text)
        tag_tokens = _estimate_tokens(tag)
        saved = max(0, original_tokens - tag_tokens)

        stats.replacements_by_id[instruction_id] = stats.replacements_by_id.get(instruction_id, 0) + 1
        stats.tokens_saved_by_id[instruction_id] = stats.tokens_saved_by_id.get(instruction_id, 0) + saved
        return tag

    def _expand_text(self, text: str, entries: dict[str, Any]) -> str:
        if not text.startswith(TAG_PREFIX) or not text.endswith(TAG_SUFFIX):
            return text
        instruction_id = text[len(TAG_PREFIX) : -len(TAG_SUFFIX)]
        entry = entries.get(instruction_id)
        if not isinstance(entry, dict):
            return text
        full_text = entry.get("text")
        return full_text if isinstance(full_text, str) else text

    def _allocate_id(self) -> str:
        next_id = int(self.data.get("next_id", 1))
        instruction_id = f"POLICY_{next_id:02d}"
        self.data["next_id"] = next_id + 1
        return instruction_id


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
