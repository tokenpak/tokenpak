"""
Error Handling Example
======================
Demonstrates robust error handling patterns for TokenPak compression.

Problem: Production apps can't crash on compression failure. Compression should
         degrade gracefully with clear logging.
Solution: Wrapper classes with fallback, retry, and circuit-breaker patterns.

Setup: pip install tokenpak
"""

import sys
import os
import time
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("tokenpak.error_handling")


# ---------------------------------------------------------------------------
# Pattern 1: Safe compress with fallback
# ---------------------------------------------------------------------------

_engine = HeuristicEngine()

def safe_compress(text: str, fallback: str = None) -> str:
    """
    Compress with graceful fallback. Never raises — returns original on failure.
    
    Args:
        text: Input text to compress.
        fallback: Custom fallback string. Defaults to original text.
    
    Returns:
        Compressed text, or fallback/original if compression fails.
    """
    if not text or not text.strip():
        log.debug("safe_compress: empty input, returning as-is")
        return text or ""

    try:
        result = _engine.compact(text)
        return result
    except Exception as e:
        log.warning(f"safe_compress: compression failed ({type(e).__name__}: {e}), using fallback")
        return fallback if fallback is not None else text


def demo_safe_compress():
    print("=== Pattern 1: Safe Compress with Fallback ===\n")

    # Normal case
    result = safe_compress("The verbose text that contains many redundant words and filler phrases.")
    print(f"  Normal:  {len(result)} chars (compressed)")

    # Empty input
    result = safe_compress("")
    print(f"  Empty:   '{result}' (returned as-is)")

    # None-like input
    result = safe_compress(None)
    print(f"  None:    '{result}' (returned as empty string)")

    # Custom fallback
    result = safe_compress("", fallback="[no content]")
    print(f"  Fallback: '{result}'")
    print()


# ---------------------------------------------------------------------------
# Pattern 2: Retry with backoff
# ---------------------------------------------------------------------------

def compress_with_retry(text: str, max_attempts: int = 3, base_delay: float = 0.1) -> str:
    """
    Compress with exponential backoff retry.
    
    Useful when compression is backed by a network service (e.g., HTTP compression proxy).
    """
    last_error = None
    for attempt in range(max_attempts):
        try:
            return _engine.compact(text)
        except Exception as e:
            last_error = e
            delay = base_delay * (2 ** attempt)
            log.warning(f"compress_with_retry: attempt {attempt + 1}/{max_attempts} failed, retrying in {delay:.2f}s")
            time.sleep(delay)

    log.error(f"compress_with_retry: all {max_attempts} attempts failed, returning original")
    return text  # fallback to original


def demo_retry():
    print("=== Pattern 2: Retry with Backoff ===\n")
    
    text = "Important context that must be preserved through all retry attempts."
    result = compress_with_retry(text, max_attempts=2)
    print(f"  Result: {len(result)} chars")
    print()


# ---------------------------------------------------------------------------
# Pattern 3: Input validation
# ---------------------------------------------------------------------------

class CompressionError(Exception):
    """Base class for TokenPak compression errors."""
    pass

class InputTooLargeError(CompressionError):
    pass

class InputTooSmallError(CompressionError):
    pass


def validated_compress(
    text: str,
    min_chars: int = 50,
    max_chars: int = 1_000_000,
) -> str:
    """
    Compress with explicit input validation.
    Raises CompressionError subclasses for invalid inputs.
    """
    if not isinstance(text, str):
        raise TypeError(f"Expected str, got {type(text).__name__}")

    if len(text) < min_chars:
        raise InputTooSmallError(
            f"Input too short ({len(text)} chars). Compression below {min_chars} chars is counterproductive."
        )

    if len(text) > max_chars:
        raise InputTooLargeError(
            f"Input too large ({len(text):,} chars). Max: {max_chars:,}. Split into chunks first."
        )

    return _engine.compact(text)


def demo_validation():
    print("=== Pattern 3: Input Validation ===\n")

    cases = [
        ("short", "Hi"),
        ("valid", "The quarterly report shows improvement in key metrics across all divisions, with revenue up 23%."),
        ("wrong type", 12345),
    ]

    for name, input_val in cases:
        try:
            result = validated_compress(input_val)
            print(f"  [{name}] OK: {len(result)} chars compressed")
        except InputTooSmallError as e:
            print(f"  [{name}] InputTooSmallError: {e}")
        except InputTooLargeError as e:
            print(f"  [{name}] InputTooLargeError: {e}")
        except TypeError as e:
            print(f"  [{name}] TypeError: {e}")
    print()


# ---------------------------------------------------------------------------
# Pattern 4: Circuit breaker
# ---------------------------------------------------------------------------

class CompressionCircuitBreaker:
    """
    Circuit breaker for compression service.
    
    States:
        CLOSED   → normal operation
        OPEN     → failing, reject requests fast
        HALF_OPEN → testing if service recovered
    
    Use when compression is backed by an external service.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self._state = self.CLOSED
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._last_failure_time = None
        self._recovery_timeout = recovery_timeout
        self._engine = HeuristicEngine()

    @property
    def state(self):
        if self._state == self.OPEN:
            if time.time() - self._last_failure_time > self._recovery_timeout:
                log.info("CircuitBreaker: entering HALF_OPEN for recovery test")
                self._state = self.HALF_OPEN
        return self._state

    def compress(self, text: str) -> str:
        """Compress with circuit breaker protection."""
        state = self.state

        if state == self.OPEN:
            log.warning("CircuitBreaker: OPEN — returning original without compression")
            return text  # fast fail

        try:
            result = self._engine.compact(text)
            if state == self.HALF_OPEN:
                log.info("CircuitBreaker: recovery successful, returning to CLOSED")
                self._state = self.CLOSED
                self._failure_count = 0
            return result
        except Exception as e:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self._failure_threshold:
                log.error(f"CircuitBreaker: {self._failure_count} failures → OPEN")
                self._state = self.OPEN
            return text  # fallback


def demo_circuit_breaker():
    print("=== Pattern 4: Circuit Breaker ===\n")
    
    cb = CompressionCircuitBreaker(failure_threshold=3, recovery_timeout=5.0)
    text = "The system experienced significant performance degradation due to resource contention."

    for i in range(5):
        result = cb.compress(text)
        print(f"  Request {i+1}: state={cb._state}, result_len={len(result)}")
    print()


# ---------------------------------------------------------------------------
# Pattern 5: Timeout handling
# ---------------------------------------------------------------------------

import threading

def compress_with_timeout(text: str, timeout_seconds: float = 5.0) -> str:
    """
    Compress with a hard timeout — fallback to original if too slow.
    
    Critical for real-time applications where latency matters more than savings.
    """
    result_container = [None]
    error_container = [None]

    def run():
        try:
            result_container[0] = _engine.compact(text)
        except Exception as e:
            error_container[0] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        log.warning(f"compress_with_timeout: exceeded {timeout_seconds}s, returning original")
        return text  # timeout fallback

    if error_container[0]:
        log.warning(f"compress_with_timeout: error ({error_container[0]}), returning original")
        return text

    return result_container[0]


def demo_timeout():
    print("=== Pattern 5: Timeout Handling ===\n")
    
    text = "Context that must be delivered within latency budget regardless of compression outcome."
    result = compress_with_timeout(text, timeout_seconds=10.0)
    print(f"  Result ({len(result)} chars): compressed within timeout")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    demo_safe_compress()
    demo_retry()
    demo_validation()
    demo_circuit_breaker()
    demo_timeout()
    print("✅ All error handling patterns demonstrated")
