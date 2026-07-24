"""Image processor for context-aware compression of documents, screenshots, and photos.

This module requires the optional ``tokenpak[image]`` extras::

    pip install tokenpak[image]

If Pillow is not installed the module degrades gracefully: :class:`ImageProcessor`
becomes a passthrough stub that logs a warning and returns the original bytes
unchanged.

Classification heuristics (pure-Python, no OpenCV):
- DOCUMENT : portrait aspect ratio (>1.2) OR limited unique colors (<100)
- SCREENSHOT: landscape/square AND few unique colors (<1 000) AND not portrait
- PHOTO     : rich color palette (≥1 000 unique colors)
"""

from __future__ import annotations

import io
import logging
import os
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Attempt to import Pillow; set a flag so the rest of the module can branch.
# ---------------------------------------------------------------------------
try:
    from PIL import (
        Image,
        ImageEnhance,
        ImageOps,
    )

    _PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PILLOW_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ImageType(Enum):
    DOCUMENT = "document"
    SCREENSHOT = "screenshot"
    PHOTO = "photo"


@dataclass
class CompressedImage:
    data: bytes
    original_size: int
    compressed_size: int
    compression_ratio: float
    image_type: ImageType


def classify(image_path: str) -> ImageType:
    """Classify an image as DOCUMENT, SCREENSHOT, or PHOTO.

    Uses color-histogram heuristics only (no OpenCV dependency):

    * DOCUMENT  — portrait aspect ratio (h/w > 1.2) **or** very few unique
                  colors (< 100 sampled from a 64×64 thumbnail)
    * SCREENSHOT— landscape/square aspect ratio **and** limited colors
                  (< 1 000) — typical for flat UI with solid backgrounds
    * PHOTO     — rich color palette (≥ 1 000 unique colors)

    Raises:
        ImportError: if Pillow is not installed (use ``pip install tokenpak[image]``).
        FileNotFoundError: if *image_path* does not exist.
    """
    if not _PILLOW_AVAILABLE:
        raise ImportError(
            "Pillow is required for image classification. Run: pip install tokenpak[image]"
        )

    with Image.open(image_path) as img:
        w, h = img.size
        aspect = h / w if w > 0 else 1.0

        # Downsample for fast color counting
        thumb = img.convert("RGB").resize((64, 64), Image.LANCZOS)
        _pdata = (
            thumb.get_flattened_data() if hasattr(thumb, "get_flattened_data") else thumb.getdata()
        )
        unique_colors = len(set(_pdata))

    if aspect > 1.2 or unique_colors < 100:
        return ImageType.DOCUMENT
    if unique_colors < 1000:
        return ImageType.SCREENSHOT
    return ImageType.PHOTO


def compress(
    image_path: str,
    mode: Optional[ImageType] = None,
) -> CompressedImage:
    """Compress an image using the strategy appropriate for its type.

    Args:
        image_path: Path to the source image file.
        mode: Override the auto-detected :class:`ImageType`.  When *None*
              (default) :func:`classify` is called automatically.

    Returns:
        A :class:`CompressedImage` with ``data`` containing the compressed
        bytes.

    Raises:
        ImportError: if Pillow is not installed.
        FileNotFoundError: if *image_path* does not exist.
    """
    if not _PILLOW_AVAILABLE:
        raise ImportError(
            "Pillow is required for image compression. Run: pip install tokenpak[image]"
        )

    original_size = os.path.getsize(image_path)

    if mode is None:
        mode = classify(image_path)

    with Image.open(image_path) as img:
        compressed_data = _compress_by_mode(img, mode)

    compressed_size = len(compressed_data)
    ratio = (original_size - compressed_size) / original_size if original_size else 0.0

    return CompressedImage(
        data=compressed_data,
        original_size=original_size,
        compressed_size=compressed_size,
        compression_ratio=ratio,
        image_type=mode,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _compress_by_mode(img: "Image.Image", mode: ImageType) -> bytes:
    buf = io.BytesIO()

    if mode == ImageType.DOCUMENT:
        # High-contrast grayscale, max 1 536 px on longest side
        img = img.convert("L")
        img.thumbnail((1536, 1536), Image.LANCZOS)
        img = ImageOps.autocontrast(img, cutoff=2)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
        img.save(buf, format="JPEG", quality=55, optimize=True)

    elif mode == ImageType.SCREENSHOT:
        # PNG quantization to 256 colours, max 1 024 px on longest side
        img = img.convert("RGBA")
        img.thumbnail((1024, 1024), Image.LANCZOS)
        img = img.convert("RGB")
        quantized = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        quantized.save(buf, format="PNG", optimize=True)

    else:  # PHOTO
        # WebP at quality 75, max 768 px on longest side
        img = img.convert("RGB")
        img.thumbnail((768, 768), Image.LANCZOS)
        img.save(buf, format="WEBP", quality=75, method=4)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Polymorphic processor (matches text/code/data interface)
# ---------------------------------------------------------------------------


class ImageProcessor:
    """Processor that wraps :func:`compress` in the tokenpak processor interface.

    If Pillow is not installed this class degrades to a **passthrough stub**:
    :meth:`process` returns the raw file bytes unchanged and emits a warning.
    """

    def process(self, content: bytes, path: str = "") -> bytes:
        """Compress *content* (raw image bytes) using context-aware strategies.

        Args:
            content: Raw bytes of the source image.
            path: Original file path — used only for format detection hints.

        Returns:
            Compressed bytes, or the original *content* if Pillow is absent.
        """
        if not _PILLOW_AVAILABLE:
            warnings.warn(
                "tokenpak[image] extras not installed — image compression skipped. "
                "Run: pip install tokenpak[image]",
                ImportWarning,
                stacklevel=2,
            )
            return content

        # Write to a temporary in-memory buffer so we can call the file-based API
        buf_in = io.BytesIO(content)
        with Image.open(buf_in) as img:
            # Determine mode from in-memory image
            w, h = img.size
            aspect = h / w if w > 0 else 1.0
            thumb = img.convert("RGB").resize((64, 64), Image.LANCZOS)
            _pdata = (
                thumb.get_flattened_data()
                if hasattr(thumb, "get_flattened_data")
                else thumb.getdata()
            )
            unique_colors = len(set(_pdata))

        if aspect > 1.2 or unique_colors < 100:
            mode = ImageType.DOCUMENT
        elif unique_colors < 1000:
            mode = ImageType.SCREENSHOT
        else:
            mode = ImageType.PHOTO

        buf_in.seek(0)
        with Image.open(buf_in) as img:
            return _compress_by_mode(img, mode)
