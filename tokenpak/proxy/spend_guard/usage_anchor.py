# SPDX-License-Identifier: Apache-2.0
"""Bounded process-local provider usage anchors; no request content is retained."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping

_LOCK = threading.Lock()
_MAX_ANCHORS = 256


@dataclass(frozen=True)
class UsageAnchor:
    input_tokens: int
    history_count: int
    history_digest: bytes
    overhead_tokens: int
    started_at: float


_ANCHORS: OrderedDict[tuple[str, str], UsageAnchor] = OrderedDict()


def _digest(value: object) -> bytes:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


def _shape(body: bytes) -> tuple[dict[str, Any], str, list[Any]]:
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("request is not an object")
    field = "messages" if "messages" in parsed else "input"
    history = parsed.get(field)
    if isinstance(history, str):
        history = [history]
    if not isinstance(history, list):
        raise ValueError("request has no supported history")
    return parsed, field, history


def _counter(parsed: dict[str, Any], model: str) -> int:
    from .estimator import structural_tokens

    return structural_tokens(parsed, model)[0]


def record_completed(
    body: bytes,
    model: str,
    session_id: str,
    usage: Mapping[str, object] | None,
    *,
    provider: str,
    started_at: float,
) -> None:
    """Observe a successfully completed provider response, never estimates.

    Anthropic cache buckets are disjoint input; OpenAI's input total already
    includes cached input. Missing/invalid usage leaves an existing anchor
    intact. A late completion of an older request cannot replace a newer one.
    """
    if (
        not session_id
        or not model
        or not usage
        or provider not in ("anthropic", "openai", "openai-codex")
    ):
        return
    names = ("input_tokens",)
    if provider == "anthropic":
        names += ("cache_read_input_tokens", "cache_creation_input_tokens")
    if "input_tokens" not in usage:
        return
    values = [usage.get(name, 0) for name in names]
    if any(type(value) is not int or value < 0 for value in values):
        return
    total = sum(values)
    if total <= 0:
        return
    parsed, field, history = _shape(body)
    overhead = {**parsed, field: []}
    anchor = UsageAnchor(
        total, len(history), _digest(history), _counter(overhead, model), started_at
    )
    key = session_id, model
    with _LOCK:
        old = _ANCHORS.get(key)
        if old is not None and old.started_at > started_at:
            return
        _ANCHORS[key] = anchor
        _ANCHORS.move_to_end(key)
        while len(_ANCHORS) > _MAX_ANCHORS:
            _ANCHORS.popitem(last=False)


def projection_floor(body: bytes, model: str, session_id: str) -> int:
    """Last observed input plus the structurally estimated appended suffix.

    A matching prefix is established with a digest, not retained content.
    If history was rewritten/compacted, use last input plus the full current
    estimate conservatively until another completion establishes a baseline.
    Responses continuations with previous_response_id use their whole input
    as the suffix. No cache sharing across sessions or models is permitted.
    """
    if not session_id or not model:
        return 0
    with _LOCK:
        anchor = _ANCHORS.get((session_id, model))
    if anchor is None:
        return 0
    parsed, field, history = _shape(body)
    if (
        len(history) >= anchor.history_count
        and _digest(history[: anchor.history_count]) == anchor.history_digest
    ):
        suffix = {field: history[anchor.history_count :]}
        overhead = _counter({**parsed, field: []}, model)
        increment = _counter(suffix, model) + max(0, overhead - anchor.overhead_tokens)
    else:
        increment = _counter(parsed, model)
    return anchor.input_tokens + increment


def reset_for_testing() -> None:
    with _LOCK:
        _ANCHORS.clear()
