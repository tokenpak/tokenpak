"""TokenPak proxy utilities."""

from .credential_passthrough import CredentialPassthrough  # noqa: F401
from .cache import LRUCache, CacheEntry, CacheMetrics  # noqa: F401


class ProxyStats:
    """Stats/metrics container — resets on each new instance (restart)."""

    def __init__(self):
        self.requests_total = 0
        self.tokens_processed = 0
        self.errors_total = 0
        self.cache_hits = 0
        self.cache_misses = 0


class TokenPakProxy:
    """TokenPak proxy entry point (stub for test surface)."""

    def __init__(self, config=None):
        self.config = config or {}
        self.stats = ProxyStats()
        self._shutdown_event = None
