"""Content processors for different file types."""

from .code import CodeCompactionMode, CodeProcessor
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

PROCESSORS = {
    "text": TextProcessor(),
    "code": _code_processor,
    "data": DataProcessor(),
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
