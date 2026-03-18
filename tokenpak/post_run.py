# SPDX-License-Identifier: MIT
"""Post-run learning + writeback helpers.

Closed-loop utilities to:
- log post-run metadata
- snapshot large model outputs as artifacts
- index artifacts for retrieval
- apply adaptive retrieval boosts ("need file X")
- cache retrieval results for short iteration loops
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


_NEED_CONTEXT_PATTERNS = (
    "need more context",
    "need additional context",
    "missing context",
    "need file",
    "please provide",
)

_NEED_FILE_REGEX = re.compile(
    r"(?:need|missing|require)(?:s|d)?\s+(?:the\s+)?(?:file\s+)?([\w./-]+\.[\w]+)",
    flags=re.IGNORECASE,
)

_CODE_FENCE_REGEX = re.compile(r"```", flags=re.MULTILINE)


@dataclass
class PostRunResult:
    log_entry: Dict[str, Any]
    artifact_path: Optional[Path] = None
    artifact_id: Optional[str] = None
    retrieval_boosts: List[str] = field(default_factory=list)
    useful_chunks: List[str] = field(default_factory=list)


class IterationCache:
    """Tiny in-memory cache with TTL for iterative loops."""

    def __init__(self, ttl_seconds: float = 120.0) -> None:
        self._ttl = max(0.01, float(ttl_seconds))
        self._data: Dict[str, Dict[str, Any]] = {}

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        ttl = self._ttl if ttl_seconds is None else max(0.01, float(ttl_seconds))
        self._data[key] = {"value": value, "expires_at": time.time() + ttl}

    def get(self, key: str) -> Any:
        item = self._data.get(key)
        if not item:
            return None
        if item["expires_at"] < time.time():
            self._data.pop(key, None)
            return None
        return item["value"]


class PostRunProcessor:
    """Handles logging, writeback, and adaptive post-run learning."""

    def __init__(
        self,
        artifacts_dir: Path | str,
        log_path: Path | str,
        retrieval_rules_path: Path | str,
        index_path: Optional[Path | str] = None,
        cache_ttl_seconds: float = 120.0,
    ) -> None:
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self.retrieval_rules_path = Path(retrieval_rules_path)
        self.retrieval_rules_path.parent.mkdir(parents=True, exist_ok=True)

        self.index_path = Path(index_path) if index_path else (self.artifacts_dir / "index.jsonl")
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

        self.iteration_cache = IterationCache(ttl_seconds=cache_ttl_seconds)

    def process(
        self,
        *,
        response_text: str,
        tokens_in: int,
        tokens_out: int,
        tier: str,
        injected_chunks: Iterable[str],
        latency_ms: float,
    ) -> PostRunResult:
        chunks = list(injected_chunks)
        useful_chunks = _extract_useful_chunks(response_text, chunks)
        need_more_context = _signals_need_more_context(response_text)

        artifact_path: Optional[Path] = None
        artifact_id: Optional[str] = None
        if _is_large_output(response_text, threshold_tokens=500):
            artifact_id, artifact_path = self._store_artifact(
                response_text=response_text,
                tier=tier,
                tokens_out=tokens_out,
                prefer_patch=_looks_repo_bound(response_text),
            )
            self._index_artifact(
                artifact_id=artifact_id,
                artifact_path=artifact_path,
                tier=tier,
                tokens_out=tokens_out,
            )

        boosts = self._apply_retrieval_boosts(response_text)

        log_entry = {
            "ts": int(time.time()),
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "tier": tier,
            "chunks_injected": chunks,
            "latency_ms": float(latency_ms),
            "need_more_context": bool(need_more_context),
            "artifact_id": artifact_id,
            "artifact_path": str(artifact_path) if artifact_path else None,
            "retrieval_boosts": boosts,
            "useful_chunks": useful_chunks,
        }
        self._append_jsonl(self.log_path, log_entry)

        return PostRunResult(
            log_entry=log_entry,
            artifact_path=artifact_path,
            artifact_id=artifact_id,
            retrieval_boosts=boosts,
            useful_chunks=useful_chunks,
        )

    def cache_retrieval(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        self.iteration_cache.set(key, value, ttl_seconds=ttl_seconds)

    def get_cached_retrieval(self, key: str) -> Any:
        return self.iteration_cache.get(key)

    def _store_artifact(
        self,
        *,
        response_text: str,
        tier: str,
        tokens_out: int,
        prefer_patch: bool,
    ) -> tuple[str, Path]:
        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        ext = "patch" if prefer_patch else "txt"
        artifact_path = self.artifacts_dir / f"{artifact_id}.{ext}"

        payload = response_text
        if prefer_patch and not _looks_like_diff(response_text):
            payload = "# Repo-bound output detected; prefer patch/diff format.\n\n" + response_text

        artifact_path.write_text(payload, encoding="utf-8")
        return artifact_id, artifact_path

    def _index_artifact(
        self,
        *,
        artifact_id: str,
        artifact_path: Path,
        tier: str,
        tokens_out: int,
    ) -> None:
        entry = {
            "artifact_id": artifact_id,
            "path": str(artifact_path),
            "indexed_at": int(time.time()),
            "tier": tier,
            "tokens_out": int(tokens_out),
        }
        self._append_jsonl(self.index_path, entry)

    def _apply_retrieval_boosts(self, response_text: str) -> List[str]:
        files = _extract_needed_files(response_text)
        if not files:
            return []

        existing = {"boost_files": []}
        if self.retrieval_rules_path.exists():
            try:
                existing = json.loads(self.retrieval_rules_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {"boost_files": []}

        boosted = set(existing.get("boost_files") or [])
        boosted.update(files)
        data = {"boost_files": sorted(boosted), "updated_at": int(time.time())}
        self.retrieval_rules_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return sorted(files)

    @staticmethod
    def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")


# -----------------------------
# Heuristics
# -----------------------------


def _estimate_token_count(text: str) -> int:
    # Lightweight estimate: ~4 chars/token in English code+text mix.
    return max(1, int(len(text) / 4))


def _is_large_output(text: str, threshold_tokens: int = 500) -> bool:
    return _estimate_token_count(text) > threshold_tokens


def _signals_need_more_context(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in _NEED_CONTEXT_PATTERNS)


def _looks_like_diff(text: str) -> bool:
    return "diff --git" in text or "@@" in text


def _looks_repo_bound(text: str) -> bool:
    # Heuristic: mentions file paths or includes code fences/diff markers.
    if "/" in text and ".py" in text:
        return True
    if _CODE_FENCE_REGEX.search(text):
        return True
    return _looks_like_diff(text)


def _extract_needed_files(text: str) -> List[str]:
    matches = _NEED_FILE_REGEX.findall(text or "")
    cleaned = [m.strip().strip("`'\"") for m in matches if m and "." in m]
    # dedupe while preserving order
    seen = set()
    out: List[str] = []
    for item in cleaned:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _extract_useful_chunks(response_text: str, chunk_ids: List[str]) -> List[str]:
    if not response_text or not chunk_ids:
        return []
    used = []
    lower = response_text.lower()
    for cid in chunk_ids:
        if cid.lower() in lower:
            used.append(cid)
    return used
