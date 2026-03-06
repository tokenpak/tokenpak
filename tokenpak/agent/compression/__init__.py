"""
TokenPak Compression Package

Provides the compression pipeline and supporting utilities:
- CompressionPipeline  — orchestrator (pipeline.py)
- segmentize           — message classification (segmentizer.py)
- SlotFiller           — intent parameterization (slot_filler.py)
- RecipeEngine         — recipe-based assembly (recipes.py)
- validate / apply_fallback — shadow-reader validation (canon.py)
- dedup_messages       — duplicate turn removal (dedup.py)
- DirectiveApplier     — directive application stub (directives.py)
"""

from .pipeline import CompressionPipeline
from .segmentizer import segmentize, Segment, SegmentType
from .slot_filler import SlotFiller, FilledSlots
from .recipes import RecipeEngine, Recipe, MissingBlockError
from .recipes import RecipeType, ContentSegment, CompressionRuleEngine, PHRASE_MAP
from .canon import validate, apply_fallback, ValidationResult
from .dedup import dedup_messages
from .directives import DirectiveApplier

__all__ = [
    "CompressionPipeline",
    "segmentize",
    "Segment",
    "SegmentType",
    "SlotFiller",
    "FilledSlots",
    "RecipeEngine",
    "Recipe",
    "MissingBlockError",
    "RecipeType",
    "ContentSegment",
    "CompressionRuleEngine",
    "PHRASE_MAP",
    "validate",
    "apply_fallback",
    "ValidationResult",
    "dedup_messages",
    "DirectiveApplier",
]
