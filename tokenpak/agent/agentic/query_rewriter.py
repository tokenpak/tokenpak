"""query_rewriter.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.agent.agentic.query_rewriter import (
        RewriteStrategy,
        QueryAnalysis,
        RewriteResult,
        QueryAnalyzer,
        QueryRewriter,
    )

    __all__ = [
        "RewriteStrategy",
        "QueryAnalysis",
        "RewriteResult",
        "QueryAnalyzer",
        "QueryRewriter",
    ]
except ImportError:
    raise ImportError(
        "query_rewriter requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
