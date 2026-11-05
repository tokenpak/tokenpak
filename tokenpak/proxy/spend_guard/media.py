# SPDX-License-Identifier: Apache-2.0
"""Bounded, offline media estimates. Never tokenize an encoded image payload.

Formulas and limits (retrieved 2026-11-03):
https://platform.claude.com/docs/en/build-with-claude/vision
https://platform.claude.com/docs/en/build-with-claude/vision-coordinates
https://developers.openai.com/api/docs/guides/images-vision
https://platform.claude.com/docs/en/build-with-claude/pdf-support

Unknown dimensions use the selected model/detail's maximum image cost. URLs
and file IDs are never fetched. Only image headers are decoded, not pixels.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
import struct
from typing import Any

_HEADER_BYTES = 1_048_576
_PDF_BYTES = 32 * 1024 * 1024
UNKNOWN_PDF_PAGES = 600
PDF_TEXT_TOKENS_PER_PAGE = 3000


def _decode(data: object, limit: int) -> bytes:
    if not isinstance(data, str):
        return b""
    if data.startswith("data:"):
        header, sep, data = data.partition(",")
        if not sep or ";base64" not in header:
            return b""
    try:
        # Bound decoding independently of the supplied encoded size.
        return base64.b64decode(data[: ((limit + 2) // 3) * 4], validate=True)[:limit]
    except (ValueError, binascii.Error):
        return b""


def image_dimensions(data: object) -> tuple[int, int] | None:
    """Read PNG, JPEG, GIF or WebP dimensions from a bounded header prefix."""
    raw = _decode(data, _HEADER_BYTES)
    size = None
    try:
        if raw.startswith(b"\x89PNG\r\n\x1a\n") and raw[12:16] == b"IHDR":
            size = struct.unpack(">II", raw[16:24])
        elif raw[:6] in (b"GIF87a", b"GIF89a"):
            size = struct.unpack("<HH", raw[6:10])
        elif raw[:2] == b"\xff\xd8":
            offset = 2
            while offset + 4 <= len(raw):
                if raw[offset] != 0xFF:
                    break
                while offset < len(raw) and raw[offset] == 0xFF:
                    offset += 1
                marker = raw[offset]
                offset += 1
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    continue
                if marker in (0xD9, 0xDA):
                    break
                length = int.from_bytes(raw[offset : offset + 2], "big")
                if length < 2 or offset + length > len(raw):
                    break
                if marker in (
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                ):
                    height, width = struct.unpack(">HH", raw[offset + 3 : offset + 7])
                    size = width, height
                    break
                offset += length
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            if raw[12:16] == b"VP8X" and len(raw) >= 30:
                size = (
                    1 + int.from_bytes(raw[24:27], "little"),
                    1 + int.from_bytes(raw[27:30], "little"),
                )
            elif raw[12:16] == b"VP8L" and len(raw) >= 25 and raw[20:21] == b"\x2f":
                bits = int.from_bytes(raw[21:25], "little")
                size = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            elif raw[12:16] == b"VP8 " and raw[23:26] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", raw[26:30])
                size = width & 0x3FFF, height & 0x3FFF
    except (IndexError, struct.error):
        return None
    return size if size and all(0 < n <= 1_000_000 for n in size) else None


def _claude_limits(model: str) -> tuple[int, int]:
    version = re.search(r"claude-(?:opus-|sonnet-|haiku-)?(\d+)[.-](\d+)", model)
    if version and tuple(map(int, version.groups())) < (4, 7):
        return 1568, 1568
    # Unknown Claude versions use the conservative high-resolution tier.
    return 2576, 4784


def _claude_tokens(width: int, height: int, edge: int, cap: int) -> int:
    def patches(w: int, h: int) -> int:
        return math.ceil(w / 28) * math.ceil(h / 28)

    def fits(w: int, h: int) -> bool:
        return (
            math.ceil(w / 28) * 28 <= edge
            and math.ceil(h / 28) * 28 <= edge
            and patches(w, h) <= cap
        )

    if fits(width, height):
        return patches(width, height)
    width, height = max(width, height), min(width, height)
    aspect = width / height
    lo, hi = 1, width
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect), 1)):
            lo = mid
        else:
            hi = mid
    return min(cap, patches(lo, max(round(lo / aspect), 1)))


def _openai_rules(model: str, detail: str) -> tuple[str, int, int | None, float]:
    # Return (method, edge/base, patch budget/tile cost, multiplier).
    if model.startswith("gpt-4o-mini"):
        return "tiles", 2833, 5667, 1.0
    if model.startswith(("gpt-4o", "gpt-4.1")) and not model.startswith(
        ("gpt-4.1-mini", "gpt-4.1-nano")
    ):
        return "tiles", 85, 170, 1.0
    if model == "gpt-5" or model.startswith("gpt-5.1"):
        return "tiles", 70, 140, 1.0
    if model.startswith(("o1", "o3")):
        return "tiles", 75, 150, 1.0
    multiplier = 1.2
    for prefix, factor in (
        ("gpt-4.1-mini", 1.62),
        ("gpt-4.1-nano", 2.46),
        ("gpt-5-nano", 1.5),
        ("o4-mini", 1.72),
    ):
        if model.startswith(prefix):
            multiplier = factor
            break
    if model.startswith(("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")):
        if detail == "low":
            return "patches", 512, None, multiplier
        if detail == "high":
            return "patches", 65535 if model.startswith("gpt-6-astra") else 2048, 2500, multiplier
        return "patches", 65535, None, multiplier
    if model.startswith(("gpt-5.5", "gpt-5.4")):
        if detail == "original" or (detail == "auto" and model.startswith("gpt-5.5")):
            return "patches", 6000, 10000, multiplier
        if detail == "low":
            return "patches", 512 if model.startswith("gpt-5.5") else 2048, 6144, multiplier
        return "patches", 2048, 2500, multiplier
    if model.startswith(("gpt-5.2", "gpt-4.1-mini")):
        return "patches", 2048, 6144, multiplier
    if model.startswith(("gpt-5-mini", "gpt-5-nano", "gpt-4.1-nano", "o4-mini")):
        return "patches", 2048, 1536, multiplier
    # Unlisted model: do not pretend a smaller known-model budget applies.
    return "unknown", 0, None, 1.0


def image_tokens(block: dict[str, Any], model: str, *, anthropic: bool) -> int:
    source = block.get("source") or {}
    data = source.get("data") if isinstance(source, dict) else None
    data = data or block.get("image_url")
    if isinstance(data, dict):
        data = data.get("url")
    size = image_dimensions(data)
    if anthropic:
        edge, cap = _claude_limits(model)
        return _claude_tokens(*size, edge, cap) if size else cap
    detail = str(block.get("detail") or "auto")
    method, edge, budget, multiplier = _openai_rules(model, detail)
    if method == "unknown":
        return 48169  # largest supported tile cap: 2833 + 8 * 5667
    if method == "tiles":
        if detail == "low":
            return edge
        if size is None:
            return edge + 8 * int(budget or 0)
        w, h = size
        scale = min(1.0, 2048 / max(w, h), 768 / min(w, h))
        w, h = max(1, math.floor(w * scale)), max(1, math.floor(h * scale))
        return edge + math.ceil(w / 512) * math.ceil(h / 512) * int(budget or 0)
    cap = min(budget or 30000, math.ceil(edge / 32) ** 2, 30000)
    if size is None:
        return math.ceil(cap * multiplier)
    w, h = size
    scale = min(1.0, edge / max(w, h))
    w, h = max(1, round(w * scale)), max(1, round(h * scale))
    if budget and math.ceil(w / 32) * math.ceil(h / 32) > budget:
        scale = math.sqrt(1024 * budget / (w * h))
        x, y = w * scale / 32, h * scale / 32
        if min(x, y) < 1:
            # The published rounding formula degenerates to zero for a
            # sub-patch side. Use the selected cap instead of undercounting.
            return math.ceil(cap * multiplier)
        scale *= min(math.floor(x) / x, math.floor(y) / y)
        w, h = max(1, math.floor(w * scale)), max(1, math.floor(h * scale))
    return math.ceil(min(cap, math.ceil(w / 32) * math.ceil(h / 32)) * multiplier)


def document_tokens(block: dict[str, Any], model: str, *, anthropic: bool) -> int:
    """PDF text allowance plus one maximum-cost image per page.

    Read the declared page-tree count only in complete, unencrypted PDFs
    with uncompressed objects. Opaque/compressed/encrypted files and URLs
    use 600 pages (the documented maximum); never infer pages from bytes.
    """
    source = block.get("source") or {}
    data = source.get("data") if isinstance(source, dict) else None
    data = data or block.get("file_data")
    raw = _decode(data, _PDF_BYTES + 1)
    pages = UNKNOWN_PDF_PAGES
    if (
        raw.startswith(b"%PDF-")
        and len(raw) <= _PDF_BYTES
        and b"%%EOF" in raw[-1024:]
        and not re.search(rb"/(?:ObjStm|Encrypt)\b", raw)
    ):
        counts = []
        for obj in re.findall(rb"\b\d+\s+\d+\s+obj\b(.*?)endobj", raw, re.S):
            dictionary = obj.split(b"stream", 1)[0]
            if re.search(rb"/Type\s*/Pages\b", dictionary):
                count = re.search(rb"/Count\s+(\d+)\b", dictionary)
                if count and int(count[1]) > 0:
                    counts.append(int(count[1]))
        if counts:
            pages = max(counts)
    return pages * (PDF_TEXT_TOKENS_PER_PAGE + image_tokens({}, model, anthropic=anthropic))
