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
- SchemaExtractor      — document-type-aware schema substitution (schema_extractor.py)
- CompressionDictionary — project-specific phrase → token replacement (dictionary.py)
"""

from .canon import ValidationResult, apply_fallback, validate
from .dedup import dedup_messages
from .directives import DirectiveApplier
from .pipeline import CompressionPipeline
from .recipes import (
    PHRASE_MAP,
    CompressionRuleEngine,
    ContentSegment,
    MissingBlockError,
    Recipe,
    RecipeEngine,
    RecipeType,
)
from .schema_extractor import TEMPLATES, ExtractionResult, SchemaExtractor
from .segmentizer import Segment, SegmentType, segmentize
from .slot_filler import FilledSlots, SlotFiller

__all__ = ['CompressionPipeline', 'segmentize', 'Segment', 'SegmentType', 'SlotFiller', 'FilledSlots', 'RecipeEngine', 'Recipe', 'MissingBlockError', 'RecipeType', 'ContentSegment', 'CompressionRuleEngine', 'PHRASE_MAP', 'validate', 'apply_fallback', 'ValidationResult', 'dedup_messages', 'DirectiveApplier', 'SchemaExtractor', 'ExtractionResult', 'TEMPLATES', 'alias_compressor', 'canon', 'dedup', 'dictionary', 'directives', 'doc_compressor', 'fidelity_tiers', 'fingerprinter', 'instruction_table', 'pipeline', 'query_rewriter', 'recipes', 'salience', 'schema_extractor', 'segmentizer', 'slot_filler']
from . import salience
from .fidelity_tiers import (
    TIER_COST_FACTOR,
    FidelityTier,
    TieredBlock,
    TierGenerator,
    TierSelector,
    TierStore,
)
from .query_rewriter import QueryRewriter, RewriteResult, rewrite_query
from .salience import (
    CodeExtractor,
    ContentType,
    DocExtractor,
    LogExtractor,
    SalientResult,
    detect_content_type,
)
from .salience import (
    extract as salience_extract,
)

__all__ += [
    "salience",
    "ContentType",
    "detect_content_type",
    "LogExtractor",
    "CodeExtractor",
    "DocExtractor",
    "SalientResult",
    "salience_extract",
]
from .alias_compressor import AliasCompressor, AliasResult

__all__ += ["AliasCompressor", "AliasResult"]
from .dictionary import CompressionDictionary, DictionaryResult, SuggestedEntry

__all__ += ["CompressionDictionary", "DictionaryResult", "SuggestedEntry"]
from .fingerprinter import (
    Fingerprint,
    FingerPrinter,
    FingerprintGenerator,
    FingerprintSync,
    PrivacyLevel,
    SyncResult,
    apply_privacy,
)

__all__ += [
    "FingerPrinter",
    "FingerprintGenerator",
    "Fingerprint",
    "PrivacyLevel",
    "apply_privacy",
    "FingerprintSync",
    "SyncResult",
]
from .doc_compressor import DocCompressor, compress_document

__all__ += ["DocCompressor", "compress_document"]
