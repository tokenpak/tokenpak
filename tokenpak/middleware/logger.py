"""
Structured logging system for TokenPak proxy.

Supports JSON + human-readable output, async file I/O, configurable levels,
and daily log rotation.
"""

import json
import logging
import logging.handlers
import os
import sys
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Literal
import threading
from collections import deque
import time


LogLevel = Literal["debug", "info", "warn", "error"]
Destination = Literal["file", "stdout", "syslog"]


@dataclass
class LogRecord:
    """Structured log record."""
    timestamp: str  # ISO 8601
    request_id: str
    level: str  # debug, info, warn, error
    endpoint: str
    client_ip: Optional[str]
    method: str
    status_code: int
    request_size: int
    response_size: int
    latency_ms: float
    compression_ratio: Optional[float]
    message: str
    context: Dict[str, Any]

    def to_json(self) -> str:
        """Convert to JSON."""
        data = asdict(self)
        return json.dumps(data, default=str)

    def to_text(self) -> str:
        """Convert to human-readable text."""
        ratio_str = f" (ratio: {self.compression_ratio:.1%})" if self.compression_ratio else ""
        return (
            f"[{self.timestamp}] {self.level.upper():5} {self.request_id} "
            f"{self.method} {self.endpoint} -> {self.status_code} "
            f"[{self.request_size}→{self.response_size}B{ratio_str}] "
            f"{self.latency_ms:.1f}ms | {self.message}"
        )


@dataclass
class LoggingConfig:
    """Logging configuration."""
    enabled: bool = True
    level: LogLevel = "info"
    destination: Destination = "file"
    retention_days: int = 30
    include_request_body: bool = False
    include_response_body: bool = False
    log_dir: Optional[str] = None  # Default: ~/.tokenpak/logs
    async_buffer_size: int = 1000
    flush_interval_sec: int = 5

    def resolve_log_dir(self) -> str:
        """Resolve log directory path."""
        if self.log_dir:
            return self.log_dir
        home = os.path.expanduser("~")
        return os.path.join(home, ".tokenpak", "logs")


class AsyncLogger:
    """Asynchronous logger with buffering."""
    
    def __init__(self, config: LoggingConfig):
        self.config = config
        self.buffer = deque(maxlen=config.async_buffer_size)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        
        # Setup Python logging
        self.logger = logging.getLogger("tokenpak.proxy")
        self.logger.setLevel(logging.DEBUG)
        
        # Remove existing handlers
        self.logger.handlers.clear()
        
        # Add handler based on destination
        self._setup_handler()
        
        # Start flush thread
        self.flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self.flush_thread.start()
    
    def _setup_handler(self):
        """Setup logging handler based on destination."""
        formatter = logging.Formatter(
            '%(message)s'
        )
        
        if self.config.destination == "file":
            log_dir = self.config.resolve_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            
            # Daily rotation
            handler = logging.handlers.TimedRotatingFileHandler(
                os.path.join(log_dir, f"proxy-{datetime.now().strftime('%Y-%m-%d')}.log"),
                when="midnight",
                interval=1,
                backupCount=self.config.retention_days
            )
        elif self.config.destination == "stdout":
            handler = logging.StreamHandler(sys.stdout)
        elif self.config.destination == "syslog":
            handler = logging.handlers.SysLogHandler(address='/dev/log')
        else:
            raise ValueError(f"Unknown destination: {self.config.destination}")
        
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
    
    def log(self, record: LogRecord):
        """Enqueue log record."""
        if not self.config.enabled:
            return
        
        with self.lock:
            self.buffer.append(record)
        
        # Flush if buffer is full
        if len(self.buffer) >= self.config.async_buffer_size * 0.8:
            self._flush()
    
    def _flush_loop(self):
        """Periodically flush buffer."""
        while not self.stop_event.is_set():
            time.sleep(self.config.flush_interval_sec)
            self._flush()
    
    def _flush(self):
        """Write buffered records to storage."""
        with self.lock:
            while self.buffer:
                record = self.buffer.popleft()
                
                # Choose format based on destination
                if self.config.destination == "file":
                    msg = record.to_json()
                else:
                    msg = record.to_text()
                
                # Log at appropriate level
                level = getattr(logging, record.level.upper(), logging.INFO)
                self.logger.log(level, msg)
    
    def stop(self):
        """Stop async logging."""
        self.stop_event.set()
        self._flush()
        self.flush_thread.join(timeout=5)


class RequestLogger:
    """Structured request logger."""
    
    def __init__(self, config: LoggingConfig):
        self.config = config
        self.async_logger = AsyncLogger(config)
    
    def log_request(
        self,
        endpoint: str,
        method: str = "POST",
        client_ip: Optional[str] = None,
        request_size: int = 0,
        response_size: int = 0,
        status_code: int = 200,
        latency_ms: float = 0.0,
        compression_ratio: Optional[float] = None,
        message: str = "",
        context: Optional[Dict[str, Any]] = None,
        level: LogLevel = "info",
        request_id: Optional[str] = None,
    ):
        """Log a request."""
        if not self.config.enabled:
            return
        
        if request_id is None:
            request_id = str(uuid.uuid4())
        
        record = LogRecord(
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            request_id=request_id,
            level=level,
            endpoint=endpoint,
            client_ip=client_ip,
            method=method,
            status_code=status_code,
            request_size=request_size,
            response_size=response_size,
            latency_ms=latency_ms,
            compression_ratio=compression_ratio,
            message=message,
            context=context or {}
        )
        
        self.async_logger.log(record)
    
    def stop(self):
        """Stop logging."""
        self.async_logger.stop()


# Global logger instance
_logger: Optional[RequestLogger] = None


def init_logger(config: LoggingConfig) -> RequestLogger:
    """Initialize global logger."""
    global _logger
    _logger = RequestLogger(config)
    return _logger


def get_logger() -> Optional[RequestLogger]:
    """Get global logger."""
    return _logger
