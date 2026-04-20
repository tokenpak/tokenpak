"""
TokenPak Request/Response Format Translator (F.2)

Translates LLM API requests and responses between provider formats:
  - Anthropic ↔ OpenAI
  - Anthropic ↔ Google
  - OpenAI  ↔ Anthropic  (bidirectional helpers)

Handles:
  - System prompt placement differences
  - Message role name differences ("assistant" vs "model")
  - Content block formats (string vs list-of-parts)
  - Tool/function calling schemas
  - Streaming format differences (passthrough — callers handle SSE framing)
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def translate_request(
    data: Dict[str, Any],
    source_provider: str,
    target_provider: str,
) -> Dict[str, Any]:
    """
    Translate a request body dict from *source_provider* format to
    *target_provider* format.

    Args:
        data: Parsed request body (dict, will not be mutated)
        source_provider: "anthropic" | "openai" | "google"
        target_provider: "anthropic" | "openai" | "google"

    Returns:
        New dict in the target provider's format.

    Raises:
        ValueError: If the translation pair is not supported.
    """
    if source_provider == target_provider:
        return copy.deepcopy(data)

    key = (source_provider, target_provider)
    translator = _REQUEST_TRANSLATORS.get(key)
    if translator is None:
        raise ValueError(
            f"No request translator for {source_provider!r} → {target_provider!r}. "
            f"Supported pairs: {sorted(_REQUEST_TRANSLATORS.keys())}"
        )
    return translator(copy.deepcopy(data))


def translate_response(
    data: Dict[str, Any],
    source_provider: str,
    target_provider: str,
) -> Dict[str, Any]:
    """
    Translate a (non-streaming) response body from *source_provider* format
    to *target_provider* format.

    Streaming SSE frames are not handled here — callers must wrap/unwrap them.
    """
    if source_provider == target_provider:
        return copy.deepcopy(data)

    key = (source_provider, target_provider)
    translator = _RESPONSE_TRANSLATORS.get(key)
    if translator is None:
        raise ValueError(f"No response translator for {source_provider!r} → {target_provider!r}.")
    return translator(copy.deepcopy(data))


# ---------------------------------------------------------------------------
# Internal helpers — content normalisation
# ---------------------------------------------------------------------------


def _text_of(content: Any) -> str:
    """Extract plain text from Anthropic/OpenAI content (str or list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item["text"]))
                elif item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return str(content)


def _as_content_blocks(content: Any) -> List[Dict[str, Any]]:
    """Ensure content is a list of Anthropic-style content blocks."""
    if isinstance(content, list):
        # Already a list — normalise each item
        result: List[Dict[str, Any]] = []
        for item in content:
            if isinstance(item, str):
                result.append({"type": "text", "text": item})
            elif isinstance(item, dict):
                result.append(item)
        return result
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _tools_anthropic_to_openai(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Anthropic tool definitions to OpenAI function-calling format."""
    result: List[Dict[str, Any]] = []
    for tool in tools:
        fn: Dict[str, Any] = {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        }
        result.append({"type": "function", "function": fn})
    return result


def _tools_openai_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI function-calling tools to Anthropic tool definitions."""
    result: List[Dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", {}) if tool.get("type") == "function" else tool
        entry: Dict[str, Any] = {
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        }
        result.append(entry)
    return result


def _tool_use_anthropic_to_openai(content: Any) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Convert Anthropic tool_use content blocks to OpenAI tool_calls.
    Returns (text_content, tool_calls_list).
    """
    if not isinstance(content, list):
        return _text_of(content), []

    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for idx, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id", f"call_{idx}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                }
            )

    text = "\n".join(text_parts) if text_parts else None
    return text, tool_calls


def _tool_calls_openai_to_anthropic(
    tool_calls: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert OpenAI tool_calls to Anthropic tool_use content blocks."""
    blocks: List[Dict[str, Any]] = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        try:
            input_data = json.loads(fn.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            input_data = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "input": input_data,
            }
        )
    return blocks


# ---------------------------------------------------------------------------
# Anthropic → OpenAI
# ---------------------------------------------------------------------------


def _anthropic_to_openai_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate Anthropic /v1/messages request to OpenAI chat completions."""
    messages: List[Dict[str, Any]] = []

    # System prompt → first message with role="system"
    system = data.get("system")
    if system:
        system_text = _text_of(system)
        if system_text:
            messages.append({"role": "system", "content": system_text})

    # Conversation messages
    for msg in data.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")

        # Convert tool_use blocks
        if isinstance(content, list):
            tool_use_blocks = [
                b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            tool_result_blocks = [
                b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
            ]

            if tool_use_blocks:
                text, tool_calls = _tool_use_anthropic_to_openai(content)
                oai_msg: Dict[str, Any] = {"role": role, "content": text}
                if tool_calls:
                    oai_msg["tool_calls"] = tool_calls
                messages.append(oai_msg)
                continue

            if tool_result_blocks:
                # Anthropic tool_result → OpenAI tool message (one per block)
                for block in tool_result_blocks:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": _text_of(block.get("content", "")),
                        }
                    )
                continue

        messages.append({"role": role, "content": _text_of(content)})

    out: Dict[str, Any] = {
        "model": data.get("model", "gpt-4o"),
        "messages": messages,
        "stream": data.get("stream", False),
    }

    if "max_tokens" in data:
        out["max_tokens"] = data["max_tokens"]
    if "temperature" in data:
        out["temperature"] = data["temperature"]
    if "top_p" in data:
        out["top_p"] = data["top_p"]
    if "stop_sequences" in data:
        out["stop"] = data["stop_sequences"]

    # Tools
    tools = data.get("tools")
    if tools:
        out["tools"] = _tools_anthropic_to_openai(tools)

    return out


# ---------------------------------------------------------------------------
# OpenAI → Anthropic
# ---------------------------------------------------------------------------


def _openai_to_anthropic_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate OpenAI chat completions request to Anthropic /v1/messages."""
    system_parts: List[str] = []
    messages: List[Dict[str, Any]] = []

    for msg in data.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")

        if role == "system":
            system_parts.append(_text_of(content))
            continue

        if role == "tool":
            # OpenAI tool message → Anthropic tool_result block in a user message
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": _text_of(content),
                        }
                    ],
                }
            )
            continue

        if role == "assistant" and tool_calls:
            # Build Anthropic content: text blocks + tool_use blocks
            ant_content: List[Dict[str, Any]] = []
            if content:
                ant_content.append({"type": "text", "text": _text_of(content)})
            ant_content.extend(_tool_calls_openai_to_anthropic(tool_calls))
            messages.append({"role": "assistant", "content": ant_content})
            continue

        messages.append({"role": role, "content": _text_of(content)})

    out: Dict[str, Any] = {
        "model": data.get("model", "claude-sonnet-4-5"),
        "messages": messages,
        "max_tokens": data.get("max_tokens", 4096),
        "stream": data.get("stream", False),
    }

    if system_parts:
        out["system"] = "\n\n".join(system_parts)
    if "temperature" in data:
        out["temperature"] = data["temperature"]
    if "top_p" in data:
        out["top_p"] = data["top_p"]
    if "stop" in data:
        out["stop_sequences"] = data["stop"] if isinstance(data["stop"], list) else [data["stop"]]

    # Tools
    tools = data.get("tools")
    if tools:
        out["tools"] = _tools_openai_to_anthropic(tools)

    return out


# ---------------------------------------------------------------------------
# Anthropic → Google
# ---------------------------------------------------------------------------


def _anthropic_to_google_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate Anthropic request to Google generateContent format."""
    contents: List[Dict[str, Any]] = []

    for msg in data.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")
        # Google uses "model" instead of "assistant"
        google_role = "model" if role == "assistant" else "user"

        parts: List[Dict[str, Any]] = []
        for block in _as_content_blocks(content):
            btype = block.get("type")
            if btype == "text":
                parts.append({"text": block.get("text", "")})
            elif btype == "tool_use":
                parts.append(
                    {
                        "functionCall": {
                            "name": block.get("name", ""),
                            "args": block.get("input", {}),
                        }
                    }
                )
            elif btype == "tool_result":
                parts.append(
                    {
                        "functionResponse": {
                            "name": block.get("tool_use_id", ""),
                            "response": {"content": block.get("content", "")},
                        }
                    }
                )
            else:
                # Pass through unknown block types as-is
                parts.append(block)

        if parts:
            contents.append({"role": google_role, "parts": parts})

    out: Dict[str, Any] = {"contents": contents}

    # System prompt
    system = data.get("system")
    if system:
        system_text = _text_of(system)
        if system_text:
            out["systemInstruction"] = {"parts": [{"text": system_text}]}

    # Generation config
    gen_config: Dict[str, Any] = {}
    if "max_tokens" in data:
        gen_config["maxOutputTokens"] = data["max_tokens"]
    if "temperature" in data:
        gen_config["temperature"] = data["temperature"]
    if "top_p" in data:
        gen_config["topP"] = data["top_p"]
    if "stop_sequences" in data:
        gen_config["stopSequences"] = data["stop_sequences"]
    if gen_config:
        out["generationConfig"] = gen_config

    # Tools
    tools = data.get("tools")
    if tools:
        fn_decls = []
        for tool in tools:
            fn_decls.append(
                {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                }
            )
        out["tools"] = [{"functionDeclarations": fn_decls}]

    return out


# ---------------------------------------------------------------------------
# Google → Anthropic
# ---------------------------------------------------------------------------


def _google_to_anthropic_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate Google generateContent request to Anthropic format."""
    messages: List[Dict[str, Any]] = []

    for content in data.get("contents", []):
        role = content.get("role", "user")
        # Google "model" → Anthropic "assistant"
        ant_role = "assistant" if role == "model" else "user"
        parts = content.get("parts", [])

        ant_content: List[Dict[str, Any]] = []
        for part in parts:
            if "text" in part:
                ant_content.append({"type": "text", "text": part["text"]})
            elif "functionCall" in part:
                fc = part["functionCall"]
                ant_content.append(
                    {
                        "type": "tool_use",
                        "id": fc.get("name", ""),
                        "name": fc.get("name", ""),
                        "input": fc.get("args", {}),
                    }
                )
            elif "functionResponse" in part:
                fr = part["functionResponse"]
                ant_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": fr.get("name", ""),
                        "content": str(fr.get("response", {}).get("content", "")),
                    }
                )
            else:
                # Pass through unknown parts
                ant_content.append({"type": "text", "text": str(part)})

        if ant_content:
            # Simplify to string if single text block
            if len(ant_content) == 1 and ant_content[0].get("type") == "text":
                messages.append({"role": ant_role, "content": ant_content[0]["text"]})
            else:
                messages.append({"role": ant_role, "content": ant_content})

    out: Dict[str, Any] = {
        "model": data.get("model", "claude-sonnet-4-5"),
        "messages": messages,
        "max_tokens": 4096,
        "stream": False,
    }

    # System instruction
    sys_instr = data.get("systemInstruction", {})
    if isinstance(sys_instr, dict):
        sys_parts = sys_instr.get("parts", [])
        sys_text = "\n".join(p.get("text", "") for p in sys_parts if isinstance(p, dict))
        if sys_text:
            out["system"] = sys_text

    # Generation config
    gen_config = data.get("generationConfig", {})
    if "maxOutputTokens" in gen_config:
        out["max_tokens"] = gen_config["maxOutputTokens"]
    if "temperature" in gen_config:
        out["temperature"] = gen_config["temperature"]
    if "topP" in gen_config:
        out["top_p"] = gen_config["topP"]
    if "stopSequences" in gen_config:
        out["stop_sequences"] = gen_config["stopSequences"]

    # Tools
    tools = data.get("tools", [])
    if tools:
        ant_tools: List[Dict[str, Any]] = []
        for tool_group in tools:
            for fn_decl in tool_group.get("functionDeclarations", []):
                ant_tools.append(
                    {
                        "name": fn_decl.get("name", ""),
                        "description": fn_decl.get("description", ""),
                        "input_schema": fn_decl.get("parameters", {}),
                    }
                )
        if ant_tools:
            out["tools"] = ant_tools

    return out


# ---------------------------------------------------------------------------
# Response translators
# ---------------------------------------------------------------------------


def _anthropic_to_openai_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Anthropic response to OpenAI chat completion format."""
    # Extract content
    content_blocks = data.get("content", [])
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for idx, block in enumerate(content_blocks):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id", f"call_{idx}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                }
            )

    message: Dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    stop_reason = data.get("stop_reason", "end_turn")
    finish_reason = "stop"
    if stop_reason == "tool_use":
        finish_reason = "tool_calls"
    elif stop_reason == "max_tokens":
        finish_reason = "length"

    usage = data.get("usage", {})
    return {
        "id": data.get("id", ""),
        "object": "chat.completion",
        "model": data.get("model", ""),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


def _openai_to_anthropic_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert OpenAI chat completion response to Anthropic format."""
    choices = data.get("choices", [{}])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})

    content: List[Dict[str, Any]] = []
    msg_content = message.get("content")
    if msg_content:
        content.append({"type": "text", "text": _text_of(msg_content)})

    for tc in message.get("tool_calls", []):
        fn = tc.get("function", {})
        try:
            input_data = json.loads(fn.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            input_data = {}
        content.append(
            {
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "input": input_data,
            }
        )

    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = "end_turn"
    if finish_reason == "tool_calls":
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"

    usage = data.get("usage", {})
    return {
        "id": data.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": data.get("model", ""),
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ---------------------------------------------------------------------------
# Translator dispatch tables
# ---------------------------------------------------------------------------

_REQUEST_TRANSLATORS = {
    ("anthropic", "openai"): _anthropic_to_openai_request,
    ("openai", "anthropic"): _openai_to_anthropic_request,
    ("anthropic", "google"): _anthropic_to_google_request,
    ("google", "anthropic"): _google_to_anthropic_request,
    ("openai", "google"): lambda d: _anthropic_to_google_request(_openai_to_anthropic_request(d)),
    ("google", "openai"): lambda d: _anthropic_to_openai_request(_google_to_anthropic_request(d)),
}

_RESPONSE_TRANSLATORS = {
    ("anthropic", "openai"): _anthropic_to_openai_response,
    ("openai", "anthropic"): _openai_to_anthropic_response,
}


# ---------------------------------------------------------------------------
# Google → Anthropic (response)
# ---------------------------------------------------------------------------


def _google_to_anthropic_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Google generateContent response to Anthropic /v1/messages format."""
    candidates = data.get("candidates", [{}])
    candidate = candidates[0] if candidates else {}
    g_content = candidate.get("content", {})
    parts = g_content.get("parts", [])

    content: List[Dict[str, Any]] = []
    for part in parts:
        if "thought" in part:
            # Google thought/reasoning parts → skip gracefully (not in Anthropic response)
            pass
        elif "text" in part:
            content.append({"type": "text", "text": part["text"]})
        elif "functionCall" in part:
            fc = part["functionCall"]
            content.append(
                {
                    "type": "tool_use",
                    "id": fc.get("name", ""),
                    "name": fc.get("name", ""),
                    "input": fc.get("args", {}),
                }
            )
        else:
            # Unknown part type → convert to text
            content.append({"type": "text", "text": str(part)})

    # Map finish reason
    finish_reason = candidate.get("finishReason", "STOP")
    stop_reason_map = {
        "STOP": "end_turn",
        "MAX_TOKENS": "max_tokens",
        "SAFETY": "end_turn",
        "RECITATION": "end_turn",
        "TOOL_CODE": "tool_use",
    }
    stop_reason = stop_reason_map.get(finish_reason, "end_turn")
    if any(b.get("type") == "tool_use" for b in content):
        stop_reason = "tool_use"

    # Usage metadata
    usage_meta = data.get("usageMetadata", {})
    return {
        "id": f"google-{id(data)}",
        "type": "message",
        "role": "assistant",
        "model": data.get("modelVersion", ""),
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": usage_meta.get("promptTokenCount", 0),
            "output_tokens": usage_meta.get("candidatesTokenCount", 0),
        },
    }


# ---------------------------------------------------------------------------
# Anthropic → Google (response)
# ---------------------------------------------------------------------------


def _anthropic_to_google_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Anthropic /v1/messages response to Google generateContent format."""
    content_blocks = data.get("content", [])
    parts: List[Dict[str, Any]] = []

    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append({"text": block.get("text", "")})
        elif btype == "tool_use":
            parts.append(
                {
                    "functionCall": {
                        "name": block.get("name", ""),
                        "args": block.get("input", {}),
                    }
                }
            )
        elif btype == "thinking":
            # Thinking blocks → gracefully skip (Google doesn't support this)
            pass

    stop_reason = data.get("stop_reason", "end_turn")
    finish_reason_map = {
        "end_turn": "STOP",
        "max_tokens": "MAX_TOKENS",
        "tool_use": "TOOL_CODE",
        "stop_sequence": "STOP",
    }
    finish_reason = finish_reason_map.get(stop_reason, "STOP")

    usage = data.get("usage", {})
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": parts,
                },
                "finishReason": finish_reason,
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": usage.get("input_tokens", 0),
            "candidatesTokenCount": usage.get("output_tokens", 0),
            "totalTokenCount": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
        "modelVersion": data.get("model", ""),
    }


# Update response translator dispatch table with Google support
_RESPONSE_TRANSLATORS.update(
    {
        ("google", "anthropic"): _google_to_anthropic_response,
        ("anthropic", "google"): _anthropic_to_google_response,
        ("google", "openai"): lambda d: _anthropic_to_openai_response(  # type: ignore
            _google_to_anthropic_response(d)
        ),
        ("openai", "google"): lambda d: _anthropic_to_google_response(  # type: ignore
            _openai_to_anthropic_response(d)
        ),
    }
)
