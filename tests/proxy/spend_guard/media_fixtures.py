# SPDX-License-Identifier: Apache-2.0
"""Generated monochrome images; only dimensions and encoded sizes match evidence."""

from __future__ import annotations

import base64
import struct
import zlib

IMAGE_SHAPES = [
    ["jpeg", 706, 576, 38520],
    ["jpeg", 706, 576, 38520],
    ["jpeg", 706, 576, 44748],
    ["jpeg", 706, 576, 44748],
    ["jpeg", 706, 576, 44748],
    ["jpeg", 706, 576, 44748],
    ["jpeg", 706, 576, 45292],
    ["jpeg", 706, 576, 45292],
    ["jpeg", 706, 576, 44860],
    ["jpeg", 706, 576, 44860],
    ["jpeg", 706, 576, 47724],
    ["jpeg", 706, 576, 47724],
    ["jpeg", 706, 576, 53288],
    ["jpeg", 706, 576, 53288],
    ["jpeg", 706, 576, 57188],
    ["jpeg", 706, 576, 57188],
    ["jpeg", 706, 576, 53376],
    ["jpeg", 706, 576, 53376],
    ["jpeg", 651, 672, 69632],
    ["jpeg", 651, 672, 69632],
    ["jpeg", 651, 672, 81212],
    ["jpeg", 651, 672, 81212],
    ["jpeg", 651, 672, 80656],
    ["jpeg", 651, 672, 80656],
    ["jpeg", 651, 672, 69752],
    ["jpeg", 651, 672, 39764],
    ["jpeg", 651, 672, 39764],
    ["jpeg", 651, 672, 42328],
    ["jpeg", 651, 672, 42328],
    ["jpeg", 651, 672, 46820],
    ["jpeg", 651, 672, 46820],
    ["jpeg", 651, 672, 81292],
    ["jpeg", 651, 672, 81292],
    ["jpeg", 651, 672, 81960],
    ["jpeg", 651, 672, 81960],
    ["jpeg", 651, 672, 79584],
    ["jpeg", 651, 672, 79584],
    ["jpeg", 651, 672, 53632],
    ["jpeg", 651, 672, 53632],
    ["jpeg", 543, 560, 50344],
    ["jpeg", 543, 560, 50344],
    ["jpeg", 644, 471, 49012],
    ["jpeg", 644, 471, 49012],
    ["jpeg", 773, 565, 22044],
    ["jpeg", 773, 565, 22044],
    ["jpeg", 773, 565, 36364],
    ["jpeg", 773, 565, 36364],
    ["jpeg", 773, 565, 43216],
    ["jpeg", 773, 565, 43216],
    ["jpeg", 644, 471, 27632],
    ["jpeg", 258, 188, 8536],
    ["png", 261, 191, 26388],
    ["jpeg", 322, 235, 11180],
]


def synthetic_image(kind: str, width: int, height: int, encoded_chars: int) -> str:
    target = encoded_chars // 4 * 3
    if kind == "jpeg":

        def segment(marker, payload):
            return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload

        prefix = b"\xff\xd8" + segment(0xDB, b"\0" + bytes([1]) * 64)
        prefix += segment(
            0xC0, bytes([8]) + struct.pack(">HH", height, width) + bytes([1, 1, 0x11, 0])
        )
        # A constant gray 8x8 block needs DC category zero and AC EOB.
        prefix += segment(0xC4, b"\0" + bytes([1] + [0] * 15) + b"\0")
        prefix += segment(0xC4, bytes([0x10]) + bytes([1] + [0] * 15) + b"\0")
        bits = ((width + 7) // 8) * ((height + 7) // 8) * 2
        entropy = bytes(bits // 8)
        if bits % 8:
            entropy += bytes([(1 << (8 - bits % 8)) - 1])
        suffix = segment(0xDA, bytes([1, 1, 0, 0, 63, 0])) + entropy + b"\xff\xd9"
        padding = target - len(prefix) - len(suffix)
        while padding:
            size = min(padding, 65537)
            if 0 < padding - size < 4:
                size -= 4
            assert size >= 4
            prefix += segment(0xFE, b"x" * (size - 4))
            padding -= size
        raw = prefix + suffix
    else:

        def chunk(kind, payload):
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload))
            )

        raw = b"\x89PNG\r\n\x1a\n"
        raw += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        raw += chunk(b"IDAT", zlib.compress(bytes((width + 1) * height)))
        padding = target - len(raw) - 24
        assert padding >= 8
        raw += chunk(b"tEXt", b"padding\0" + b"x" * (padding - 8))
        raw += chunk(b"IEND", b"")
    encoded = base64.b64encode(raw).decode()
    assert len(encoded) == encoded_chars
    return encoded


def image_block(kind="jpeg", width=706, height=576, size=38520):
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": f"image/{kind}",
            "data": synthetic_image(kind, width, height, size),
        },
    }


def workload():
    images = [image_block(*row) for row in IMAGE_SHAPES]
    # About 0.9M characters of synthetic prose and tool JSON, plus thinking.
    return {
        "model": "claude-opus-5-5",
        "max_tokens": 128000,
        "system": "synthetic context " * 22000,
        "messages": [
            {"role": "user", "content": "review the synthetic fixture"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "reason " * 10000},
                    {
                        "type": "tool_use",
                        "id": "fixture",
                        "name": "inspect",
                        "input": {"code": "return value;\n" * 30000},
                    },
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "fixture", "content": images}],
            },
        ],
    }
