"""Content processors for different file types."""

from .text import TextProcessor
from .code import CodeProcessor
from .data import DataProcessor

PROCESSORS = {
    "text": TextProcessor(),
    "code": CodeProcessor(),
    "data": DataProcessor(),
}


def get_processor(file_type: str):
    """Get the appropriate processor for a file type."""
    return PROCESSORS.get(file_type)
