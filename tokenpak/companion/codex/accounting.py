# SPDX-License-Identifier: Apache-2.0
"""No-body accounting receipts for explicit ``tokenpak codex`` runs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "tokenpak.codex.accounting_receipt.v1"

_MODEL_FLAGS = {"-m", "--model"}
_VALUE_FLAGS = {
    "-C",
    "-c",
    "-i",
    "-m",
    "-o",
    "-s",
    "--ask-for-approval",
    "--cd",
    "--color",
    "--config",
    "--image",
    "--model",
    "--model-provider",
    "--output-schema",
    "--profile",
    "--sandbox",
}
_CODEX_SUBCOMMANDS = {
    "apply",
    "debug",
    "doctor",
    "exec",
    "e",
    "login",
    "logout",
    "mcp",
    "review",
    "sandbox",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def model_from_args(args: list[str]) -> str | None:
    """Return an explicit Codex model from argv when one is present."""
    for index, token in enumerate(args):
        if token in _MODEL_FLAGS and index + 1 < len(args):
            return args[index + 1]
        if token.startswith("--model="):
            return token.split("=", 1)[1] or None
    return None


def redact_argv(args: list[str]) -> list[str]:
    """Return a structural argv record without positional prompt/body content."""
    redacted: list[str] = []
    redact_next_for: str | None = None
    positional_seen = False

    for index, token in enumerate(args):
        if redact_next_for is not None:
            if redact_next_for in _MODEL_FLAGS:
                redacted.append(token)
            else:
                redacted.append("<redacted-value>")
            redact_next_for = None
            continue

        if token in _CODEX_SUBCOMMANDS and index == 0:
            redacted.append(token)
            continue

        if token in _VALUE_FLAGS:
            redacted.append(token)
            redact_next_for = token
            continue

        if token.startswith("--"):
            flag, has_value, value = token.partition("=")
            if flag in _MODEL_FLAGS and has_value:
                redacted.append(f"{flag}={value}")
            elif has_value:
                redacted.append(f"{flag}=<redacted-value>")
            else:
                redacted.append(token)
            continue

        if token.startswith("-") and len(token) > 1:
            redacted.append(token)
            continue

        if not positional_seen:
            redacted.append("<redacted-positional>")
            positional_seen = True

    return redacted


def usage_from_event(event: dict[str, Any]) -> dict[str, int | None]:
    """Extract numeric usage fields from a Codex JSON event without retaining text."""
    found: dict[str, int | None] = {
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }

    def _int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            input_tokens = _int(value.get("input_tokens"))
            prompt_tokens = _int(value.get("prompt_tokens"))
            output_tokens = _int(value.get("output_tokens"))
            completion_tokens = _int(value.get("completion_tokens"))
            cached_tokens = _int(value.get("cached_input_tokens"))
            cache_read = _int(value.get("cache_read_input_tokens"))
            total_tokens = _int(value.get("total_tokens"))

            if input_tokens is not None:
                found["input_tokens"] = input_tokens
            elif prompt_tokens is not None:
                found["input_tokens"] = prompt_tokens
            if output_tokens is not None:
                found["output_tokens"] = output_tokens
            elif completion_tokens is not None:
                found["output_tokens"] = completion_tokens
            if cached_tokens is not None:
                found["cached_input_tokens"] = cached_tokens
            elif cache_read is not None:
                found["cached_input_tokens"] = cache_read
            if total_tokens is not None:
                found["total_tokens"] = total_tokens

            for child in value.values():
                _walk(child)
        elif isinstance(value, list):
            for child in value:
                _walk(child)

    _walk(event)
    if found["total_tokens"] is None:
        input_tokens = found["input_tokens"]
        output_tokens = found["output_tokens"]
        if input_tokens is not None and output_tokens is not None:
            found["total_tokens"] = input_tokens + output_tokens
    return found


def usage_from_json_line(line: str) -> dict[str, int | None]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return {
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
    if not isinstance(event, dict):
        return {
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
    return usage_from_event(event)


def merge_usage(
    current: dict[str, int | None], update: dict[str, int | None]
) -> dict[str, int | None]:
    merged = dict(current)
    for key, value in update.items():
        if value is not None:
            merged[key] = value
    return merged


def empty_usage() -> dict[str, int | None]:
    return {
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def build_receipt(
    *,
    run_id: str,
    codex_args: list[str],
    cwd: str,
    started_at: str,
    ended_at: str,
    duration_ms: int,
    exit_code: int,
    status: str,
    setup: dict[str, Any],
    usage: dict[str, int | None] | None = None,
    missing_evidence: list[str] | None = None,
) -> dict[str, Any]:
    usage = usage or empty_usage()
    missing = list(missing_evidence or [])
    if all(value is None for value in usage.values()):
        missing.append("codex_token_usage_unavailable")
    if usage.get("cached_input_tokens") is None:
        missing.append("provider_cached_input_tokens_unavailable")

    return {
        "schema": SCHEMA,
        "receipt_type": "codex_cli_no_body_accounting",
        "run_id": run_id,
        "created_at": utc_now(),
        "status": status,
        "privacy": {
            "prompt_body_stored": False,
            "completion_body_stored": False,
            "stdout_stored": False,
            "stderr_stored": False,
            "body_capture_mode": "disabled",
        },
        "command": {
            "program": "codex",
            "argv_redacted": ["codex", *redact_argv(codex_args)],
            "model": model_from_args(codex_args),
            "cwd": cwd,
        },
        "process": {
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "exit_code": exit_code,
        },
        "metrics": {
            "input_tokens": usage.get("input_tokens"),
            "cached_input_tokens": usage.get("cached_input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "estimated_cost_usd": None,
            "billed_cost_usd": None,
        },
        "attribution": {
            "receipt_wrapper_active": bool(
                setup.get("receipt_wrapper_active", True)
            ),
            "tokenpak_mechanism_active": bool(
                setup.get(
                    "tokenpak_mechanism_active",
                    setup.get("setup_completed"),
                )
            ),
            "tokenpak_value_mechanism_active": bool(
                setup.get(
                    "tokenpak_mechanism_active",
                    setup.get("setup_completed"),
                )
            ),
            "provider_native_caching_involved": (
                usage.get("cached_input_tokens") is not None
            ),
            "cache_origin": "upstream"
            if usage.get("cached_input_tokens") is not None
            else "unavailable",
            "tokenpak_attributable_savings": "unavailable",
        },
        "tokenpak_setup": setup,
        "missing_evidence": sorted(set(missing)),
    }


def write_receipt(path: str | os.PathLike[str], receipt: dict[str, Any]) -> Path:
    dest = Path(path).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return dest
