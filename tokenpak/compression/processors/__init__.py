"""Content processors for different file types."""

from .code import CodeCompactionMode as CodeCompactionMode, CodeProcessor
from .data import DataProcessor
from .text import TextProcessor

# Tree-sitter processor (optional — graceful fallback if unavailable)
try:
    from .code_treesitter import TreeSitterProcessor
    from .code_treesitter import is_available as _ts_available

    _HAS_TREESITTER = _ts_available()
except ImportError:
    _HAS_TREESITTER = False

# Default code processor: tree-sitter if available, regex-based otherwise
_code_processor = TreeSitterProcessor() if _HAS_TREESITTER else CodeProcessor()
_code_processor_no_ts = CodeProcessor()

# Image processor (optional — graceful fallback if Pillow not installed)
try:
    from .image import ImageProcessor

    _image_processor: "ImageProcessor | None" = ImageProcessor()
except ImportError:  # pragma: no cover
    _image_processor = None

PROCESSORS = {
    "text": TextProcessor(),
    "code": _code_processor,
    "data": DataProcessor(),
    "image": _image_processor,
}


def get_processor(file_type: str, no_treesitter: bool = False):
    """
    Get the appropriate processor for a file type.

    Args:
        file_type:      One of 'text', 'code', 'data'.
        no_treesitter:  If True, force the regex-based CodeProcessor for code
                        files (respects --no-treesitter CLI flag).
    """
    if file_type == "code" and no_treesitter:
        return _code_processor_no_ts
    return PROCESSORS.get(file_type)

__all__ = ['code', 'code_treesitter', 'data', 'image', 'text']
