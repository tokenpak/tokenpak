"""Unit tests for processors/image.py — ImageProcessor, classify, compress."""

import io
import warnings
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Pillow availability guard
# ---------------------------------------------------------------------------

_PIL_AVAILABLE = True
try:
    from PIL import Image
except ImportError:
    _PIL_AVAILABLE = False

requires_pillow = pytest.mark.skipif(
    not _PIL_AVAILABLE, reason="Pillow not installed (pip install tokenpak[image])"
)


# ---------------------------------------------------------------------------
# Helpers — synthetic in-memory images (no file fixtures needed)
# ---------------------------------------------------------------------------


def _make_png_bytes(width: int, height: int, color: tuple) -> bytes:
    """Create a minimal solid-color PNG in memory using Pillow."""
    from PIL import Image as _Image

    img = _Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes(width: int, height: int) -> bytes:
    """Create a minimal JPEG in memory with varied colors."""
    import random

    from PIL import Image as _Image

    # Use a random pixel array to maximize unique colors (photo-like)
    random.seed(42)
    img = _Image.new("RGB", (width, height))
    pixels = [
        (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for _ in range(width * height)
    ]
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Enums and dataclasses
# ---------------------------------------------------------------------------


class TestImageType:
    def test_import(self):
        from tokenpak.compression.processors.image import ImageType

        assert ImageType is not None

    def test_document_value(self):
        from tokenpak.compression.processors.image import ImageType

        assert ImageType.DOCUMENT.value == "document"

    def test_screenshot_value(self):
        from tokenpak.compression.processors.image import ImageType

        assert ImageType.SCREENSHOT.value == "screenshot"

    def test_photo_value(self):
        from tokenpak.compression.processors.image import ImageType

        assert ImageType.PHOTO.value == "photo"


class TestCompressedImage:
    def test_import(self):
        from tokenpak.compression.processors.image import CompressedImage

        assert CompressedImage is not None

    @requires_pillow
    def test_fields(self):
        from tokenpak.compression.processors.image import CompressedImage, ImageType

        ci = CompressedImage(
            data=b"abc",
            original_size=100,
            compressed_size=50,
            compression_ratio=0.5,
            image_type=ImageType.PHOTO,
        )
        assert ci.data == b"abc"
        assert ci.original_size == 100
        assert ci.compressed_size == 50
        assert ci.compression_ratio == pytest.approx(0.5)
        assert ci.image_type == ImageType.PHOTO


# ---------------------------------------------------------------------------
# classify() — without Pillow
# ---------------------------------------------------------------------------


class TestClassifyNoPillow:
    def test_raises_import_error_when_pillow_absent(self):
        with mock.patch("tokenpak.compression.processors.image._PILLOW_AVAILABLE", False):
            from tokenpak.compression.processors.image import classify

            with pytest.raises(ImportError, match="pip install tokenpak"):
                classify("any_path.png")


# ---------------------------------------------------------------------------
# compress() — without Pillow
# ---------------------------------------------------------------------------


class TestCompressNoPillow:
    def test_raises_import_error_when_pillow_absent(self):
        with mock.patch("tokenpak.compression.processors.image._PILLOW_AVAILABLE", False):
            from tokenpak.compression.processors.image import compress

            with pytest.raises(ImportError, match="pip install tokenpak"):
                compress("any_path.png")


# ---------------------------------------------------------------------------
# ImageProcessor — without Pillow (passthrough stub)
# ---------------------------------------------------------------------------


class TestImageProcessorNoPillow:
    def test_passthrough_returns_original_bytes(self):
        with mock.patch("tokenpak.compression.processors.image._PILLOW_AVAILABLE", False):
            from tokenpak.compression.processors.image import ImageProcessor

            ip = ImageProcessor()
            raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = ip.process(raw, path="fake.png")
            assert result == raw

    def test_passthrough_emits_import_warning(self):
        with mock.patch("tokenpak.compression.processors.image._PILLOW_AVAILABLE", False):
            from tokenpak.compression.processors.image import ImageProcessor

            ip = ImageProcessor()
            raw = b"\x00" * 10
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ip.process(raw, path="fake.png")
            assert any(issubclass(w.category, ImportWarning) for w in caught)


# ---------------------------------------------------------------------------
# ImageProcessor — with Pillow
# ---------------------------------------------------------------------------


@requires_pillow
class TestImageProcessorWithPillow:
    def test_instantiation(self):
        from tokenpak.compression.processors.image import ImageProcessor

        ip = ImageProcessor()
        assert ip is not None

    def test_process_returns_bytes(self):
        from tokenpak.compression.processors.image import ImageProcessor

        ip = ImageProcessor()
        # Use a small solid-color PNG (limited unique colors → DOCUMENT type)
        raw = _make_png_bytes(20, 30, color=(200, 200, 200))
        result = ip.process(raw, path="doc.png")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_process_photo_like_image(self):
        """High-color-variety image should be processed as PHOTO."""
        from tokenpak.compression.processors.image import ImageProcessor

        ip = ImageProcessor()
        raw = _make_jpeg_bytes(200, 200)
        result = ip.process(raw, path="photo.jpg")
        assert isinstance(result, bytes)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# PROCESSORS dict registration
# ---------------------------------------------------------------------------


class TestProcessorsDict:
    def test_image_in_processors(self):
        from tokenpak.compression.processors import PROCESSORS

        assert "image" in PROCESSORS

    @requires_pillow
    def test_image_processor_not_none_with_pillow(self):
        from tokenpak.compression.processors import PROCESSORS

        assert PROCESSORS["image"] is not None


# ---------------------------------------------------------------------------
# _PILLOW_AVAILABLE flag reflects environment
# ---------------------------------------------------------------------------


class TestPillowFlag:
    def test_flag_is_bool(self):
        import tokenpak.compression.processors.image as img_mod

        assert isinstance(img_mod._PILLOW_AVAILABLE, bool)
