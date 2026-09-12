"""Reconstructed client feature shapes; no captured prompts or provider evidence.

The fixed no-tools fields came from a confined installed client 2.1.267
metadata-only capture. Text and identifiers below are synthetic replacements;
this is deliberately not a copy of the captured request bytes.
"""

import json

CAPTURE_METADATA_SHA256 = "48f30ac0392923bf473d0d4b447f280dc933690bfdb7bfb9ad4ef7b129421c78"
BETA = ",".join(
    (
        "claude-code-20250219",
        "context-management-2025-06-27",
        "effort-2025-11-24",
        "extended-cache-ttl-2025-04-11",
        "interleaved-thinking-2025-05-14",
        "oauth-2025-04-20",
        "prompt-caching-scope-2026-01-05",
        "thinking-token-count-2026-05-13",
    )
)


def native_text_body(*, stream=True, max_tokens=32000):
    return {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "stream": stream,
        "tools": [],
        "system": [
            {"type": "text", "text": "synthetic system"},
            {
                "type": "text",
                "text": "synthetic policy",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "synthetic context"},
                    {"type": "text", "text": "synthetic history"},
                    {
                        "type": "text",
                        "text": "synthetic input",
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    },
                    {
                        "type": "text",
                        "text": "synthetic work",
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    },
                ],
            }
        ],
        "metadata": {
            "user_id": json.dumps(
                {
                    "account_uuid": "synthetic-account",
                    "device_id": "synthetic-device",
                    "session_id": "synthetic-session",
                }
            )
        },
        "thinking": {"type": "adaptive", "display": "omitted"},
        "output_config": {"effort": "high"},
        "context_management": {"edits": [{"keep": "all", "type": "clear_thinking_20251015"}]},
    }
