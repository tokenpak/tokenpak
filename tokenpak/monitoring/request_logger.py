"""tokenpak/monitoring/request_logger.py

Structured request logging for the TokenPak proxy.

Supports JSON (structured) and text output, with async file writing,
stdout, and syslog destinations.  Designed for zero-blocking overhead:
all I/O is dispatched to a background queue.

Configuration (via ~/.tokenpak/config.json under key "logging"):
    enabled              bool   default True
    level                str    "debug" | "info" | "warn"  (default "info")
    destination          str    "file" | "stdout" | "syslog"  (default "file")
    retention_days       int    default 30
    include_request_body bool   default False  (privacy)
    include_response_body bool  default False  (privacy)

Env var overrides (all optional):
    TOKENPAK_LOG_ENABLED        "0" / "1"
    TOKENPAK_LOG_LEVEL          "debug" / "info" / "warn"
    TOKENPAK_LOG_DESTINATION    "file" / "stdout" / "syslog"
    TOKENPAK_LOG_RETENTION_DAYS integer
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Module-level standard logger (for internal errors only)
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Log directory
# ---------------------------------------------------------------------------
_LOG_DIR = Path(os.path.expanduser("~/.tokenpak/logs"))

# ---------------------------------------------------------------------------
# Level constants
# ---------------------------------------------------------------------------
LEVEL_DEBUG = "debug"
LEVEL_INFO = "info"
LEVEL_WARN = "warn"

_LEVEL_ORDER = {LEVEL_DEBUG: 0, LEVEL_INFO: 1, LEVEL_WARN: 2}


# ---------------------------------------------------------------------------
# cache_origin enum (Constitution §5.3, Glossary `cache_origin`)
# ---------------------------------------------------------------------------
# Every request record MUST carry a cache_origin value. Overclaiming is worse
# than admitting uncertainty, so unclassified requests use "unknown".
CACHE_ORIGIN_CLIENT = "client"    # Provider-side cache hit (e.g. Anthropic cache_control)
CACHE_ORIGIN_PROXY = "proxy"      # TokenPak-local cache hit
CACHE_ORIGIN_NONE = "none"        # No cache involved; live provider call
CACHE_ORIGIN_UNKNOWN = "unknown"  # Caller did not classify; do NOT infer optimistically

_CACHE_ORIGIN_VALUES = frozenset({
    CACHE_ORIGIN_CLIENT,
    CACHE_ORIGIN_PROXY,
    CACHE_ORIGIN_NONE,
    CACHE_ORIGIN_UNKNOWN,
})


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_logging_config() -> Dict[str, Any]:
    """Return the merged logging config (env vars override config.json)."""
    defaults: Dict[str, Any] = {
        "enabled": True,
        "level": LEVEL_INFO,
        "destination": "file",
        "retention_days": 30,
        "include_request_body": False,
        "include_response_body": False,
    }
    # Try to load from ~/.tokenpak/config.json
    cfg_path = Path(os.path.expanduser("~/.tokenpak/config.json"))
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text())
            file_cfg = raw.get("logging", {})
            defaults.update(file_cfg)
        except Exception:
            pass
    # Env var overrides
    if os.environ.get("TOKENPAK_LOG_ENABLED") is not None:
        defaults["enabled"] = os.environ["TOKENPAK_LOG_ENABLED"] not in ("0", "false", "False")
    if os.environ.get("TOKENPAK_LOG_LEVEL"):
        defaults["level"] = os.environ["TOKENPAK_LOG_LEVEL"].lower()
    if os.environ.get("TOKENPAK_LOG_DESTINATION"):
        defaults["destination"] = os.environ["TOKENPAK_LOG_DESTINATION"].lower()
    if os.environ.get("TOKENPAK_LOG_RETENTION_DAYS"):
        try:
            defaults["retention_days"] = int(os.environ["TOKENPAK_LOG_RETENTION_DAYS"])
        except ValueError:
            pass
    return defaults


# ---------------------------------------------------------------------------
# Log record
# ---------------------------------------------------------------------------

class RequestLogRecord:
    """Immutable snapshot of a single proxied request/response cycle."""

    __slots__ = (
        "request_id",
        "timestamp",
        "level",
        "client_ip",
        "method",
        "endpoint",
        "request_body_size",
        "response_status",
        "response_body_size",
        "compression_ratio",
        "latency_ms",
        "model",
        "provider",
        "cache_origin",
        "extra",
    )

    def __init__(
        self,
        *,
        request_id: str,
        timestamp: str,
        level: str = LEVEL_INFO,
        client_ip: str = "",
        method: str = "POST",
        endpoint: str = "",
        request_body_size: int = 0,
        response_status: int = 0,
        response_body_size: int = 0,
        compression_ratio: Optional[float] = None,
        latency_ms: float = 0.0,
        model: str = "",
        provider: str = "",
        cache_origin: str = CACHE_ORIGIN_UNKNOWN,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.request_id = request_id
        self.timestamp = timestamp
        self.level = level
        self.client_ip = client_ip
        self.method = method
        self.endpoint = endpoint
        self.request_body_size = request_body_size
        self.response_status = response_status
        self.response_body_size = response_body_size
        self.compression_ratio = compression_ratio
        self.latency_ms = latency_ms
        self.model = model
        self.provider = provider
        if cache_origin not in _CACHE_ORIGIN_VALUES:
            raise ValueError(
                f"cache_origin={cache_origin!r} is not one of {sorted(_CACHE_ORIGIN_VALUES)}. "
                "Unclassified requests must pass cache_origin='unknown'."
            )
        self.cache_origin = cache_origin
        self.extra = extra or {}

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "level": self.level,
            "client_ip": self.client_ip,
            "method": self.method,
            "endpoint": self.endpoint,
            "request_body_size": self.request_body_size,
            "response_status": self.response_status,
            "response_body_size": self.response_body_size,
            "latency_ms": round(self.latency_ms, 2),
            "cache_origin": self.cache_origin,
        }
        if self.model:
            d["model"] = self.model
        if self.provider:
            d["provider"] = self.provider
        if self.compression_ratio is not None:
            d["compression_ratio"] = round(self.compression_ratio, 4)
        if self.extra:
            d.update(self.extra)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def to_text(self) -> str:
        ratio_str = (
            f" ratio={self.compression_ratio:.2%}" if self.compression_ratio is not None else ""
        )
        return (
            f"[{self.timestamp}] {self.level.upper()} req={self.request_id}"
            f" {self.method} {self.endpoint}"
            f" status={self.response_status}"
            f" in={self.request_body_size}B out={self.response_body_size}B"
            f"{ratio_str} lat={self.latency_ms:.1f}ms"
            + (f" model={self.model}" if self.model else "")
        )


# ---------------------------------------------------------------------------
# Writer implementations
# ---------------------------------------------------------------------------

class _FileWriter:
    """Thread-safe daily-rotating file writer."""

    def __init__(self, log_dir: Path, retention_days: int) -> None:
        self._log_dir = log_dir
        self._retention_days = retention_days
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""
        self._fh = None
        self._lock = threading.Lock()

    def _rotate(self, today: str) -> None:
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
        path = self._log_dir / f"proxy-{today}.log"
        self._fh = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
        self._current_date = today

    def write(self, line: str) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            if today != self._current_date:
                self._rotate(today)
                self._prune_old_logs()
            try:
                self._fh.write(line + "\n")  # type: ignore[union-attr]
            except Exception as exc:
                _log.warning("request_logger: file write failed: %s", exc)

    def _prune_old_logs(self) -> None:
        """Delete log files older than retention_days."""
        try:
            import time as _time
            cutoff = _time.time() - self._retention_days * 86400
            for p in self._log_dir.glob("proxy-*.log"):
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None


class _StdoutWriter:
    def write(self, line: str) -> None:
        print(line, flush=True)

    def close(self) -> None:
        pass


class _SyslogWriter:
    """Best-effort syslog writer (Linux/macOS only)."""

    def __init__(self) -> None:
        try:
            import syslog as _syslog
            _syslog.openlog("tokenpak-proxy", _syslog.LOG_PID)
            self._syslog = _syslog
            self._available = True
        except (ImportError, AttributeError):
            self._available = False
            _log.warning("request_logger: syslog not available on this platform")

    def write(self, line: str) -> None:
        if self._available:
            try:
                self._syslog.syslog(self._syslog.LOG_INFO, line[:1024])
            except Exception:
                pass

    def close(self) -> None:
        if self._available:
            try:
                self._syslog.closelog()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# RequestLogger
# ---------------------------------------------------------------------------

class RequestLogger:
    """
    Async structured request logger for the TokenPak proxy.

    Usage::

        logger = RequestLogger()

        # At request start:
        req_id = logger.new_request_id(headers)  # honours X-Request-ID

        # At request end:
        record = logger.build_record(
            request_id=req_id,
            client_ip="127.0.0.1",
            method="POST",
            endpoint="/v1/chat/completions",
            request_body_size=4096,
            response_status=200,
            response_body_size=512,
            compression_ratio=0.72,
            latency_ms=120.5,
            model="claude-3-5-sonnet",
            provider="anthropic",
        )
        logger.log(record)

    The logger writes to a background queue processed by a daemon thread.
    Call ``logger.stop()`` for clean shutdown (flushes queue).
    """

    _instance: Optional["RequestLogger"] = None
    _instance_lock = threading.Lock()

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._cfg = config or _load_logging_config()
        self._enabled: bool = bool(self._cfg.get("enabled", True))
        self._level: str = str(self._cfg.get("level", LEVEL_INFO))
        self._destination: str = str(self._cfg.get("destination", "file"))
        self._retention_days: int = int(self._cfg.get("retention_days", 30))

        self._writer = self._build_writer()
        self._queue: queue.Queue = queue.Queue(maxsize=10_000)
        self._thread = threading.Thread(target=self._worker, daemon=True, name="tokenpak-logger")
        self._thread.start()

    def _build_writer(self):
        if self._destination == "stdout":
            return _StdoutWriter()
        elif self._destination == "syslog":
            return _SyslogWriter()
        else:
            return _FileWriter(_LOG_DIR, self._retention_days)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "RequestLogger":
        """Return the process-wide singleton, creating it if needed."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (test helper)."""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
                cls._instance = None

    @staticmethod
    def new_request_id(headers: Optional[Dict[str, str]] = None) -> str:
        """
        Generate a new request UUID (v4).

        Honours ``X-Request-ID`` header if present, otherwise generates one.
        """
        if headers:
            xrid = headers.get("X-Request-ID") or headers.get("x-request-id")
            if xrid:
                return xrid
        return str(uuid.uuid4())

    def build_record(
        self,
        *,
        request_id: str,
        client_ip: str = "",
        method: str = "POST",
        endpoint: str = "",
        request_body_size: int = 0,
        response_status: int = 0,
        response_body_size: int = 0,
        compression_ratio: Optional[float] = None,
        latency_ms: float = 0.0,
        model: str = "",
        provider: str = "",
        cache_origin: str = CACHE_ORIGIN_UNKNOWN,
        extra: Optional[Dict[str, Any]] = None,
    ) -> RequestLogRecord:
        """Build a RequestLogRecord with current timestamp.

        ``cache_origin`` defaults to ``"unknown"`` — per Constitution §5.3, every
        record must carry a value, and unclassified calls must not be optimistically
        labeled as a cache hit. Callers that can classify should pass the explicit
        value (``CACHE_ORIGIN_CLIENT`` / ``CACHE_ORIGIN_PROXY`` / ``CACHE_ORIGIN_NONE``).
        """
        level = LEVEL_WARN if response_status >= 400 else LEVEL_INFO
        return RequestLogRecord(
            request_id=request_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=level,
            client_ip=client_ip,
            method=method,
            endpoint=endpoint,
            request_body_size=request_body_size,
            response_status=response_status,
            response_body_size=response_body_size,
            compression_ratio=compression_ratio,
            latency_ms=latency_ms,
            model=model,
            provider=provider,
            cache_origin=cache_origin,
            extra=extra,
        )

    def log(self, record: RequestLogRecord) -> None:
        """Enqueue a record for async writing (non-blocking)."""
        if not self._enabled:
            return
        if _LEVEL_ORDER.get(record.level, 1) < _LEVEL_ORDER.get(self._level, 1):
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            # Drop silently — logging must never block the proxy
            pass

    def log_dict(self, level: str = LEVEL_INFO, **kwargs) -> None:
        """Convenience: log an arbitrary dict (debug/info/warn).

        ``cache_origin`` may be passed as a kwarg; it defaults to ``"unknown"``.
        """
        record = RequestLogRecord(
            request_id=kwargs.pop("request_id", str(uuid.uuid4())),
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=level,
            **{k: kwargs.pop(k, v) for k, v in [
                ("client_ip", ""),
                ("method", ""),
                ("endpoint", ""),
                ("request_body_size", 0),
                ("response_status", 0),
                ("response_body_size", 0),
                ("compression_ratio", None),
                ("latency_ms", 0.0),
                ("model", ""),
                ("provider", ""),
                ("cache_origin", CACHE_ORIGIN_UNKNOWN),
            ]},
            extra=kwargs,
        )
        self.log(record)

    def stop(self, timeout: float = 5.0) -> None:
        """Flush remaining queue entries and stop the background thread."""
        try:
            self._queue.put(None, timeout=1.0)  # sentinel
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)
        self._writer.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        while True:
            try:
                record = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if record is None:  # sentinel → exit
                break
            try:
                line = record.to_json()
                self._writer.write(line)
            except Exception as exc:
                _log.debug("request_logger: worker error: %s", exc)


# ---------------------------------------------------------------------------
# Module-level convenience: log request-end event
# ---------------------------------------------------------------------------

def log_request(
    *,
    request_id: str,
    client_ip: str = "",
    method: str = "POST",
    endpoint: str = "",
    request_body_size: int = 0,
    response_status: int = 0,
    response_body_size: int = 0,
    compression_ratio: Optional[float] = None,
    latency_ms: float = 0.0,
    model: str = "",
    provider: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log a completed request using the process-wide RequestLogger singleton.

    This is the primary integration point for the proxy server.
    """
    rl = RequestLogger.get_instance()
    record = rl.build_record(
        request_id=request_id,
        client_ip=client_ip,
        method=method,
        endpoint=endpoint,
        request_body_size=request_body_size,
        response_status=response_status,
        response_body_size=response_body_size,
        compression_ratio=compression_ratio,
        latency_ms=latency_ms,
        model=model,
        provider=provider,
        extra=extra,
    )
    rl.log(record)


def new_request_id(headers: Optional[Dict[str, str]] = None) -> str:
    """Module-level convenience wrapper for RequestLogger.new_request_id."""
    return RequestLogger.new_request_id(headers)
