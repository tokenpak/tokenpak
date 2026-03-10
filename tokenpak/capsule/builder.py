"""
tokenpak.capsule.builder
========================

Capsule Builder — context-block compression for the TokenPak proxy pipeline.

A **capsule** is a compact, structured representation of a verbose context
block. The builder identifies large historical message blocks, compresses them
deterministically, and wraps them in a capsule envelope so the model still
receives the semantic content at reduced token cost.

Design Principles
-----------------
* **Deterministic** — SHA-256 of normalised content drives the capsule ID,
  so identical input always produces identical output.
* **Fast** — pure string operations only; no model calls. Target <5 ms p99
  on typical payloads.
* **Transparent** — capsule envelopes are readable plain-text, not binary.
* **Safe** — if the builder raises for any reason, the caller can fall back
  to the original body unmodified.
* **Feature-flagged** — disabled by default; opt-in via
  ``TOKENPAK_CAPSULE_BUILDER=1`` (or the ``enabled`` constructor arg).

Capsule Envelope Format
-----------------------
::

    [CAPSULE id=a1b2c3d4 ratio=0.42 chars_in=1200 chars_out=504]
    <compressed content>
    [/CAPSULE]

The envelope is plain text that large-context models handle gracefully.

Usage
-----
::

    from tokenpak.capsule.builder import CapsuleBuilder

    builder = CapsuleBuilder()
    new_body, stats = builder.process(body_bytes)
    # stats: {"blocks_capsulized": 3, "chars_in": 4800, "chars_out": 2016,
    #          "ratio": 0.42, "duration_ms": 2.1}
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum character length of a text block before the builder considers
# compressing it.  Below this threshold the overhead of a capsule envelope
# would exceed the savings.
DEFAULT_MIN_BLOCK_CHARS: int = 400

# Number of most-recent messages to leave untouched (the "hot window").
# Capsule compression is only applied to messages *outside* this window.
DEFAULT_HOT_WINDOW: int = 2

# Maximum chars to keep per paragraph in compressed form.
_MAX_PARA_CHARS: int = 200

# Pre-compiled patterns (module-level for reuse across calls)
_RE_MULTI_BLANK = re.compile(r"\n{3,}")
_RE_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_RE_MULTI_SPACE = re.compile(r"[ \t]{2,}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capsule_id(content: str) -> str:
    """Return a short deterministic ID for *content* (8 hex chars)."""
    digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
    return digest[:8]


def _compress_paragraph(para: str) -> str:
    """
    Compress a single prose paragraph deterministically.

    Strategy (in order):
    1. Collapse internal whitespace.
    2. If the paragraph ends with a sentence boundary within ``_MAX_PARA_CHARS``,
       truncate there.
    3. Hard-truncate at ``_MAX_PARA_CHARS`` on a word boundary.
    """
    # Collapse runs of spaces / tabs (not newlines — those separate paragraphs)
    text = _RE_MULTI_SPACE.sub(" ", para).strip()

    if len(text) <= _MAX_PARA_CHARS:
        return text

    # Try to find a sentence end within budget
    m = None
    for m in _RE_SENTENCE_END.finditer(text):
        if m.end() > _MAX_PARA_CHARS:
            break
    if m and m.end() <= _MAX_PARA_CHARS:
        return text[: m.end()].strip()

    # Fall back to word-boundary truncation
    truncated = text[:_MAX_PARA_CHARS]
    last_space = truncated.rfind(" ")
    if last_space > _MAX_PARA_CHARS // 2:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "…"


def _compress_text(text: str) -> str:
    """
    Compress *text* by applying paragraph-level compression.

    Structure-bearing lines (headers ``#``, bullets ``- / * / +``, numbered
    lists ``1.``, code fences ```` ``` ````) are preserved verbatim.
    Prose paragraphs are compressed.

    Returns the compressed text.  Always deterministic.
    """
    # Normalise excessive blank lines first
    text = _RE_MULTI_BLANK.sub("\n\n", text).strip()

    # Split into logical blocks separated by blank lines
    blocks = re.split(r"\n{2,}", text)
    compressed_blocks: List[str] = []

    in_code_fence = False

    for block in blocks:
        lines = block.split("\n")
        out_lines: List[str] = []

        for line in lines:
            stripped = line.strip()

            # Track code fences — never compress inside them
            if stripped.startswith("```"):
                in_code_fence = not in_code_fence
                out_lines.append(line)
                continue

            if in_code_fence:
                out_lines.append(line)
                continue

            # Structure lines — keep verbatim
            if (
                stripped.startswith("#")  # heading
                or re.match(r"^[-*+]\s", stripped)  # unordered bullet
                or re.match(r"^\d+\.\s", stripped)  # ordered list
                or stripped.startswith(">")  # blockquote
                or stripped == "---"
                or stripped == "==="  # hr / setext heading
                or stripped == ""  # blank line within block
            ):
                out_lines.append(line)
                continue

            # Prose line — compress
            out_lines.append(_compress_paragraph(stripped))

        compressed_blocks.append("\n".join(out_lines))

    return "\n\n".join(compressed_blocks)


def _wrap_capsule(original: str, compressed: str) -> str:
    """
    Wrap *compressed* content in a capsule envelope.

    The capsule ID is derived from *original* (pre-compression) content so
    that the ID is stable even if the compressor changes.
    """
    cid = _capsule_id(original)
    chars_in = len(original)
    chars_out = len(compressed)
    ratio = round(chars_out / chars_in, 3) if chars_in else 1.0
    header = f"[CAPSULE id={cid} ratio={ratio} chars_in={chars_in} chars_out={chars_out}]"
    return f"{header}\n{compressed}\n[/CAPSULE]"


# ---------------------------------------------------------------------------
# CapsuleBuilder
# ---------------------------------------------------------------------------


class CapsuleBuilder:
    """
    Compress verbose historical context blocks in an LLM request payload.

    Parameters
    ----------
    enabled : bool
        Master switch.  When *False* (the default), :meth:`process` is a
        no-op (returns original bytes + empty stats).
    min_block_chars : int
        Minimum character length of a text block to qualify for compression.
    hot_window : int
        Number of trailing messages to leave untouched (the "hot window").
        Capsule compression applies only to messages *before* this window.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        min_block_chars: int = DEFAULT_MIN_BLOCK_CHARS,
        hot_window: int = DEFAULT_HOT_WINDOW,
    ) -> None:
        self._enabled = enabled
        self._min_block_chars = min_block_chars
        self._hot_window = hot_window

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        body_bytes: bytes,
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Process the request body, capsulising eligible context blocks.

        Parameters
        ----------
        body_bytes : bytes
            Raw JSON request body (OpenAI / Anthropic chat format).

        Returns
        -------
        (new_body_bytes, stats)
            *new_body_bytes* — modified body (or original if nothing changed).
            *stats* — dict with keys:
                ``blocks_capsulized`` int,
                ``chars_in``         int,
                ``chars_out``        int,
                ``ratio``            float,
                ``duration_ms``      float.
        """
        _empty_stats: Dict[str, Any] = {
            "blocks_capsulized": 0,
            "chars_in": 0,
            "chars_out": 0,
            "ratio": 1.0,
            "duration_ms": 0.0,
            "skipped": True,
            "skip_reason": "disabled",
        }

        if not self._enabled:
            return body_bytes, _empty_stats

        t0 = time.monotonic()
        try:
            data = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            return body_bytes, {**_empty_stats, "skip_reason": "invalid_json"}

        messages: List[Dict[str, Any]] = data.get("messages") or []
        if not messages:
            stats = {**_empty_stats, "skip_reason": "no_messages", "duration_ms": 0.0}
            return body_bytes, stats

        # Determine the hot window: last `hot_window` messages are untouched
        hot_start = max(0, len(messages) - self._hot_window)

        total_chars_in = 0
        total_chars_out = 0
        blocks_capsulized = 0
        modified = False

        for idx, msg in enumerate(messages):
            if idx >= hot_start:
                # Inside hot window — never touch
                continue
            if not isinstance(msg, dict):
                continue

            content = msg.get("content")

            if isinstance(content, str):
                new_content, delta_in, delta_out, capsulized = self._maybe_capsulise(content)
                if capsulized:
                    msg["content"] = new_content
                    modified = True
                total_chars_in += delta_in
                total_chars_out += delta_out
                blocks_capsulized += capsulized

            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        new_text, delta_in, delta_out, capsulized = self._maybe_capsulise(
                            part["text"]
                        )
                        if capsulized:
                            part["text"] = new_text
                            modified = True
                        total_chars_in += delta_in
                        total_chars_out += delta_out
                        blocks_capsulized += capsulized

        duration_ms = (time.monotonic() - t0) * 1000

        if not modified:
            ratio = 1.0
            stats: Dict[str, Any] = {  # type: ignore[no-redef]
                "blocks_capsulized": 0,
                "chars_in": total_chars_in,
                "chars_out": total_chars_in,
                "ratio": ratio,
                "duration_ms": round(duration_ms, 3),
                "skipped": False,
                "skip_reason": "no_eligible_blocks",
            }
            return body_bytes, stats

        new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        ratio = round(total_chars_out / total_chars_in, 3) if total_chars_in else 1.0

        stats = {
            "blocks_capsulized": blocks_capsulized,
            "chars_in": total_chars_in,
            "chars_out": total_chars_out,
            "ratio": ratio,
            "duration_ms": round(duration_ms, 3),
            "skipped": False,
            "skip_reason": None,
        }
        return new_body, stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_capsulise(self, text: str) -> Tuple[str, int, int, int]:
        """
        Conditionally capsulise a single text block.

        Returns
        -------
        (new_text, chars_in, chars_out, capsulized)
            *capsulized* is 1 if the block was wrapped, 0 otherwise.
        """
        chars_in = len(text)

        if chars_in < self._min_block_chars:
            return text, chars_in, chars_in, 0

        compressed = _compress_text(text)
        wrapped = _wrap_capsule(text, compressed)
        return wrapped, chars_in, len(wrapped), 1
