"""
TokenPak middleware — Request logging, audit trails, observability.
"""

from .logger import (
    RequestLogger,
    LoggingConfig,
    LogLevel,
    Destination,
    LogRecord,
    AsyncLogger,
    init_logger,
    get_logger,
)

from .audit_trail import (
    CompileAudit,
    CacheAudit,
    MetricsAudit,
    BlockAudit,
    CompressionMethod,
    BlockType,
    create_compile_audit,
    create_cache_audit,
    create_metrics_audit,
)

from .logging_middleware import LoggingMiddleware
from .semantic_cache_middleware import SemanticCacheMiddleware

__all__ = [
    # Logger
    "RequestLogger",
    "LoggingConfig",
    "LogLevel",
    "Destination",
    "LogRecord",
    "AsyncLogger",
    "init_logger",
    "get_logger",
    
    # Audit
    "CompileAudit",
    "CacheAudit",
    "MetricsAudit",
    "BlockAudit",
    "CompressionMethod",
    "BlockType",
    "create_compile_audit",
    "create_cache_audit",
    "create_metrics_audit",
    
    # Middleware
    "LoggingMiddleware",
    "SemanticCacheMiddleware",
]
