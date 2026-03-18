"""TokenPak Agent Ingest API — Phase 5A."""

from .api import create_ingest_app
from .api import router as ingest_router
from .disclosure import build_disclosure_payload, choose_disclosure_level, shortlist_sections

__all__ = [
    "create_ingest_app",
    "ingest_router",
    "choose_disclosure_level",
    "shortlist_sections",
    "build_disclosure_payload",
]

from .cross_doc import (  # noqa: F401
    CrossDocAnalyzer,
    DocCard,
    SchemaConverter,
    analyze_docs,
)

__all__ += ["CrossDocAnalyzer", "DocCard", "SchemaConverter", "analyze_docs"]

from .table_extractor import (  # noqa: F401
    NormalizedTable,
    TableExtractor,
)

__all__ += ["NormalizedTable", "TableExtractor"]

from .document_parser import (  # noqa: F401
    DocumentParser,
    DocumentSection,
    DocumentStructure,
)

__all__ += ["DocumentParser", "DocumentSection", "DocumentStructure"]
