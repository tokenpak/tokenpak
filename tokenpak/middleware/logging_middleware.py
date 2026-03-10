"""
Request logging middleware for TokenPak proxy.

Integrates with proxy request/response cycle to capture metrics.
"""

import time
import uuid
from functools import wraps
from typing import Callable, Any, Optional, Dict
import socket

from .logger import RequestLogger, LoggingConfig, LogLevel
from .audit_trail import CompileAudit, CacheAudit, MetricsAudit


class LoggingMiddleware:
    """Request logging middleware for proxy."""
    
    def __init__(self, logger: RequestLogger):
        self.logger = logger
        self._request_contexts: Dict[str, Dict[str, Any]] = {}
    
    def wrap_request(
        self,
        endpoint: str,
        method: str = "POST",
    ) -> Callable:
        """Decorator to wrap a request handler with logging."""
        def decorator(handler: Callable) -> Callable:
            @wraps(handler)
            def wrapper(*args, **kwargs) -> Any:
                request_id = str(uuid.uuid4())
                start_time = time.time()
                
                # Extract client IP from Flask/Starlette request object if present
                client_ip = self._get_client_ip(args, kwargs)
                
                # Store context for audit trails
                self._request_contexts[request_id] = {
                    "start_time": start_time,
                    "endpoint": endpoint,
                    "method": method,
                    "client_ip": client_ip,
                    "request_id": request_id,
                }
                
                try:
                    # Call actual handler
                    result = handler(*args, **kwargs)
                    
                    # Measure latency
                    latency_ms = (time.time() - start_time) * 1000
                    
                    # Extract response info
                    status_code = 200
                    response_size = 0
                    request_size = 0
                    compression_ratio = None
                    
                    if isinstance(result, tuple):
                        # (data, status_code) or (data, status_code, headers)
                        data = result[0]
                        status_code = result[1] if len(result) > 1 else 200
                        response_size = len(str(data)) if data else 0
                    else:
                        response_size = len(str(result)) if result else 0
                    
                    # Try to get request body size from kwargs
                    if "body" in kwargs:
                        request_size = len(str(kwargs["body"]))
                    elif args and isinstance(args[0], dict):
                        request_size = len(str(args[0]))
                    
                    # Calculate compression ratio if applicable
                    if request_size > 0 and response_size > 0:
                        compression_ratio = response_size / request_size
                    
                    # Log success
                    self.logger.log_request(
                        endpoint=endpoint,
                        method=method,
                        client_ip=client_ip,
                        request_size=request_size,
                        response_size=response_size,
                        status_code=status_code,
                        latency_ms=latency_ms,
                        compression_ratio=compression_ratio,
                        message="Request successful",
                        request_id=request_id,
                        level="info",
                    )
                    
                    return result
                
                except Exception as e:
                    # Measure latency
                    latency_ms = (time.time() - start_time) * 1000
                    
                    # Log error
                    self.logger.log_request(
                        endpoint=endpoint,
                        method=method,
                        client_ip=client_ip,
                        request_size=0,
                        response_size=0,
                        status_code=500,
                        latency_ms=latency_ms,
                        message=f"Error: {str(e)}",
                        request_id=request_id,
                        level="error",
                    )
                    
                    # Re-raise
                    raise
                
                finally:
                    # Clean up context
                    self._request_contexts.pop(request_id, None)
            
            return wrapper
        return decorator
    
    def log_compile_audit(self, audit: CompileAudit):
        """Log compilation audit trail."""
        # Convert audit to log entry
        message = (
            f"Compile: {audit.input_block_count} blocks → {audit.output_block_count} "
            f"({audit.compression_ratio:.1%} ratio, {audit.total_latency_ms:.1f}ms)"
        )
        
        self.logger.log_request(
            endpoint="/compile",
            method="POST",
            status_code=200,
            message=message,
            request_id=audit.request_id,
            context={
                "input_blocks": audit.input_block_count,
                "output_blocks": audit.output_block_count,
                "compression_ratio": audit.compression_ratio,
                "compression_methods": audit.compression_methods_used,
                "latency_breakdown": {
                    "parse_ms": audit.parse_latency_ms,
                    "compile_ms": audit.compile_latency_ms,
                    "render_ms": audit.render_latency_ms,
                    "total_ms": audit.total_latency_ms,
                },
                "blocks_removed": len([b for b in audit.blocks_audited if b.action == "removed"]),
                "blocks_compacted": len([b for b in audit.blocks_audited if b.action == "compacted"]),
                "tokens_removed": audit.tokens_removed,
            },
            level="info",
        )
    
    def log_cache_audit(self, audit: CacheAudit):
        """Log cache audit trail."""
        message = f"Cache {audit.operation}: {audit.block_id or 'all'} ({'hit' if audit.cache_hit else 'miss'})"
        
        self.logger.log_request(
            endpoint="/cache/",
            method="GET" if audit.operation == "get" else "POST",
            status_code=200,
            message=message,
            request_id=audit.request_id,
            context={
                "operation": audit.operation,
                "block_id": audit.block_id,
                "cache_hit": audit.cache_hit,
                "cached_value_size": audit.cached_value_size,
                "ttl_seconds": audit.ttl_seconds,
            },
            level="info",
        )
    
    def log_metrics_audit(self, audit: MetricsAudit):
        """Log metrics audit trail."""
        message = f"Metrics: {audit.aggregation_window} window, {audit.data_points_returned} data points"
        
        self.logger.log_request(
            endpoint="/metrics",
            method="GET",
            status_code=200,
            message=message,
            request_id=audit.request_id,
            context={
                "aggregation_window": audit.aggregation_window,
                "data_points_returned": audit.data_points_returned,
                "metrics_included": audit.metrics_included,
            },
            level="info",
        )
    
    def _get_client_ip(self, args: tuple, kwargs: dict) -> Optional[str]:
        """Extract client IP from request object."""
        # Try to find a request object in args/kwargs
        for arg in args:
            if hasattr(arg, "remote_addr"):
                return arg.remote_addr
            if hasattr(arg, "client") and hasattr(arg.client, "host"):
                return arg.client.host
        
        for value in kwargs.values():
            if hasattr(value, "remote_addr"):
                return value.remote_addr
            if hasattr(value, "client") and hasattr(value.client, "host"):
                return value.client.host
        
        return None
