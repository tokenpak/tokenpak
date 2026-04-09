"""
TokenPak Streaming Chunk Format Translator (F.2 — Streaming Extension)

Translates individual SSE streaming chunks between provider formats:
  - Anthropic ↔ OpenAI
  - Anthropic ↔ Google (partial/best-effort — Google uses different streaming model)

Anthropic SSE event types:
  message_start, content_block_start, content_block_delta (text_delta/input_json_delta),
  content_block_stop, message_delta, message_stop, ping

OpenAI SSE chunk format:
  Single JSON object per "data: " line with choices[0].delta

Google SSE format (streamGenerateContent):
  Each "data: " line is a full partial GenerateContentResponse with candidates

Usage:
  translator = StreamingTranslator("anthropic", "openai")
  for sse_line in upstream_lines:
      translated = translator.translate_chunk(sse_line)
      if translated is not None:
          yield translated
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Iterator, List, Optional

# ---------------------------------------------------------------------------
# SSE line helpers
# ---------------------------------------------------------------------------


def _parse_sse_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a 'data: {...}' SSE line. Returns None for non-data or [DONE]."""
    line = line.strip()
    if not line.startswith("data: "):
        return None
    payload = line[6:]
    if payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None


def _sse_line(data: Dict[str, Any]) -> str:
    """Format a dict as an SSE data line (no trailing newline)."""
    return f"data: {json.dumps(data, ensure_ascii=False)}"


def _sse_done() -> str:
    return "data: [DONE]"


# ---------------------------------------------------------------------------
# Anthropic → OpenAI streaming
# ---------------------------------------------------------------------------


class _AnthropicToOpenAIStream:
    """
    Stateful converter: Anthropic SSE events → OpenAI SSE chunks.

    Anthropic fires: message_start → content_block_start → N×content_block_delta
                     → content_block_stop → message_delta → message_stop

    OpenAI fires: N chunks with choices[0].delta, last has finish_reason.
    """

    def __init__(self, stream_id: Optional[str] = None) -> None:
        self._id = stream_id or f"chatcmpl-{uuid.uuid4().hex[:12]}"
        self._model: str = ""
        self._block_type: str = "text"  # current content block type
        self._tool_call_index: int = -1
        self._tool_call_id: str = ""
        self._tool_call_name: str = ""
        self._finish_reason: Optional[str] = None

    def translate(self, event: Dict[str, Any]) -> Optional[str]:
        """Translate one Anthropic event dict → OpenAI SSE line (or None)."""
        etype = event.get("type", "")

        if etype == "message_start":
            msg = event.get("message", {})
            self._model = msg.get("model", "")
            # Emit a role chunk
            chunk = self._chunk({"role": "assistant", "content": ""})
            return _sse_line(chunk)

        if etype == "content_block_start":
            block = event.get("content_block", {})
            btype = block.get("type", "text")
            self._block_type = btype
            if btype == "tool_use":
                self._tool_call_index += 1
                self._tool_call_id = block.get("id", f"call_{self._tool_call_index}")
                self._tool_call_name = block.get("name", "")
                # Emit tool_call start delta
                delta = {
                    "tool_calls": [
                        {
                            "index": self._tool_call_index,
                            "id": self._tool_call_id,
                            "type": "function",
                            "function": {"name": self._tool_call_name, "arguments": ""},
                        }
                    ]
                }
                return _sse_line(self._chunk(delta))
            return None  # text block start — no OpenAI equivalent

        if etype == "content_block_delta":
            delta_block = event.get("delta", {})
            dtype = delta_block.get("type", "")
            if dtype == "text_delta":
                delta = {"content": delta_block.get("text", "")}
                return _sse_line(self._chunk(delta))
            if dtype == "input_json_delta":
                # Tool call argument streaming
                delta = {
                    "tool_calls": [
                        {
                            "index": self._tool_call_index,
                            "function": {"arguments": delta_block.get("partial_json", "")},
                        }
                    ]
                }
                return _sse_line(self._chunk(delta))
            return None

        if etype == "content_block_stop":
            return None  # no OpenAI equivalent

        if etype == "message_delta":
            delta = event.get("delta", {})
            stop_reason = delta.get("stop_reason", "end_turn")
            finish_map = {
                "end_turn": "stop",
                "tool_use": "tool_calls",
                "max_tokens": "length",
                "stop_sequence": "stop",
            }
            self._finish_reason = finish_map.get(stop_reason, "stop")  # type: ignore
            # Emit final chunk with finish_reason
            chunk = self._chunk({}, finish_reason=self._finish_reason)
            return _sse_line(chunk)

        if etype in ("message_stop", "ping"):
            return None

        return None  # ignore unknown events

    def _chunk(
        self,
        delta: Dict[str, Any],
        finish_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "id": self._id,
            "object": "chat.completion.chunk",
            "model": self._model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }


# ---------------------------------------------------------------------------
# OpenAI → Anthropic streaming
# ---------------------------------------------------------------------------


class _OpenAIToAnthropicStream:
    """
    Stateful converter: OpenAI SSE chunks → Anthropic SSE events.

    Emits a minimal but valid Anthropic SSE sequence so clients that
    parse Anthropic streaming can consume an OpenAI upstream.
    """

    def __init__(self, message_id: Optional[str] = None) -> None:
        self._id = message_id or f"msg_{uuid.uuid4().hex[:16]}"
        self._model: str = ""
        self._started: bool = False
        self._text_block_open: bool = False
        self._tool_call_blocks: Dict[int, Dict[str, Any]] = {}  # index → state

    def translate(self, chunk: Dict[str, Any]) -> List[str]:
        """Translate one OpenAI chunk → list of Anthropic SSE event lines."""
        lines: List[str] = []
        choices = chunk.get("choices", [])
        if not choices:
            return lines

        if not self._started:
            self._model = chunk.get("model", "")
            lines.append(
                _sse_line(
                    {
                        "type": "message_start",
                        "message": {
                            "id": self._id,
                            "type": "message",
                            "role": "assistant",
                            "model": self._model,
                            "content": [],
                            "stop_reason": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    }
                )
            )
            self._started = True

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # Text delta
        text = delta.get("content")
        if text:
            if not self._text_block_open:
                lines.append(
                    _sse_line(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        }
                    )
                )
                self._text_block_open = True
            lines.append(
                _sse_line(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": text},
                    }
                )
            )

        # Tool call deltas
        for tc in delta.get("tool_calls", []):
            idx = tc.get("index", 0)
            if idx not in self._tool_call_blocks:
                # New tool call block
                tc_id = tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}")
                tc_name = tc.get("function", {}).get("name", "")
                self._tool_call_blocks[idx] = {
                    "id": tc_id,
                    "name": tc_name,
                    "block_index": idx + 1,  # 0 is text block
                }
                lines.append(
                    _sse_line(
                        {
                            "type": "content_block_start",
                            "index": idx + 1,
                            "content_block": {
                                "type": "tool_use",
                                "id": tc_id,
                                "name": tc_name,
                                "input": {},
                            },
                        }
                    )
                )
            args_fragment = tc.get("function", {}).get("arguments", "")
            if args_fragment:
                lines.append(
                    _sse_line(
                        {
                            "type": "content_block_delta",
                            "index": idx + 1,
                            "delta": {"type": "input_json_delta", "partial_json": args_fragment},
                        }
                    )
                )

        # Finish
        if finish_reason:
            if self._text_block_open:
                lines.append(_sse_line({"type": "content_block_stop", "index": 0}))
            for idx, state in self._tool_call_blocks.items():
                lines.append(
                    _sse_line(
                        {
                            "type": "content_block_stop",
                            "index": state["block_index"],
                        }
                    )
                )
            finish_map = {
                "stop": "end_turn",
                "tool_calls": "tool_use",
                "length": "max_tokens",
            }
            ant_stop = finish_map.get(finish_reason, "end_turn")
            lines.append(
                _sse_line(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": ant_stop, "stop_sequence": None},
                        "usage": {"output_tokens": 0},
                    }
                )
            )
            lines.append(_sse_line({"type": "message_stop"}))

        return lines


# ---------------------------------------------------------------------------
# Google → Anthropic streaming (best-effort)
# ---------------------------------------------------------------------------


class _GoogleToAnthropicStream:
    """
    Best-effort: Google streamGenerateContent chunks → Anthropic SSE events.

    Google emits full partial responses per chunk (candidates[].content.parts).
    We diff against the last seen text and emit text_delta events.
    """

    def __init__(self, message_id: Optional[str] = None) -> None:
        self._id = message_id or f"msg_{uuid.uuid4().hex[:16]}"
        self._model: str = ""
        self._started: bool = False
        self._last_text: str = ""
        self._block_open: bool = False

    def translate(self, chunk: Dict[str, Any]) -> List[str]:
        lines: List[str] = []

        if not self._started:
            self._model = chunk.get("modelVersion", "")
            lines.append(
                _sse_line(
                    {
                        "type": "message_start",
                        "message": {
                            "id": self._id,
                            "type": "message",
                            "role": "assistant",
                            "model": self._model,
                            "content": [],
                            "stop_reason": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    }
                )
            )
            self._started = True

        candidates = chunk.get("candidates", [])
        if not candidates:
            return lines

        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        finish_reason = candidate.get("finishReason")

        # Extract text from parts (diff-based)
        current_text = "".join(p.get("text", "") for p in parts if "text" in p)
        new_text = current_text[len(self._last_text) :]
        self._last_text = current_text

        if new_text:
            if not self._block_open:
                lines.append(
                    _sse_line(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        }
                    )
                )
                self._block_open = True
            lines.append(
                _sse_line(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": new_text},
                    }
                )
            )

        # Function calls in parts
        for part in parts:
            if "functionCall" in part:
                fc = part["functionCall"]
                block_idx = 1
                lines.append(
                    _sse_line(
                        {
                            "type": "content_block_start",
                            "index": block_idx,
                            "content_block": {
                                "type": "tool_use",
                                "id": fc.get("name", ""),
                                "name": fc.get("name", ""),
                                "input": fc.get("args", {}),
                            },
                        }
                    )
                )
                lines.append(
                    _sse_line(
                        {
                            "type": "content_block_stop",
                            "index": block_idx,
                        }
                    )
                )

        # Finish
        if finish_reason and finish_reason != "FINISH_REASON_UNSPECIFIED":
            if self._block_open:
                lines.append(_sse_line({"type": "content_block_stop", "index": 0}))
            finish_map = {
                "STOP": "end_turn",
                "MAX_TOKENS": "max_tokens",
                "TOOL_CODE": "tool_use",
            }
            ant_stop = finish_map.get(finish_reason, "end_turn")
            usage_meta = chunk.get("usageMetadata", {})
            lines.append(
                _sse_line(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": ant_stop, "stop_sequence": None},
                        "usage": {"output_tokens": usage_meta.get("candidatesTokenCount", 0)},
                    }
                )
            )
            lines.append(_sse_line({"type": "message_stop"}))

        return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class StreamingTranslator:
    """
    Stateful SSE stream translator between provider formats.

    Usage::

        t = StreamingTranslator("anthropic", "openai")
        for raw_line in upstream_sse_lines:
            out = t.translate_chunk(raw_line)
            if out:
                for line in out:
                    yield line + "\\n\\n"

    Args:
        source_provider: "anthropic" | "openai" | "google"
        target_provider: "anthropic" | "openai" | "google"
    """

    def __init__(self, source_provider: str, target_provider: str) -> None:
        self.source = source_provider
        self.target = target_provider
        self._impl = self._build_impl(source_provider, target_provider)

    def _build_impl(self, src: str, tgt: str):
        if src == tgt:
            return None
        if src == "anthropic" and tgt == "openai":
            return _AnthropicToOpenAIStream()
        if src == "openai" and tgt == "anthropic":
            return _OpenAIToAnthropicStream()
        if src == "google" and tgt == "anthropic":
            return _GoogleToAnthropicStream()
        # For pairs via chain (e.g., google→openai), raise — caller should
        # decompose into two steps or use passthrough.
        raise ValueError(
            f"No direct streaming translator for {src!r} → {tgt!r}. "
            f"Supported: anthropic↔openai, google→anthropic."
        )

    def translate_chunk(self, raw_line: str) -> List[str]:
        """
        Translate one raw SSE line.

        Args:
            raw_line: Raw "data: {...}" or "data: [DONE]" line (with or without
                      trailing newlines).

        Returns:
            List of output SSE lines (each a "data: ..." string, no trailing newline).
            Empty list if the chunk produces no output.
            ["data: [DONE]"] for the stream terminator.
        """
        raw_line = raw_line.strip()

        # Passthrough mode (same provider)
        if self._impl is None:
            return [raw_line] if raw_line else []

        if raw_line == "data: [DONE]":
            return [_sse_done()]

        event = _parse_sse_line(raw_line)
        if event is None:
            return []

        if isinstance(self._impl, _AnthropicToOpenAIStream):
            result = self._impl.translate(event)
            return [result] if result is not None else []
        else:
            # Returns list already
            return self._impl.translate(event)

    def translate_stream(self, raw_lines: Iterator[str]) -> Iterator[str]:
        """
        Translate an iterator of raw SSE lines into translated SSE lines.

        Yields translated lines (no trailing newlines). Caller should append
        "\\n\\n" for proper SSE framing.
        """
        for line in raw_lines:
            for out_line in self.translate_chunk(line):
                yield out_line
