# SPDX-License-Identifier: Apache-2.0
"""Compressor-backed preview service.

This module replaces a simulation. The previous ``tokenpak preview`` computed
``output_tokens = max(int(len(text.split()) * 0.65), 10)`` and printed fixed
block names (``system_prompt``, ``user_context``, ``debug_logs``,
``duplicate_text``) that bore no relation to the user's input. On JSON input
with no whitespace it reported **-900% savings** with negative block counts.

Everything here is measured by running the real
:class:`~tokenpak.compression.pipeline.CompressionPipeline`, and every
reported quantity is checked against the invariants in
:meth:`PreviewResult.validate` before it can reach a user.

Provenance is mandatory, not decorative: a preview states *what* was measured
(input digest, byte length, source), *how* (tokenizer id, pipeline stages,
mode) and *with what* (TokenPak version). A savings figure without that
context is not a measurement.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

#: Identifier for the token estimator used by the compression pipeline.
#: The pipeline estimates ``len(content) // 4`` characters-per-token rather
#: than invoking a model tokenizer; naming it here keeps the preview honest
#: about the precision of its own numbers.
TOKENIZER_ID = "heuristic-chars-per-token-4"


class PreviewState(str, Enum):
    """Outcome of a preview attempt."""

    MEASURED = "measured"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class PreviewBlock:
    """A real segment identified by the compression pipeline.

    ``block_id`` and ``segment_type`` come from the pipeline's own
    :class:`~tokenpak.compression.segmentizer.Segment` objects. They are never
    synthesized: if the pipeline did not identify a segment, no block is
    reported for it.
    """

    block_id: str
    segment_type: str
    order: int
    raw_chars: int
    final_chars: int
    retained: bool

    def to_json(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "segment_type": self.segment_type,
            "order": self.order,
            "raw_chars": self.raw_chars,
            "final_chars": self.final_chars,
            "retained": self.retained,
        }


@dataclass(frozen=True)
class PreviewProvenance:
    """What was measured, how, and with what version."""

    input_sha256: str
    input_bytes: int
    input_source: str
    input_kind: str
    turns: int
    tokenizer: str
    mode: str
    stages_run: List[str]
    tokenpak_version: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "input_sha256": self.input_sha256,
            "input_bytes": self.input_bytes,
            "input_source": self.input_source,
            "input_kind": self.input_kind,
            "turns": self.turns,
            "tokenizer": self.tokenizer,
            "mode": self.mode,
            "stages_run": list(self.stages_run),
            "tokenpak_version": self.tokenpak_version,
        }


class PreviewInvariantError(AssertionError):
    """A preview result violated its own contract — refuse to display it."""


@dataclass(frozen=True)
class PreviewResult:
    """Strict result contract for ``tokenpak preview``.

    Invariants (enforced by :meth:`validate`, called from ``__post_init__``):

    1. Token counts are non-negative.
    2. ``saved_tokens == input_tokens - output_tokens`` exactly.
    3. ``compression_ratio`` lies in ``[0.0, 1.0]``.
    4. ``duration_ms`` is a real measurement (``> 0``) whenever state is
       ``MEASURED``.
    5. Block identities come from the pipeline; no synthesized names.
    6. Savings are never negative. If compression would *expand* the input,
       the original is retained and ``applied`` is ``False`` — the honest
       report of "we looked and chose not to change anything".

    In any non-``MEASURED`` state every numeric field is ``None``, never ``0``.
    """

    state: PreviewState
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    saved_tokens: Optional[int] = None
    compression_ratio: Optional[float] = None
    duration_ms: Optional[float] = None
    applied: Optional[bool] = None
    blocks: List[PreviewBlock] = field(default_factory=list)
    provenance: Optional[PreviewProvenance] = None
    reason: str = ""

    def __post_init__(self) -> None:
        self.validate()

    # -- contract -----------------------------------------------------------

    def validate(self) -> None:
        """Raise :class:`PreviewInvariantError` if the contract is violated."""
        if self.state is not PreviewState.MEASURED:
            numeric = {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "saved_tokens": self.saved_tokens,
                "compression_ratio": self.compression_ratio,
                "duration_ms": self.duration_ms,
            }
            populated = {k: v for k, v in numeric.items() if v is not None}
            if populated:
                raise PreviewInvariantError(
                    f"state={self.state.value} must not carry numeric values, "
                    f"got {populated!r}; unmeasured is null, never zero"
                )
            if not self.reason:
                raise PreviewInvariantError(f"state={self.state.value} requires a reason")
            return

        # MEASURED — every field is required and mutually consistent.
        for name in ("input_tokens", "output_tokens", "saved_tokens", "compression_ratio"):
            if getattr(self, name) is None:
                raise PreviewInvariantError(f"measured preview missing {name}")
        assert self.input_tokens is not None
        assert self.output_tokens is not None
        assert self.saved_tokens is not None
        assert self.compression_ratio is not None

        if self.input_tokens < 0 or self.output_tokens < 0:
            raise PreviewInvariantError(
                f"negative token count: input={self.input_tokens} output={self.output_tokens}"
            )
        if self.saved_tokens < 0:
            raise PreviewInvariantError(
                f"negative savings: {self.saved_tokens}; expansion must set applied=False"
            )
        if self.saved_tokens != self.input_tokens - self.output_tokens:
            raise PreviewInvariantError(
                f"saved_tokens {self.saved_tokens} != input {self.input_tokens} "
                f"- output {self.output_tokens}"
            )
        if not (0.0 <= self.compression_ratio <= 1.0):
            raise PreviewInvariantError(f"compression_ratio out of [0,1]: {self.compression_ratio}")
        if self.duration_ms is None or self.duration_ms <= 0.0:
            raise PreviewInvariantError(
                f"duration_ms must be a real measurement, got {self.duration_ms!r}"
            )
        if self.applied is None:
            raise PreviewInvariantError("measured preview must state whether compression applied")
        if self.provenance is None:
            raise PreviewInvariantError("measured preview must carry provenance")
        for b in self.blocks:
            if b.raw_chars < 0 or b.final_chars < 0:
                raise PreviewInvariantError(
                    f"negative block size on {b.block_id}: raw={b.raw_chars} final={b.final_chars}"
                )
            if not b.block_id or not b.segment_type:
                raise PreviewInvariantError("block identity must come from the pipeline")
            # Invariant 7: the blocks and the totals must describe the same
            # outcome. "We kept your input" alongside a block reporting that
            # input reduced to nothing is not a rendering detail — it is two
            # contradictory measurements in one payload.
            if not self.applied and b.final_chars != b.raw_chars:
                raise PreviewInvariantError(
                    f"applied=False but block {b.block_id} reports "
                    f"{b.raw_chars} -> {b.final_chars} chars; nothing was applied, "
                    "so nothing was removed"
                )

    # -- serialization ------------------------------------------------------

    def to_json(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "saved_tokens": self.saved_tokens,
            "compression_ratio": self.compression_ratio,
            "duration_ms": self.duration_ms,
            "applied": self.applied,
            "blocks": [b.to_json() for b in self.blocks],
            "provenance": self.provenance.to_json() if self.provenance else None,
            "reason": self.reason or None,
        }


# -- service ----------------------------------------------------------------


def _tokenpak_version() -> str:
    try:
        from tokenpak import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - version is best-effort metadata
        return "unknown"


def _parse_messages(text: str) -> tuple[List[Dict[str, Any]], str]:
    """Interpret *text* as a conversation when it plainly is one.

    TokenPak's savings come from redundancy *across conversation turns* —
    dedup, alias extraction, instruction-table substitution. A single blob of
    prose has almost nothing for that machinery to remove, so previewing one
    and reporting a large percentage would be a lie about where the value
    comes from. Accepting real conversation exports lets the preview measure
    the thing the proxy actually optimizes.

    Recognized shapes:
      * a JSON array of ``{"role": ..., "content": ...}`` objects
      * a JSON object with a ``messages`` array (provider request body)
      * JSONL, one message object per line

    Anything else is treated as a single user turn, labelled as such.
    """
    import json as _json

    stripped = text.strip()

    def _valid(seq: Any) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(seq, list) or not seq:
            return None
        out: List[Dict[str, Any]] = []
        for item in seq:
            if not isinstance(item, dict) or "role" not in item or "content" not in item:
                return None
            out.append(item)
        return out

    if stripped[:1] in ("[", "{"):
        try:
            parsed = _json.loads(stripped)
        except Exception:
            parsed = None
        if parsed is not None:
            msgs = _valid(parsed)
            if msgs is not None:
                return msgs, "conversation"
            if isinstance(parsed, dict):
                msgs = _valid(parsed.get("messages"))
                if msgs is not None:
                    return msgs, "conversation"

    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if len(lines) > 1 and all(ln.lstrip().startswith("{") for ln in lines):
        acc: List[Dict[str, Any]] = []
        for ln in lines:
            try:
                obj = _json.loads(ln)
            except Exception:
                acc = []
                break
            if not isinstance(obj, dict) or "role" not in obj or "content" not in obj:
                acc = []
                break
            acc.append(obj)
        if acc:
            return acc, "conversation"

    return [{"role": "user", "content": text}], "single_turn"


def run_preview(
    text: str,
    *,
    input_source: str = "stdin",
    mode: str = "hybrid",
) -> PreviewResult:
    """Measure what compression would do to *text*.

    Returns a :class:`PreviewResult` in state:

    * ``NO_DATA`` — input was empty or whitespace-only. Nothing to measure.
    * ``UNAVAILABLE`` — the compression pipeline could not be loaded.
    * ``ERROR`` — the pipeline was invoked and raised.
    * ``MEASURED`` — real numbers, contract-checked.

    Never raises for ordinary failure; the state carries the outcome so no
    caller has to invent a number to fill a gap.
    """
    if not text or not text.strip():
        return PreviewResult(state=PreviewState.NO_DATA, reason="input was empty")

    try:
        from tokenpak.compression.pipeline import CompressionPipeline
    except Exception as exc:
        return PreviewResult(
            state=PreviewState.UNAVAILABLE,
            reason=f"compression pipeline unavailable: {exc}",
        )

    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    input_bytes = len(text.encode("utf-8", errors="replace"))
    messages, input_kind = _parse_messages(text)

    t0 = time.perf_counter()
    try:
        pipeline = CompressionPipeline()
        result = pipeline.run(messages, dry_run=True)
    except Exception as exc:
        return PreviewResult(
            state=PreviewState.ERROR,
            reason=f"compression failed: {type(exc).__name__}: {exc}",
        )
    wall_ms = (time.perf_counter() - t0) * 1000.0

    input_tokens = int(getattr(result, "tokens_raw", 0) or 0)
    output_tokens = int(getattr(result, "tokens_after", 0) or 0)

    # Invariant 6: expansion is reported honestly rather than as a negative
    # saving or a clamped zero. We keep the original and say we did not apply.
    applied = output_tokens < input_tokens
    if not applied:
        output_tokens = input_tokens

    saved_tokens = input_tokens - output_tokens
    ratio = (saved_tokens / input_tokens) if input_tokens > 0 else 0.0
    ratio = min(max(ratio, 0.0), 1.0)

    # Invariant 4: prefer the pipeline's own timing, fall back to our wall
    # clock, and never report a non-positive duration as if it were measured.
    duration_ms = float(getattr(result, "duration_ms", 0.0) or 0.0)
    if duration_ms <= 0.0:
        duration_ms = wall_ms
    if duration_ms <= 0.0:
        duration_ms = 1e-3

    blocks: List[PreviewBlock] = []
    for seg in getattr(result, "segments", []) or []:
        seg_id = str(getattr(seg, "segment_id", "") or "")
        seg_type = str(getattr(seg, "segment_type", "") or "")
        if not seg_id or not seg_type:
            # Invariant 5: an unidentified segment is omitted, not named.
            continue
        raw_len = max(0, int(getattr(seg, "raw_len", 0) or 0))
        final_len = max(0, int(getattr(seg, "final_len", 0) or 0))
        if not applied:
            # The totals already say the original was kept. The blocks were
            # still describing the *attempted* compression, so a result could
            # report applied=false with saved_tokens=0 while its blocks showed
            # a 3,321-character segment reduced to 0 and retained=false. Both
            # halves of one payload, disagreeing about whether the user's
            # input survived. When nothing was applied, nothing was removed.
            final_len = raw_len
        blocks.append(
            PreviewBlock(
                block_id=seg_id,
                segment_type=seg_type,
                order=int(getattr(seg, "order", 0) or 0),
                raw_chars=raw_len,
                final_chars=final_len,
                retained=final_len > 0,
            )
        )

    provenance = PreviewProvenance(
        input_sha256=digest,
        input_bytes=input_bytes,
        input_source=input_source,
        input_kind=input_kind,
        turns=len(messages),
        tokenizer=TOKENIZER_ID,
        mode=mode,
        stages_run=list(getattr(result, "stages_run", []) or []),
        tokenpak_version=_tokenpak_version(),
    )

    return PreviewResult(
        state=PreviewState.MEASURED,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        saved_tokens=saved_tokens,
        compression_ratio=ratio,
        duration_ms=duration_ms,
        applied=applied,
        blocks=blocks,
        provenance=provenance,
    )
