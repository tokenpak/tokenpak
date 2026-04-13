"""TokenPak Agent Ingest API — Phase 5A."""

try:
    from .api import create_ingest_app
    from .api import router as ingest_router
    _INGEST_API_AVAILABLE = True
except (TypeError, ImportError) as _ingest_init_err:
    # FastAPI/Starlette version incompatibility (e.g. Starlette 1.0 dropped on_startup kwarg)
    # Proxy runs in standalone HTTP mode — ingest API not required for core proxy operation
    import warnings as _warnings
    _warnings.warn(
        f"tokenpak.agent.ingest.api unavailable (FastAPI compat): {_ingest_init_err}",
        ImportWarning,
        stacklevel=2,
    )
    create_ingest_app = None
    ingest_router = None
    _INGEST_API_AVAILABLE = False

from .disclosure import build_disclosure_payload, choose_disclosure_level, shortlist_sections

__all__ = ['create_ingest_app', 'ingest_router', '_INGEST_API_AVAILABLE', 'choose_disclosure_level', 'shortlist_sections', 'build_disclosure_payload', 'api', 'claim_indexer', 'cross_doc', 'disclosure', 'document_parser', 'schema_converter', 'table_extractor']

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
