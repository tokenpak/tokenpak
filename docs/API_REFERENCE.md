# TokenPak API Reference

> Auto-generated from source code docstrings and type hints via `scripts/generate_api_reference.py`.

**Public classes:** 678  
**Public methods:** 2543

## API Index

- **TokenPakClient**: SDK client usage pattern (documented in examples; production-facing entrypoint is `ContextPack` + connectors/processors)
- **TokenPakProxy**: Proxy service capabilities (implemented across `proxy.py` and `tokenpak/proxy/*` adapters)
- **Adapters**: `tokenpak.adapters.*`, `tokenpak.proxy.adapters.*`, `tokenpak.telemetry.adapters.*`
- **Metrics**: `tokenpak.monitoring.metrics.ProxyMetricsCollector`, telemetry collectors/storage
- **Cache**: `tokenpak.cache.*`, `tokenpak.telemetry.cache.CacheStore`
- **Config**: `tokenpak.telemetry.config.*`, policy/config models across modules

## Type Hints Guide

- `Optional[T]` means parameter may be `None`.
- `Union[A, B]` or `A | B` means either type is accepted/returned.
- Container hints (`list[T]`, `dict[K, V]`) define item/key/value types.
- Return type `Any` indicates dynamically shaped data.

## Code Examples

### ContextPack
```python
from tokenpak.pack import ContextPack
pack = ContextPack()
result = pack.compile_blocks(raw_blocks, source='notes.md')
```

### RequestValidator
```python
from tokenpak.validation.request_validator import RequestValidator
validator = RequestValidator()
validation = validator.validate(payload)
```

### OpenAIAdapter
```python
from tokenpak.adapters.openai import OpenAIAdapter
adapter = OpenAIAdapter(model='gpt-4o-mini', api_key='...')
response = adapter.complete(messages)
```

### AnthropicAdapter
```python
from tokenpak.adapters.anthropic import AnthropicAdapter
adapter = AnthropicAdapter(model='claude-3-5-sonnet-latest', api_key='...')
response = adapter.complete(messages)
```

### ProxyMetricsCollector
```python
from tokenpak.monitoring.metrics import ProxyMetricsCollector
metrics = ProxyMetricsCollector()
metrics.record_request(provider='openai', status='ok', latency_ms=120)
```

## Class Reference

### `tokenpak._cli_core.Colors`

**Bases:** object

ANSI color codes.

#### `ok`

```python
def ok(text: str) -> str
```

- **Returns:** `str`

#### `warn`

```python
def warn(text: str) -> str
```

- **Returns:** `str`

#### `fail`

```python
def fail(text: str) -> str
```

- **Returns:** `str`

### `tokenpak._formatting.formatter.OutputFormatter`

**Bases:** object

#### `__init__`

```python
def __init__(self, section: str, mode: OutputMode = OutputMode.NORMAL, minimal: bool = False) -> Any
```

- **Returns:** `Any`

#### `header`

```python
def header(self) -> str
```

- **Returns:** `str`

#### `kv`

```python
def kv(self, rows: Iterable[tuple[str, str]]) -> str
```

- **Returns:** `str`

#### `signal`

```python
def signal(self, symbol: str, text: str, tone: str = 'info') -> str
```

- **Returns:** `str`

#### `error_block`

```python
def error_block(self, title: str, reason: str, action: str) -> str
```

- **Returns:** `str`

#### `minimal_line`

```python
def minimal_line(self, cells: Iterable[str]) -> str
```

- **Returns:** `str`

#### `raw`

```python
def raw(self, payload: dict[str, Any]) -> str
```

- **Returns:** `str`

### `tokenpak._formatting.picker.AltScreenSession`

**Bases:** object

Context manager that owns the alternate-screen buffer for one session.

Enters the alt-screen + hides the cursor ONCE on ``__enter__`` and restores
the normal buffer + cursor ONCE on ``__exit__`` — including on ``q``/esc/EOF/
``^C``/exception, because ``__exit__`` always runs. ``suspend()`` / ``resume()``
leave and re-enter the alt-screen for a command that must run on the normal
buffer (lifecycle ``run_and_exit`` / ``suspend_and_return``).

Every ``\033[?1049h`` is balanced by exactly one ``\033[?1049l`` on every
path (spec H2): an internal ``_alt_active`` flag ensures the leave sequence
is emitted once and only once whether the user quits, suspends-then-exits,
or an exception unwinds the stack.

#### `__init__`

```python
def __init__(self, *, enabled: Optional[bool] = None) -> None
```

- **Returns:** `None`

#### `suspend`

```python
def suspend(self) -> None
```

- **Returns:** `None`
- **Description:** Leave the alt-screen to run a command on the normal buffer.

#### `resume`

```python
def resume(self) -> None
```

- **Returns:** `None`
- **Description:** Re-enter the alt-screen after a suspended command.

### `tokenpak.agent.license.validator.LicenseTier`

**Bases:** Enum

License tiers ordered from least to most capable.

#### `ladder`

```python
def ladder(cls) -> tuple['LicenseTier', ...]
```

- **Returns:** `tuple['LicenseTier', ...]`
- **Description:** Return license tiers in ascending capability order.

### `tokenpak.alerts.__init__.AlertRuleState`

**Bases:** object

Tracks state for a single alert rule (cooldown enforcement).

#### `should_fire`

```python
def should_fire(self, cooldown_minutes: int) -> bool
```

- **Returns:** `bool`
- **Description:** Check if enough time has passed since last fire.

#### `update_fired`

```python
def update_fired(self, value: float = 0.0) -> None
```

- **Returns:** `None`
- **Description:** Record that this alert fired.

#### `to_dict`

```python
def to_dict(self) -> AlertPayload
```

- **Returns:** `AlertPayload`
- **Description:** Serialize alert to a plain dictionary.

### `tokenpak.alerts.channels.email.EmailChannel`

**Bases:** object

Delivers alerts via SMTP email.

#### `__init__`

```python
def __init__(self, smtp_host: str, smtp_port: int, to_addr: str, *, smtp_user: str = '', smtp_pass: str = '', from_addr: str = '', use_tls: bool = True) -> None
```

- **Returns:** `None`

#### `send`

```python
def send(self, event: str, severity: str, message: str, **kwargs: Any) -> bool
```

- **Returns:** `bool`

### `tokenpak.alerts.channels.slack.SlackChannel`

**Bases:** object

Delivers alerts to a Slack incoming-webhook URL.

#### `__init__`

```python
def __init__(self, webhook: str) -> None
```

- **Returns:** `None`

#### `send`

```python
def send(self, event: str, severity: str, message: str, **kwargs: Any) -> bool
```

- **Returns:** `bool`

### `tokenpak.alerts.channels.telegram.TelegramChannel`

**Bases:** object

Delivers alerts to a Telegram chat via Bot API.

#### `__init__`

```python
def __init__(self, token: str, chat_id: str) -> None
```

- **Returns:** `None`

#### `send`

```python
def send(self, event: str, severity: str, message: str, **kwargs: Any) -> bool
```

- **Returns:** `bool`

### `tokenpak.alerts.channels.webhook.WebhookChannel`

**Bases:** object

Delivers alerts as generic JSON POST requests.

#### `__init__`

```python
def __init__(self, url: str) -> None
```

- **Returns:** `None`

#### `send`

```python
def send(self, event: str, severity: str, message: str, **kwargs: Any) -> bool
```

- **Returns:** `bool`

### `tokenpak.cache.cache_manager.CacheManager`

**Bases:** object

Unified interface over :class:`VolatileCache` and :class:`StableCache`.

Parameters
----------
volatile_cache:
    Existing :class:`VolatileCache` instance to use. If not provided, a
    new instance with default settings is created.
stable_cache:
    Existing :class:`StableCache` instance to use. If not provided, a
    new instance with default settings is created.
volatile_threshold:
    TTL threshold (seconds). ``set()`` calls with ``ttl < threshold``
    route to the volatile layer; ``>= threshold`` route to stable.
    Defaults to 300 s.

Examples
--------
>>> cm = CacheManager()
>>> cm.set("k", "v", ttl=60)
>>> cm.get("k")
'v'
>>> cm.delete("k")
>>> cm.get("k") is None
True

#### `__init__`

```python
def __init__(self, volatile_cache: Optional[VolatileCache] = None, stable_cache: Optional[StableCache] = None, volatile_threshold: float = _VOLATILE_THRESHOLD) -> None
```

- **Returns:** `None`

#### `get`

```python
def get(self, key: str, layer: Layer = 'auto', default: Any = None) -> Any
```

- **Returns:** `Any`
- **Description:** Retrieve *key* from the cache.

#### `set`

```python
def set(self, key: str, value: Any, ttl: Optional[float] = None, layer: Layer = 'auto') -> None
```

- **Returns:** `None`
- **Description:** Store *value* under *key*.

#### `delete`

```python
def delete(self, key: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove *key* from both layers.

#### `clear`

```python
def clear(self, layer: Layer = 'all') -> None
```

- **Returns:** `None`
- **Description:** Clear one or both layers.

#### `volatile`

```python
def volatile(self) -> VolatileCache
```

- **Returns:** `VolatileCache`
- **Description:** Direct access to the underlying :class:`VolatileCache`.

#### `stable`

```python
def stable(self) -> StableCache
```

- **Returns:** `StableCache`
- **Description:** Direct access to the underlying :class:`StableCache`.

### `tokenpak.cache.cache_store.CacheStore`

**Bases:** object

Persistent key-value cache backed by a JSON file.

Parameters
----------
path:
    Filesystem path for the backing JSON file.  Parent directories are
    created automatically on first save.  Defaults to
    ``~/.tokenpak/cache_store.json``.

Examples
--------
>>> import tempfile, os
>>> with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
...     path = f.name
>>> store = CacheStore(path=path)
>>> store.set("hello", "world")
>>> store.get("hello")
'world'
>>> store.has("hello")
True
>>> store.delete("hello")
>>> store.get("hello") is None
True
>>> os.unlink(path)

#### `__init__`

```python
def __init__(self, path: Optional[os.PathLike[str] | str] = None) -> None
```

- **Returns:** `None`

#### `get`

```python
def get(self, key: str, default: Any = None) -> Any
```

- **Returns:** `Any`
- **Description:** Return the value stored under *key*, or *default* if absent/expired.

#### `set`

```python
def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None
```

- **Returns:** `None`
- **Description:** Store *value* under *key*.

#### `delete`

```python
def delete(self, key: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove *key* from the store.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Remove all entries and persist the empty store.

#### `has`

```python
def has(self, key: str) -> bool
```

- **Returns:** `bool`
- **Description:** Return ``True`` if *key* exists and has not expired.

#### `keys`

```python
def keys(self) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Return all non-expired keys.

### `tokenpak.cache.prefix_registry.StablePrefixRegistry`

**Bases:** object

Thread-safe in-memory registry mapping content-addressed IDs to metadata.

>>> reg = StablePrefixRegistry()
>>> bid, is_new = reg.get_or_create({"system": "You are helpful."})
>>> is_new
True
>>> bid2, is_new2 = reg.get_or_create({"system": "You are helpful."})
>>> bid == bid2
True
>>> is_new2
False
>>> reg.metadata(bid)["hit_count"]
2

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `get_or_create`

```python
def get_or_create(self, payload: Any) -> Tuple[str, bool]
```

- **Returns:** `Tuple[str, bool]`
- **Description:** Return (block_id, is_new) for *payload*.

#### `metadata`

```python
def metadata(self, block_id: str) -> Optional[Dict[str, Any]]
```

- **Returns:** `Optional[Dict[str, Any]]`
- **Description:** Return a copy of the metadata dict for *block_id*, or None if unknown.

#### `all_metadata`

```python
def all_metadata(self) -> Dict[str, Dict[str, Any]]
```

- **Returns:** `Dict[str, Dict[str, Any]]`
- **Description:** Return a snapshot of all registry entries (copies).

#### `size`

```python
def size(self) -> int
```

- **Returns:** `int`
- **Description:** Number of distinct stable blocks currently tracked.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Wipe all entries (useful for tests).

#### `summary`

```python
def summary(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return a high-level summary suitable for diagnostics/logging.

### `tokenpak.cache.registry.CacheRegistry`

**Bases:** object

Class-level registry; no instantiation needed.

#### `get_default`

```python
def get_default(cls) -> VolatileCache
```

- **Returns:** `VolatileCache`
- **Description:** Return the default VolatileCache, creating it on first call.

#### `get_stable`

```python
def get_stable(cls) -> StableCache
```

- **Returns:** `StableCache`
- **Description:** Return the default StableCache, creating it on first call.

#### `get_injection`

```python
def get_injection(cls) -> VolatileCache
```

- **Returns:** `VolatileCache`
- **Description:** Return the injection cache (alias for the proxy vault-injection cache).

#### `register`

```python
def register(cls, name: str, cache: CacheInstance, *, overwrite: bool = False) -> None
```

- **Returns:** `None`
- **Description:** Register *cache* under *name*.

#### `get`

```python
def get(cls, name: str) -> Optional[CacheInstance]
```

- **Returns:** `Optional[CacheInstance]`
- **Description:** Return the cache registered under *name*, or None.

#### `names`

```python
def names(cls) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return all registered cache names.

#### `summary`

```python
def summary(cls) -> dict[str, dict[str, int | str]]
```

- **Returns:** `dict[str, dict[str, int | str]]`
- **Description:** Return a size snapshot for all registered caches.

### `tokenpak.cache.semantic_cache.SemanticCache`

**Bases:** object

Semantic query cache with normalised-text and Jaccard matching.

Thread-safe.  Uses an OrderedDict (LRU eviction by insertion order).

Wire-format-aware — lookup requires ``expected_format`` and only
returns entries whose ``wire_format`` matches.  Store requires raw bytes +
content_type + wire_format.

>>> import json
>>> cfg = SemanticCacheConfig(ttl_seconds=60, max_entries=10)
>>> sc = SemanticCache(cfg)
>>> sc.store("What is the capital of France?", b'{"answer":"Paris"}', "application/json", "json")
SemanticCacheEntry(...)
>>> result = sc.lookup("What is the capital of France?", expected_format="json")
>>> result.hit
True
>>> result.match_strategy
'exact'

#### `__init__`

```python
def __init__(self, config: Optional[SemanticCacheConfig] = None) -> None
```

- **Returns:** `None`

#### `lookup`

```python
def lookup(self, query: str, *, expected_format: str = 'json') -> SemanticCacheLookup
```

- **Returns:** `SemanticCacheLookup`
- **Description:** Look up *query* in the cache, returning only entries matching *expected_format*.

#### `store`

```python
def store(self, query: str, response_bytes: bytes, content_type: str = 'application/json', wire_format: Literal['json', 'sse'] = 'json') -> SemanticCacheEntry
```

- **Returns:** `SemanticCacheEntry`
- **Description:** Store *response_bytes* for *query*.

#### `stats`

```python
def stats(self) -> Dict[str, int | float]
```

- **Returns:** `Dict[str, int | float]`
- **Description:** Return hit/miss statistics.

#### `size`

```python
def size(self) -> int
```

- **Returns:** `int`
- **Description:** Return the number of live (non-expired) entries.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`

#### `invalidate`

```python
def invalidate(self, query: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove all entries for *query* (any wire format). Returns True if any was present.

### `tokenpak.cache.semantic_cache.SemanticCacheEntry`

**Bases:** object

A single cached query/response pair.

Response is stored as raw bytes with an explicit wire_format and
content_type so the proxy can serve the entry back to the client with the
correct HTTP Content-Type header without any re-serialization.

#### `expires_at`

```python
def expires_at(self) -> float
```

- **Returns:** `float`

#### `is_expired`

```python
def is_expired(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.cache.stable_cache.StableCache`

**Bases:** object

LRU cache with a long (default 24 h) TTL.

>>> sc = StableCache(max_size=10)
>>> sc.set("k", "v")
>>> sc.get("k")
'v'
>>> sc.size()
1
>>> sc.is_cached("k")
True

#### `__init__`

```python
def __init__(self, max_size: int = _DEFAULT_MAX_SIZE, ttl: float = _DEFAULT_TTL, name: str = 'stable') -> None
```

- **Returns:** `None`

#### `is_cached`

```python
def is_cached(self, key: Any) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if *key* is present and not expired.

#### `retrieve`

```python
def retrieve(self, key: Any) -> Optional[Any]
```

- **Returns:** `Optional[Any]`
- **Description:** Return cached value for *key*, or None if missing / expired.

#### `get`

```python
def get(self, key: Any, default: Any = None) -> Any
```

- **Returns:** `Any`

#### `set`

```python
def set(self, key: Any, value: Any, ttl: Optional[float] = None) -> None
```

- **Returns:** `None`
- **Description:** Store *value* under *key*.  Evicts LRU entry if at capacity.

#### `invalidate`

```python
def invalidate(self, key: Any) -> bool
```

- **Returns:** `bool`
- **Description:** Remove *key*. Returns True if it existed.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Wipe all entries.

#### `size`

```python
def size(self) -> int
```

- **Returns:** `int`
- **Description:** Return the number of live (non-expired) entries.

### `tokenpak.cache.telemetry.CacheMetrics`

**Bases:** object

Snapshot of cache behaviour for a single proxy request.

Parameters
----------
request_id:
    Unique identifier for the request (any string; auto-generated if
    ``""`` is passed, but callers should supply a meaningful id).
stable_prefix_tokens:
    Estimated token count of the *stable* portion of the prompt that
    is expected to be cache-resident after the first request.
stable_cached:
    True when the LLM reported cache-read tokens > 0.
cache_miss_reason:
    Human-readable diagnosis string when the cache missed.
    ``None`` means cache hit (or unknown miss, not diagnosed).
volatile_tail_tokens:
    Tokens in the *volatile* tail (user message + tool call etc.).
total_input_tokens:
    Total input token count as reported by the LLM API response.
cache_read_tokens:
    Tokens served from the prompt cache (``cache_read_input_tokens``
    in Anthropic's usage object).
cache_creation_tokens:
    Tokens written into the prompt cache for this request
    (``cache_creation_input_tokens``).
output_tokens:
    Output / completion tokens for this request.
timestamp:
    Unix epoch seconds when the request was recorded.

#### `cache_hit`

```python
def cache_hit(self) -> bool
```

- **Returns:** `bool`
- **Description:** True when the prompt cache served at least one token.

#### `cache_hit_ratio`

```python
def cache_hit_ratio(self) -> float
```

- **Returns:** `float`
- **Description:** Fraction of input tokens served from cache (0.0–1.0).

#### `effective_tokens`

```python
def effective_tokens(self) -> int
```

- **Returns:** `int`
- **Description:** Total tokens minus cache_read tokens (new tokens processed).

#### `cache_ratio`

```python
def cache_ratio(self) -> float
```

- **Returns:** `float`
- **Description:** Alias for cache_hit_ratio (cache_read / total input).

#### `cost_saved`

```python
def cost_saved(self) -> float
```

- **Returns:** `float`
- **Description:** Estimated relative cost saving from cache reads.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.cache.telemetry.CacheTelemetryCollector`

**Bases:** object

Thread-safe session-level cache telemetry aggregator.

All public methods are safe to call from multiple threads.

Parameters
----------
max_recent:
    Maximum number of per-request ``CacheMetrics`` objects to retain
    in memory.  Older entries are dropped (FIFO) to bound memory use.

#### `__init__`

```python
def __init__(self, max_recent: int = _MAX_RECENT) -> None
```

- **Returns:** `None`

#### `record`

```python
def record(self, metrics: CacheMetrics) -> None
```

- **Returns:** `None`
- **Description:** Record a single request's cache metrics.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Clear all recorded metrics and reset state.

#### `by_ttl_attribution`

```python
def by_ttl_attribution(self) -> Dict[str, int]
```

- **Returns:** `Dict[str, int]`
- **Description:** Return a copy of the TTL-attribution histogram (counts per category).

#### `hit_rate`

```python
def hit_rate(self) -> float
```

- **Returns:** `float`
- **Description:** Fraction of requests that were cache hits (0.0–1.0).

#### `total`

```python
def total(self) -> int
```

- **Returns:** `int`
- **Description:** Total number of requests recorded.

#### `hits`

```python
def hits(self) -> int
```

- **Returns:** `int`
- **Description:** Total number of cache hits recorded.

#### `misses`

```python
def misses(self) -> int
```

- **Returns:** `int`
- **Description:** Total number of cache misses recorded.

#### `avg_cache_ratio`

```python
def avg_cache_ratio(self) -> float
```

- **Returns:** `float`
- **Description:** Average per-request cache-read / total-input ratio (0.0–1.0).

#### `by_miss_reason`

```python
def by_miss_reason(self) -> Dict[str, int]
```

- **Returns:** `Dict[str, int]`
- **Description:** Return a copy of the miss-reason histogram.

#### `recent_requests`

```python
def recent_requests(self, n: int = 10) -> List[dict[str, object]]
```

- **Returns:** `List[dict[str, object]]`
- **Description:** Return the last *n* requests as dicts (newest last).

#### `summary`

```python
def summary(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return all KPIs as a JSON-serialisable dict.

### `tokenpak.cache.volatile_cache.VolatileCache`

**Bases:** object

Short-lived TTL cache.

>>> vc = VolatileCache(ttl=60)
>>> vc.set("session-abc", {"text": "hello", "tokens": 42})
>>> vc.is_cached("session-abc")
True
>>> vc.retrieve("session-abc")
{'text': 'hello', 'tokens': 42}
>>> vc.size()
1

#### `__init__`

```python
def __init__(self, ttl: float = _DEFAULT_TTL, max_size: int = _DEFAULT_MAX_SIZE, name: str = 'volatile') -> None
```

- **Returns:** `None`

#### `is_cached`

```python
def is_cached(self, key: Any) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if *key* exists and has not expired.

#### `retrieve`

```python
def retrieve(self, key: Any) -> Optional[Any]
```

- **Returns:** `Optional[Any]`
- **Description:** Return the cached value or None if missing / expired.

#### `get`

```python
def get(self, key: Any, default: Any = None) -> Any
```

- **Returns:** `Any`

#### `set`

```python
def set(self, key: Any, value: Any, ttl: Optional[float] = None) -> None
```

- **Returns:** `None`
- **Description:** Store *value* under *key* with an optional per-entry TTL override.

#### `invalidate`

```python
def invalidate(self, key: Any) -> bool
```

- **Returns:** `bool`
- **Description:** Remove *key*. Returns True if it was present.

#### `sweep`

```python
def sweep(self) -> int
```

- **Returns:** `int`
- **Description:** Remove all expired entries. Returns count removed.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`

#### `size`

```python
def size(self) -> int
```

- **Returns:** `int`
- **Description:** Return the number of live (non-expired) entries.

### `tokenpak.cli.cli_diagnose.DiagResult`

**Bases:** object

Single diagnostic check result.

#### `__init__`

```python
def __init__(self, check: str, severity: str, message: str, detail: Optional[str] = None, data: Optional[Dict[str, Any]] = None) -> None
```

- **Returns:** `None`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.cli.cli_doctor.Colors`

**Bases:** object

ANSI color codes.

#### `ok`

```python
def ok(text) -> Any
```

- **Returns:** `Any`

#### `warn`

```python
def warn(text) -> Any
```

- **Returns:** `Any`

#### `fail`

```python
def fail(text) -> Any
```

- **Returns:** `Any`

### `tokenpak.cli.cli_validate_config.ConfigError`

**Bases:** object

Represents a single config error or warning.

#### `__init__`

```python
def __init__(self, line: int, field: str, message: str, suggestion: str = '', is_warning: bool = False) -> None
```

- **Returns:** `None`

#### `format`

```python
def format(self, symbol: str = 'ERROR') -> str
```

- **Returns:** `str`
- **Description:** Format error for display.

### `tokenpak.cli.cli_validate_config.ConfigValidator`

**Bases:** object

Validates TokenPak configuration files (YAML or JSON).

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `validate`

```python
def validate(self, config_path: str) -> tuple[int, list[ConfigError], list[ConfigError]]
```

- **Returns:** `tuple[int, list[ConfigError], list[ConfigError]]`
- **Description:** Validate a config file and return exit code + errors/warnings.

#### `validate_dict`

```python
def validate_dict(self, config: dict[str, Any]) -> tuple[int, list[ConfigError], list[ConfigError]]
```

- **Returns:** `tuple[int, list[ConfigError], list[ConfigError]]`
- **Description:** Validate a config dict directly (for testing).

### `tokenpak.cli.commands.compress_cmd.CompressReport`

**Bases:** object

#### `chars_saved`

```python
def chars_saved(self) -> int
```

- **Returns:** `int`

#### `tokens_saved`

```python
def tokens_saved(self) -> int
```

- **Returns:** `int`

#### `pct_saved`

```python
def pct_saved(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.cli.commands.config_env.Check`

**Bases:** object

#### `as_dict`

```python
def as_dict(self) -> dict[str, str]
```

- **Returns:** `dict[str, str]`

### `tokenpak.cli.commands.diff.ContextDiff`

**Bases:** object

A complete context diff.

#### `__init__`

```python
def __init__(self, trace_id: str, timestamp: Optional[str] = None, removed: Optional[list[DiffBlock]] = None, compressed: Optional[list[DiffBlock]] = None, retained: Optional[list[DiffBlock]] = None) -> Any
```

- **Returns:** `Any`

#### `total_blocks`

```python
def total_blocks(self) -> int
```

- **Returns:** `int`

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.cli.commands.diff.DiffBlock`

**Bases:** object

A single block in the diff.

#### `__init__`

```python
def __init__(self, block_id: str, name: str, status: str, tokens_before: int = 0, tokens_after: int = 0, pinned: bool = False, compression_pct: Optional[float] = None) -> Any
```

- **Returns:** `Any`

#### `symbol`

```python
def symbol(self) -> str
```

- **Returns:** `str`
- **Description:** Return the symbol for this block.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.cli.commands.doctor.Colors`

**Bases:** object

ANSI color codes + emoji markers, suppressed when color is unsupported.

#### `ok`

```python
def ok(text: str) -> str
```

- **Returns:** `str`

#### `warn`

```python
def warn(text: str) -> str
```

- **Returns:** `str`

#### `fail`

```python
def fail(text: str) -> str
```

- **Returns:** `str`

### `tokenpak.cli.commands.menu_status.StatusCache`

**Bases:** object

Lazy, TTL'd, backoff-protected proxy-status cache (single-writer).

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `snapshot`

```python
def snapshot(self, *, probe: bool = True) -> ProxyStatus
```

- **Returns:** `ProxyStatus`
- **Description:** Return the current honest status. Never blocks beyond ``_TIMEOUT``.

### `tokenpak.cli.commands.optimize_prompt.OptimizationReport`

**Bases:** object

#### `est_chars_saved`

```python
def est_chars_saved(self) -> int
```

- **Returns:** `int`

#### `est_tokens_saved`

```python
def est_tokens_saved(self) -> int
```

- **Returns:** `int`

#### `est_pct_saved`

```python
def est_pct_saved(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.cli.commands.uninstall.Receipt`

**Bases:** object

#### `record`

```python
def record(self, op: Op, outcome: str, detail: str) -> None
```

- **Returns:** `None`

#### `to_json`

```python
def to_json(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.cli.goals.Goal`

**Bases:** object

Single savings/metric goal definition.

#### `to_dict`

```python
def to_dict(self) -> GoalRecord
```

- **Returns:** `GoalRecord`

#### `from_dict`

```python
def from_dict(cls, data: GoalRecord) -> Goal
```

- **Returns:** `Goal`

#### `days_remaining`

```python
def days_remaining(self) -> int
```

- **Returns:** `int`
- **Description:** Calculate days remaining until goal end date.

#### `days_elapsed`

```python
def days_elapsed(self) -> int
```

- **Returns:** `int`
- **Description:** Calculate days elapsed since goal start date.

#### `total_days`

```python
def total_days(self) -> int
```

- **Returns:** `int`
- **Description:** Calculate total days for this goal.

#### `expected_progress_percent`

```python
def expected_progress_percent(self) -> float
```

- **Returns:** `float`
- **Description:** Calculate expected progress based on elapsed time.

### `tokenpak.cli.goals.GoalManager`

**Bases:** object

Manages goal creation, loading, tracking, and persistence.

#### `__init__`

```python
def __init__(self, goals_path: Optional[str] = None, state_path: Optional[str] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize goal manager.

#### `add_goal`

```python
def add_goal(self, name: str, goal_type: str, target_value: float, start_date: Optional[str] = None, end_date: Optional[str] = None, description: str = '', metric_name: str = '', rolling_window: bool = False) -> Goal
```

- **Returns:** `Goal`
- **Description:** Create a new goal.

#### `edit_goal`

```python
def edit_goal(self, goal_id: str, **kwargs: object) -> Optional[Goal]
```

- **Returns:** `Optional[Goal]`
- **Description:** Edit an existing goal.

#### `delete_goal`

```python
def delete_goal(self, goal_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Delete a goal.

#### `update_progress`

```python
def update_progress(self, goal_id: str, current_value: float, source: str = 'manual') -> Optional[GoalProgress]
```

- **Returns:** `Optional[GoalProgress]`
- **Description:** Update progress for a goal.

#### `get_goal`

```python
def get_goal(self, goal_id: str) -> Optional[Goal]
```

- **Returns:** `Optional[Goal]`
- **Description:** Retrieve a goal by ID.

#### `get_progress`

```python
def get_progress(self, goal_id: str) -> Optional[GoalProgress]
```

- **Returns:** `Optional[GoalProgress]`
- **Description:** Retrieve progress for a goal.

#### `list_goals`

```python
def list_goals(self, status: Optional[str] = None, goal_type: Optional[str] = None) -> list[Goal]
```

- **Returns:** `list[Goal]`
- **Description:** List all goals, optionally filtered.

#### `check_milestones`

```python
def check_milestones(self, goal_id: str) -> list[dict[str, str | int]]
```

- **Returns:** `list[dict[str, str | int]]`
- **Description:** Check and fire milestone alerts for a goal.

#### `check_pace_alerts`

```python
def check_pace_alerts(self, goal_id: str) -> Optional[dict[str, str | int]]
```

- **Returns:** `Optional[dict[str, str | int]]`
- **Description:** Check and fire pace alert for a goal.

#### `get_summary_stats`

```python
def get_summary_stats(self) -> dict[str, int | float]
```

- **Returns:** `dict[str, int | float]`
- **Description:** Get summary statistics for all goals.

### `tokenpak.cli.goals.GoalProgress`

**Bases:** object

Tracks progress for a single goal.

#### `to_dict`

```python
def to_dict(self) -> GoalProgressRecord
```

- **Returns:** `GoalProgressRecord`

#### `from_dict`

```python
def from_dict(cls, data: GoalProgressRecord) -> GoalProgress
```

- **Returns:** `GoalProgress`

### `tokenpak.companion.budget.tracker.BudgetTracker`

**Bases:** object

Track and gate costs across a session and day.

#### `__init__`

```python
def __init__(self, db_path: Path, daily_budget: float = 0.0) -> None
```

- **Returns:** `None`

#### `estimate`

```python
def estimate(self, input_tokens: int, cached_tokens: int = 0, model: str = 'sonnet') -> CostEstimate
```

- **Returns:** `CostEstimate`
- **Description:** Estimate cost for a request without recording it.

#### `record`

```python
def record(self, input_tokens: int, output_tokens: int = 0, cached_tokens: int = 0, model: str = 'sonnet', session_id: str = '') -> None
```

- **Returns:** `None`
- **Description:** Record a completed request's cost.

#### `session_cost`

```python
def session_cost(self) -> float
```

- **Returns:** `float`

#### `session_requests`

```python
def session_requests(self) -> int
```

- **Returns:** `int`

### `tokenpak.companion.capsules.builder.CapsuleBuilder`

**Bases:** object

Compress verbose historical context blocks in an LLM request payload.

Parameters
----------
enabled : bool
    Master switch.  When *False* (the default), :meth:`process` is a
    no-op (returns original bytes + empty stats).
min_block_chars : int
    Minimum character length of a text block to qualify for compression.
hot_window : int
    Number of trailing messages to leave untouched (the "hot window").
    Capsule compression applies only to messages *before* this window.

#### `__init__`

```python
def __init__(self, *, enabled: bool = False, min_block_chars: int = DEFAULT_MIN_BLOCK_CHARS, hot_window: int = DEFAULT_HOT_WINDOW) -> None
```

- **Returns:** `None`

#### `process`

```python
def process(self, body_bytes: bytes) -> Tuple[bytes, Dict[str, Any]]
```

- **Returns:** `Tuple[bytes, Dict[str, Any]]`
- **Description:** Process the request body, capsulising eligible context blocks.

### `tokenpak.companion.codex.session_home.SessionLease`

**Bases:** object

Owner-checked ``codex.pid`` lifecycle lease.

The sentinel is a single slot per home, so owning it is inherently
exclusive.  That is correct for a home TokenPak generated and may later
reclaim, where retention must never delete a directory a live session is
using.  It is *not* correct for the user's own shared home: Codex supports
concurrent sessions there, and TokenPak neither created that directory nor
ever deletes it.  A shared-home launch that finds the slot already held by
a live session therefore proceeds without owning it (``owns_sentinel`` is
False) instead of refusing, and every mutation below is a no-op for a
non-owner so it can never disturb the owner's lease.

#### `__init__`

```python
def __init__(self, paths: SessionPaths, sentinel: PidSentinel, *, proc_root: Path = Path('/proc'), home_fd: int | None, owns_sentinel: bool = True) -> None
```

- **Returns:** `None`

#### `assert_home_binding`

```python
def assert_home_binding(self) -> None
```

- **Returns:** `None`
- **Description:** Fail if the selected pathname no longer names the pinned home.

#### `acquire`

```python
def acquire(cls, paths: SessionPaths, *, pid: int | None = None, session_id: str | None = None, proc_root: Path = Path('/proc')) -> 'SessionLease'
```

- **Returns:** `'SessionLease'`

#### `begin_transfer`

```python
def begin_transfer(self) -> None
```

- **Returns:** `None`
- **Description:** Publish a durable handoff marker before spawning the child.

#### `transfer_to`

```python
def transfer_to(self, pid: int) -> None
```

- **Returns:** `None`
- **Description:** Transfer the lease to the spawned child process incarnation.

#### `release`

```python
def release(self) -> bool
```

- **Returns:** `bool`
- **Description:** Remove only the still-matching sentinel owned by this session.

### `tokenpak.companion.codex.session_home.SessionPaths`

**Bases:** object

Every path selected for one Codex launcher invocation.

#### `environment`

```python
def environment(self, base: dict[str, str] | None = None) -> dict[str, str]
```

- **Returns:** `dict[str, str]`
- **Description:** Return a child environment pointing Codex at this home.

#### `report_rows`

```python
def report_rows(self) -> list[tuple[str, str]]
```

- **Returns:** `list[tuple[str, str]]`
- **Description:** Return stable labels used by launcher and doctor output.

### `tokenpak.companion.config.CompanionConfig`

**Bases:** object

Runtime configuration for the companion.

Constructed once at launch, passed to all subsystems.  Subsystems never
read env vars directly — they receive this config object.

#### `run_dir`

```python
def run_dir(self) -> Path
```

- **Returns:** `Path`
- **Description:** Runtime directory for generated config files (AC5).

#### `from_env`

```python
def from_env(cls) -> 'CompanionConfig'
```

- **Returns:** `'CompanionConfig'`
- **Description:** Build config from environment variables + defaults.

#### `profile_overrides`

```python
def profile_overrides(self) -> None
```

- **Returns:** `None`
- **Description:** Apply profile-specific overrides after construction.

### `tokenpak.companion.journal.store.JournalStore`

**Bases:** object

Persistent session journal backed by SQLite.

#### `__init__`

```python
def __init__(self, db_path: Path) -> None
```

- **Returns:** `None`

#### `start_session`

```python
def start_session(self, session_id: str, project_dir: str = '', model: str = '') -> None
```

- **Returns:** `None`
- **Description:** Record a new session start.

#### `end_session`

```python
def end_session(self, session_id: str) -> None
```

- **Returns:** `None`
- **Description:** Record session end and update totals.

#### `add_entry`

```python
def add_entry(self, session_id: str, entry_type: str, content: str, metadata: Optional[dict[str, Any]] = None) -> None
```

- **Returns:** `None`
- **Description:** Append a journal entry to a session.

#### `get_session`

```python
def get_session(self, session_id: str) -> Optional[SessionRecord]
```

- **Returns:** `Optional[SessionRecord]`
- **Description:** Retrieve a session record.

#### `get_entries`

```python
def get_entries(self, session_id: str, entry_type: Optional[str] = None, limit: int = 100) -> list[JournalEntry]
```

- **Returns:** `list[JournalEntry]`
- **Description:** Retrieve journal entries for a session.

#### `record_savings`

```python
def record_savings(self, session_id: str, tool: str, tokens_avoided: int, cost_avoided_usd: float, model_hint: Optional[str] = None, extra: Optional[dict[str, Any]] = None) -> None
```

- **Returns:** `None`
- **Description:** Persist a companion-layer savings event (prompt-side, pre-wire).

#### `session_savings`

```python
def session_savings(self, session_id: str) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Aggregate companion savings for a session.

#### `recent_sessions`

```python
def recent_sessions(self, limit: int = 10) -> list[SessionRecord]
```

- **Returns:** `list[SessionRecord]`
- **Description:** List recent sessions, newest first.

### `tokenpak.companion.mcp.tools.CompanionState`

**Bases:** object

Shared mutable state for the MCP server process.

Lives for the duration of the Claude Code session.  All tools receive
this and can read/mutate it.

#### `budget_tracker`

```python
def budget_tracker(self) -> Any
```

- **Returns:** `Any`

#### `journal_store`

```python
def journal_store(self) -> Any
```

- **Returns:** `Any`

### `tokenpak.companion.memory.decision_memory.DecisionMemoryDB`

**Bases:** object

SQLite-backed decision memory store.

Stores decisions indexed by query hash for fast retrieval and learning.
Confidence scores are updated based on observed outcomes.

#### `__init__`

```python
def __init__(self, db_path: Optional[str] = None) -> None
```

- **Returns:** `None`
- **Description:** Initialize the database.

#### `record`

```python
def record(self, query: str, decision: str, confidence: float = 0.7, notes: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Record a new decision.

#### `retrieve`

```python
def retrieve(self, query: Optional[str] = None, query_hash: Optional[str] = None, top_k: int = 5) -> List[DecisionRecord]
```

- **Returns:** `List[DecisionRecord]`
- **Description:** Retrieve decisions by query or query_hash, sorted by confidence (descending).

#### `update_confidence`

```python
def update_confidence(self, record_id: str, new_confidence: float) -> bool
```

- **Returns:** `bool`
- **Description:** Update the confidence score for a decision.

#### `record_outcome`

```python
def record_outcome(self, record_id: str, outcome: str, success: bool, notes: Optional[str] = None) -> bool
```

- **Returns:** `bool`
- **Description:** Record the outcome of a decision and optionally adjust confidence.

#### `get`

```python
def get(self, record_id: str) -> Optional[DecisionRecord]
```

- **Returns:** `Optional[DecisionRecord]`
- **Description:** Retrieve a specific decision by ID.

#### `all`

```python
def all(self, order_by: str = 'timestamp DESC') -> List[DecisionRecord]
```

- **Returns:** `List[DecisionRecord]`
- **Description:** Retrieve all decisions.

#### `count`

```python
def count(self) -> int
```

- **Returns:** `int`
- **Description:** Return total number of decisions.

#### `delete`

```python
def delete(self, record_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Delete a decision by ID.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Clear all decisions from the database.

### `tokenpak.companion.recall.store.RecallStore`

**Bases:** object

Recall storage handle.

Open via :meth:`RecallStore.open` (or the module-level
:func:`open_recall_store`). The instance is a context manager so
callers can ``with RecallStore.open() as store: ...`` and have the
connection closed deterministically.

#### `__init__`

```python
def __init__(self, conn: sqlite3.Connection, path: Path) -> None
```

- **Returns:** `None`

#### `open`

```python
def open(cls, path: Optional[Path] = None) -> 'RecallStore'
```

- **Returns:** `'RecallStore'`
- **Description:** Open (and migrate if needed) the recall store at ``path``.

#### `conn`

```python
def conn(self) -> sqlite3.Connection
```

- **Returns:** `sqlite3.Connection`
- **Description:** The underlying SQLite connection.

#### `path`

```python
def path(self) -> Path
```

- **Returns:** `Path`
- **Description:** The filesystem path the store was opened at.

#### `schema_version`

```python
def schema_version(self) -> int
```

- **Returns:** `int`
- **Description:** The schema version currently applied to the underlying DB.

#### `upsert_pak`

```python
def upsert_pak(self, *, pak_id: str, pak_type: str, source_type: str, authority: str, title: str, content_hash: str, summary: str = '', project: Optional[str] = None, topic: Optional[str] = None, superseded_by: Optional[str] = None, now: Optional[str] = None) -> UpsertResult
```

- **Returns:** `UpsertResult`
- **Raises:** `ValueError`, `empty / whitespace-only.`, `sqlite3.IntegrityError`, `unknown ``superseded_by``).`
- **Description:** Insert or update a single Pak metadata row.

#### `list_paks`

```python
def list_paks(self, filters: Optional[PakListFilters] = None) -> PakListResult
```

- **Returns:** `PakListResult`
- **Raises:** `ValueError`, `cursor.`
- **Description:** Return a paginated page of ``paks`` rows, newest-first.

#### `get_pak`

```python
def get_pak(self, pak_id: str) -> Optional[PakRow]
```

- **Returns:** `Optional[PakRow]`
- **Description:** Return the ``paks`` row for ``pak_id``, or ``None`` if absent.

#### `set_pak_reason_codes`

```python
def set_pak_reason_codes(self, pak_id: str, codes: Sequence[ReasonCodeEntry], *, now: Optional[str] = None) -> None
```

- **Returns:** `None`
- **Raises:** `ValueError`, `reason_code`` is empty/whitespace, an entry's ``weight`, `falls outside ``[0.0, 1.0]``, or ``codes`` contains`, `duplicate ``reason_code`` values.`, `sqlite3.IntegrityError`, `in ``paks`` (foreign-key violation).`
- **Description:** Replace the reason-code set for ``pak_id``.

#### `get_pak_reason_codes`

```python
def get_pak_reason_codes(self, pak_id: str) -> list[ReasonCodeEntry]
```

- **Returns:** `list[ReasonCodeEntry]`
- **Description:** Return the reason codes attached to ``pak_id``.

#### `set_pak_risk_flags`

```python
def set_pak_risk_flags(self, pak_id: str, flags: Sequence[RiskFlagEntry], *, now: Optional[str] = None) -> None
```

- **Returns:** `None`
- **Raises:** `ValueError`, `risk_flag`` is empty/whitespace, an entry's ``severity`, `is not in ``{"info", "warn", "block"}``, or ``flags`, `contains duplicate ``risk_flag`` values.`, `sqlite3.IntegrityError`, `in ``paks``.`
- **Description:** Replace the risk-flag set for ``pak_id``.

#### `get_pak_risk_flags`

```python
def get_pak_risk_flags(self, pak_id: str) -> list[RiskFlagEntry]
```

- **Returns:** `list[RiskFlagEntry]`
- **Description:** Return the risk flags attached to ``pak_id``.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`
- **Description:** Close the underlying connection.

### `tokenpak.companion.stream.StreamTruncatedError`

**Bases:** Exception

Clean, structured error raised when a provider stream is truncated.

Carries the partial content received so far, a stable error code, a
remedy hint, and the trace_id for replay. The string form is safe to log
(it contains the code + trace_id but never raw provider body content).

#### `__init__`

```python
def __init__(self, *, partial_content: bytes, trace_id: str, reason: str, last_event_kind: str = '', bytes_received: int = 0, time_since_last_chunk_ms: int = 0, remedy: str = STREAM_TRUNCATED_REMEDY) -> None
```

- **Returns:** `None`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return a JSON-safe error envelope for the calling client.

### `tokenpak.companion.trace.TokenPakTrace`

**Bases:** object

Diagnostic metadata attached to a TokenPak proxy response.

All fields are plain-Python types so the dataclass can be serialised with
``dataclasses.asdict`` and round-tripped through JSON / base64url.

Attributes
----------
trace_id:
    UUIDv4 unique per request.
timestamp:
    ISO-8601 UTC timestamp when the trace was created.
routing:
    Routing decision: provider chosen, model, and reason string.
budget:
    Budget tier applied, token allocation, and list of reasons.
retrieval:
    Context-retrieval stats: sources used, top-k, coverage score,
    and whether the semantic cache was hit.
packing:
    Wire-format packing stats: kept/dropped turns and injected tokens.
economics:
    Token economics: actual tokens consumed, cost in USD, and savings
    (i.e. tokens/cost avoided by retrieval / caching).
warnings:
    Free-form warning strings raised during processing (non-fatal).

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return a plain-dict representation suitable for JSON encoding.

#### `to_json`

```python
def to_json(self) -> str
```

- **Returns:** `str`
- **Description:** Serialise to a compact JSON string.

#### `to_base64url`

```python
def to_base64url(self) -> str
```

- **Returns:** `str`
- **Description:** Encode to URL-safe base64 (no padding) for use in HTTP headers.

#### `from_base64url`

```python
def from_base64url(cls, encoded: str) -> 'TokenPakTrace'
```

- **Returns:** `'TokenPakTrace'`
- **Description:** Decode a trace from a base64url-encoded header value.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'TokenPakTrace'
```

- **Returns:** `'TokenPakTrace'`
- **Description:** Construct from a plain dict (e.g. parsed from JSON envelope).

### `tokenpak.companion.trace.TraceBuilder`

**Bases:** object

Fluent builder for :class:`TokenPakTrace`.

Example::

    trace = (
        TraceBuilder()
        .routing("anthropic", "claude-3-haiku", "economy_tier")
        .budget("economy", 4096, ["cost_optimise"])
        .retrieval(["semantic_cache"], top_k=5, coverage=0.87, cache_hit=True)
        .packing(kept_turns=6, dropped_turns=2, inject_tokens=312)
        .economics(actual_tokens=1800, cost_usd=0.0012, savings_usd=0.0038)
        .build()
    )

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `routing`

```python
def routing(self, provider: str, model: str, reason: str = '', *, rule_id: Optional[str] = None) -> 'TraceBuilder'
```

- **Returns:** `'TraceBuilder'`
- **Description:** Record routing decision: which provider/model was selected and why.

#### `budget`

```python
def budget(self, tier: str, tokens: int, reasons: Optional[List[str]] = None, *, trim_applied: bool = False) -> 'TraceBuilder'
```

- **Returns:** `'TraceBuilder'`
- **Description:** Record budget tier and token allocation for this request.

#### `retrieval`

```python
def retrieval(self, sources: Optional[List[str]] = None, top_k: int = 0, coverage: float = 0.0, cache_hit: bool = False, *, retrieval_ms: Optional[float] = None) -> 'TraceBuilder'
```

- **Returns:** `'TraceBuilder'`
- **Description:** Record context retrieval stats: sources used and coverage metrics.

#### `packing`

```python
def packing(self, kept_turns: int = 0, dropped_turns: int = 0, inject_tokens: int = 0, *, compression_ratio: Optional[float] = None) -> 'TraceBuilder'
```

- **Returns:** `'TraceBuilder'`
- **Description:** Record compression packing stats: turns kept/dropped and injection.

#### `economics`

```python
def economics(self, actual_tokens: int = 0, cost_usd: float = 0.0, savings_usd: float = 0.0, *, baseline_tokens: Optional[int] = None, baseline_cost_usd: Optional[float] = None) -> 'TraceBuilder'
```

- **Returns:** `'TraceBuilder'`
- **Description:** Record token economics.

#### `warn`

```python
def warn(self, message: str) -> 'TraceBuilder'
```

- **Returns:** `'TraceBuilder'`
- **Description:** Attach a warning message to the trace (non-fatal issues).

#### `build`

```python
def build(self) -> TokenPakTrace
```

- **Returns:** `TokenPakTrace`
- **Description:** Construct and return the :class:`TokenPakTrace`.

### `tokenpak.compression.alias_compressor.AliasCompressor`

**Bases:** object

Entity-alias compressor.

Parameters
----------
min_occurrences:
    Minimum number of times an entity must appear to be aliased.
min_entity_length:
    Minimum character length of an entity to be considered.
entity_types:
    Which entity types to alias. Subset of
    ``["file", "url", "class", "env", "service"]``.

#### `__init__`

```python
def __init__(self, min_occurrences: int = 3, min_entity_length: int = 20, entity_types: Optional[List[str]] = None) -> None
```

- **Returns:** `None`

#### `compress`

```python
def compress(self, messages: List[Dict[str, Any]]) -> AliasResult
```

- **Returns:** `AliasResult`
- **Description:** Alias repeated entities in *messages*.

#### `expand`

```python
def expand(self, text: str, symbol_table: Dict[str, str]) -> str
```

- **Returns:** `str`
- **Description:** Reverse alias compression: replace aliases with originals.

### `tokenpak.compression.assembler.CanonBlockRegistry`

**Bases:** object

Lightweight file-based registry for CANON blocks.

Stores canonical block wire text at:
  .tpk/blocks/BLOCK_ID@vN.tpkb

Tracks versions in manifest:
  .tpk/blocks/manifest.json  →  {block_id: {hash, version}}

#### `__init__`

```python
def __init__(self, base_dir: str = '.tpk') -> Any
```

- **Returns:** `Any`

#### `get_or_register`

```python
def get_or_register(self, block_id: str, content: str) -> tuple[str, bool]
```

- **Returns:** `tuple[str, bool]`
- **Description:** Register or look up a CANON block.

#### `current_version`

```python
def current_version(self, block_id: str) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Return current version string for a block_id, or None if unknown.

#### `read_block_content`

```python
def read_block_content(self, block_id: str, version_str: str) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Read stored .tpkb content for a block/version pair.

### `tokenpak.compression.assembler.ContextAssembler`

**Bases:** object

Assembles TPK wire-format context payloads.

  Session state (which blocks have been sent at which version) is
  persisted to .tpk/state/session_<id>.state.json so it survives
  across turns without holding all context in memory.

  Usage:
      assembler = ContextAssembler(session_id="abc123")

      # First turn — inlines SOUL.md, sends ref for TOOLS if unchanged
      canon = assembler.assemble_context({
          "SOUL":  (soul_content, None),   # version auto-detected
          "TOOLS": (tools_content, None),
      })
      # canon → "CANON:
SOUL=[full content]
TOOLS=[full content]"

      # Second turn — sends refs only
      canon = assembler.assemble_context({...})
      # canon → "CANON:
SOUL=@SOUL#v1
TOOLS=@TOOLS#v1"

#### `__init__`

```python
def __init__(self, session_id: str, base_dir: str = '.tpk') -> Any
```

- **Returns:** `Any`

#### `sent_blocks`

```python
def sent_blocks(self) -> dict[str, str]
```

- **Returns:** `dict[str, str]`
- **Description:** Map of {block_id: version_str} for blocks already sent this session.

#### `add_canon_block`

```python
def add_canon_block(self, block_id: str, block_content: str, version: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Produce the wire entry for one CANON block.

#### `assemble_context`

```python
def assemble_context(self, required_blocks: dict[str, tuple[str, Optional[str]]], save_session: bool = True) -> str
```

- **Returns:** `str`
- **Description:** Build the full CANON section for a request payload.

#### `assemble_full_payload`

```python
def assemble_full_payload(self, required_blocks: dict[str, tuple[str, Optional[str]]], state_manager: _StateManagerLike | None = None, evidence_pack: EvidencePack | None = None, recent_text: str = '', tools_text: str = '', budgeter: _BudgeterLike | None = None) -> str
```

- **Returns:** `str`
- **Description:** Build the complete TPK payload: CANON section + optional STATE_JSON.

#### `session_summary`

```python
def session_summary(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return current session metadata for logging/debugging.

### `tokenpak.compression.budgets.policy.BlockPolicy`

**Bases:** object

Per-block-type compaction policy.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'BlockPolicy'
```

- **Returns:** `'BlockPolicy'`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.compression.budgets.policy.CompactionPolicy`

**Bases:** object

Top-level compaction policy.

Attributes:
    mode:            Default compaction mode for all blocks.
    max_tokens:      Global token budget ceiling (across all blocks).
    priority_order:  Block types ordered by priority when trimming.
    per_block_limits: Per-block-type overrides (keyed by block type).

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'CompactionPolicy'
```

- **Returns:** `'CompactionPolicy'`
- **Description:** Build policy from a plain dictionary (e.g. parsed JSON).

#### `default`

```python
def default(cls) -> 'CompactionPolicy'
```

- **Returns:** `'CompactionPolicy'`
- **Description:** Return the default balanced policy.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Serialise to a plain dictionary suitable for JSON round-trip.

#### `compact_block`

```python
def compact_block(self, text: str, block_type: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Compact *text* according to this policy.

#### `resolve_mode`

```python
def resolve_mode(self, block_type: Optional[str] = None) -> CompactionMode
```

- **Returns:** `CompactionMode`
- **Description:** Return the effective CompactionMode for *block_type*.

### `tokenpak.compression.budgets.policy.TopicAwarePolicy`

**Bases:** CompactionPolicy

Topic-aware compaction policy with differential compression.

Extends CompactionPolicy to support topic-aware segmentation and
compression. Active topics receive richer context (less compression),
while inactive topics are summarized aggressively.

Attributes:
    active_mode:       Compaction mode for active topics (default: balanced).
    inactive_mode:     Compaction mode for inactive topics (default: aggressive).
    activity_threshold: Score threshold for topic activity classification (0.0-1.0).
    per_topic_limits:  Optional per-topic max token budgets.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'TopicAwarePolicy'
```

- **Returns:** `'TopicAwarePolicy'`
- **Description:** Build policy from dictionary with topic-aware extensions.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Serialize to dictionary with topic-aware fields.

#### `compact_with_topics`

```python
def compact_with_topics(self, text: str) -> str
```

- **Returns:** `str`
- **Description:** Compact text using topic-aware segmentation.

#### `compact_block_with_topics`

```python
def compact_block_with_topics(self, text: str, block_type: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Compact block with optional topic awareness.

### `tokenpak.compression.budgets.topic_aware.TopicBoundaryDetector`

**Bases:** object

Deterministic topic boundary detection using similarity signals.

Identifies transitions between distinct topics by comparing semantic
similarity between consecutive text chunks.

#### `__init__`

```python
def __init__(self, chunk_size: int = 100, similarity_threshold: float = 0.3, min_segment_chars: int = 50) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize detector.

#### `segment`

```python
def segment(self, text: str) -> List[TopicSegment]
```

- **Returns:** `List[TopicSegment]`
- **Description:** Segment text into topic boundaries.

### `tokenpak.compression.budgets.topic_aware.TopicSegment`

**Bases:** object

Represents a contiguous topic span in text.

#### `is_active`

```python
def is_active(self) -> bool
```

- **Returns:** `bool`
- **Description:** Topic is active if activity_score exceeds threshold.

#### `length_chars`

```python
def length_chars(self) -> int
```

- **Returns:** `int`
- **Description:** Length in characters.

#### `to_dict`

```python
def to_dict(self) -> dict[str, int | float | str]
```

- **Returns:** `dict[str, int | float | str]`
- **Description:** Serialize to dict.

### `tokenpak.compression.context_composer.ContextComposer`

**Bases:** object

Pack prompt components into a budget-constrained context window.

#### `compose`

```python
def compose(self, *, budget: int, system_prompt: str = '', session_state: Optional[str] = None, user_request: str = '', retrieved_chunks: Optional[list[RetrievedChunk]] = None, recent_turns: Optional[list[dict[str, Any]]] = None, previous_phase_summary: Optional[str] = None) -> ComposedContext
```

- **Returns:** `ComposedContext`
- **Description:** Pack all components within *budget* tokens.

### `tokenpak.compression.core.IndexRegistry`

**Bases:** object

Return value of index_directory(). Has .blocks and .tokenpak_dir.

#### `__init__`

```python
def __init__(self, vault_dir: Path, blocks: dict[str, _BlockMetadata]) -> Any
```

- **Returns:** `Any`

### `tokenpak.compression.dictionary.CompressionDictionary`

**Bases:** object

Project-specific phrase → token replacement pass.

Parameters
----------
dict_path:
    Path to the JSON dictionary file.  Defaults to
    ``~/.tokenpak/compression_dict.json``.
case_sensitive:
    Whether phrase matching is case-sensitive (default ``True``).
auto_learn_threshold:
    Minimum occurrences before a phrase appears in :attr:`suggestions`.
auto_learn_min_length:
    Minimum character length of a phrase to track for auto-learn.

#### `__init__`

```python
def __init__(self, dict_path: Optional[Path | str] = None, *, case_sensitive: bool = True, auto_learn_threshold: int = 3, auto_learn_min_length: int = _MIN_PHRASE_LEN) -> None
```

- **Returns:** `None`

#### `dictionary`

```python
def dictionary(self) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`
- **Description:** Current phrase → token mapping (read-only copy).

#### `suggestions`

```python
def suggestions(self) -> List[SuggestedEntry]
```

- **Returns:** `List[SuggestedEntry]`
- **Description:** Phrases that have been seen at least ``auto_learn_threshold`` times

#### `reload`

```python
def reload(self) -> None
```

- **Returns:** `None`
- **Description:** Re-read the dictionary file from disk.

#### `apply`

```python
def apply(self, messages: List[Dict[str, Any]]) -> DictionaryResult
```

- **Returns:** `DictionaryResult`
- **Description:** Apply dictionary replacements to a list of messages.

#### `suggest_entries`

```python
def suggest_entries(self) -> List[Dict[str, str]]
```

- **Returns:** `List[Dict[str, str]]`
- **Description:** Return a list of ``{phrase: suggested_token}`` dicts for auto-learned

#### `save_suggestions`

```python
def save_suggestions(self, min_occurrences: int = 1) -> int
```

- **Returns:** `int`
- **Description:** Append accepted suggestions to the dictionary file on disk.

### `tokenpak.compression.dictionary.SuggestedEntry`

**Bases:** object

A phrase the auto-learner thinks should be added to the dictionary.

#### `as_dict`

```python
def as_dict(self) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`

### `tokenpak.compression.directives.DirectiveApplier`

**Bases:** object

Apply compression directives to a messages list.

Passes messages through unmodified when no directives are configured.

Parameters
----------
directives : list[dict], optional
    List of directive dicts to apply.

#### `__init__`

```python
def __init__(self, directives: Optional[List[Dict[str, Any]]] = None) -> None
```

- **Returns:** `None`

#### `apply`

```python
def apply(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Apply registered directives to messages.

#### `add_directive`

```python
def add_directive(self, directive: Dict[str, Any]) -> None
```

- **Returns:** `None`
- **Description:** Register a directive.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Remove all registered directives.

#### `directive_count`

```python
def directive_count(self) -> int
```

- **Returns:** `int`

### `tokenpak.compression.directives.DirectiveCache`

**Bases:** object

In-process cache for server directive responses with 5-minute TTL.

#### `__init__`

```python
def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None
```

- **Returns:** `None`

#### `get`

```python
def get(self, raw: DirectivePayload) -> DirectivePayload | None
```

- **Returns:** `DirectivePayload | None`

#### `set`

```python
def set(self, raw: DirectivePayload, parsed: DirectivePayload) -> None
```

- **Returns:** `None`

#### `invalidate`

```python
def invalidate(self, raw: DirectivePayload) -> bool
```

- **Returns:** `bool`

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`

#### `purge_expired`

```python
def purge_expired(self) -> int
```

- **Returns:** `int`

#### `size`

```python
def size(self) -> int
```

- **Returns:** `int`

### `tokenpak.compression.doc_compressor.DocCompressor`

**Bases:** object

Document-level compression entry point (C4).

Wraps :class:`~tokenpak.compression.salience.doc_extractor.DocExtractor`
to extract high-signal content from markdown/RST documents.

Parameters
----------
annotation_context : int
    Lines of context to retain after each TODO/FIXME/NOTE/etc.
include_rst_headings : bool
    Also detect RST-style underline headings.

#### `__init__`

```python
def __init__(self, annotation_context: int = 2, include_rst_headings: bool = True) -> None
```

- **Returns:** `None`

#### `compress`

```python
def compress(self, markdown: str) -> str
```

- **Returns:** `str`
- **Description:** Compress *markdown* and return a compact, high-signal string.

### `tokenpak.compression.engines.base.CompactionEngine`

**Bases:** ABC

Base class for compaction engines.

#### `compact`

```python
def compact(self, text: str, hints: Optional[CompactionHints] = None) -> str
```

- **Returns:** `str`
- **Description:** Compact text according to hints.

#### `estimate_tokens`

```python
def estimate_tokens(self, text: str) -> int
```

- **Returns:** `int`
- **Description:** Estimate token count for text.

### `tokenpak.compression.engines.heuristic.HeuristicEngine`

**Bases:** CompactionEngine

Fast heuristic compaction using rule-based text processing.

No ML dependencies required. Suitable for:
- Real-time interactive use
- Resource-constrained environments
- Baseline comparison

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `compact`

```python
def compact(self, text: str, hints: Optional[CompactionHints] = None) -> str
```

- **Returns:** `str`
- **Description:** Compact using heuristic rules.

### `tokenpak.compression.engines.llmlingua.LLMLinguaEngine`

**Bases:** CompactionEngine

ML-powered compaction using Microsoft LLMLingua.

Requires: pip install tokenpak[compression]

Provides:
- Higher compression ratios (5-20x vs 2-5x heuristic)
- Better semantic preservation
- Configurable force tokens

Tradeoffs:
- Slower (requires model inference)
- Higher memory usage
- Requires model download on first use

#### `__init__`

```python
def __init__(self, model_name: str = 'microsoft/llmlingua-2-xlm-roberta-large-meetingbank') -> Any
```

- **Returns:** `Any`

#### `compact`

```python
def compact(self, text: str, hints: Optional[CompactionHints] = None) -> str
```

- **Returns:** `str`
- **Description:** Compact using LLMLingua-2.

#### `estimate_tokens`

```python
def estimate_tokens(self, text: str) -> int
```

- **Returns:** `int`
- **Description:** Estimate tokens using the model's tokenizer if available.

### `tokenpak.compression.evidence_pack.EvidenceItem`

**Bases:** object

A single evidence item with provenance.

#### `__init__`

```python
def __init__(self, src: str, ref: str, span: str, score: float, text: str) -> Any
```

- **Returns:** `Any`

#### `to_wire_line`

```python
def to_wire_line(self, index: int) -> str
```

- **Returns:** `str`
- **Description:** Render as EVIDENCE wire line.

#### `to_dict`

```python
def to_dict(self) -> dict[str, str | float]
```

- **Returns:** `dict[str, str | float]`

### `tokenpak.compression.evidence_pack.EvidencePack`

**Bases:** object

Builds an EVIDENCE section from memory search results and files.

Integration with memory search (replacing full chunk dumps):
    # Old way:
    memory_results = memory.search(query, top_k=10)
    context_text = "\n\n".join([r['text'] for r in memory_results])

    # New way:
    memory_results = memory.search(query, top_k=10)
    pack = EvidencePack()
    pack.add_from_memory(memory_results, query, max_items=10)
    context_text = pack.to_wire_format()

#### `__init__`

```python
def __init__(self, use_reranker: bool = False) -> Any
```

- **Returns:** `Any`

#### `add_from_memory`

```python
def add_from_memory(self, memory_chunks: list[dict[str, object]], query: str, max_items: int = 10, max_tokens_each: int = 50) -> None
```

- **Returns:** `None`
- **Description:** Convert memory search results into evidence items.

#### `add_from_file`

```python
def add_from_file(self, file_path: str, query: str, max_tokens_each: int = 80, ref_override: Optional[str] = None) -> None
```

- **Returns:** `None`
- **Description:** Extract the most relevant span from a file.

#### `add_from_log`

```python
def add_from_log(self, log_ref: str, log_text: str, query: str, turn_range: Optional[str] = None, max_tokens_each: int = 50) -> None
```

- **Returns:** `None`
- **Description:** Extract span from a session log or JSONL.

#### `add_item`

```python
def add_item(self, src: str, ref: str, text: str, score: float = 1.0, span: str = 'manual') -> None
```

- **Returns:** `None`
- **Description:** Manually add a pre-extracted evidence item.

#### `to_wire_format`

```python
def to_wire_format(self) -> str
```

- **Returns:** `str`
- **Description:** Format evidence pack for LLM payload.

#### `filter_by_score`

```python
def filter_by_score(self, min_score: float = 0.1) -> 'EvidencePack'
```

- **Returns:** `'EvidencePack'`
- **Description:** Return new EvidencePack with items above min_score.

#### `top_n`

```python
def top_n(self, n: int) -> 'EvidencePack'
```

- **Returns:** `'EvidencePack'`
- **Description:** Return new EvidencePack with top N items by score.

#### `sort_by_score`

```python
def sort_by_score(self, descending: bool = True) -> None
```

- **Returns:** `None`
- **Description:** Sort items in-place by score.

#### `total_tokens`

```python
def total_tokens(self) -> int
```

- **Returns:** `int`
- **Description:** Estimate total tokens in all evidence items.

### `tokenpak.compression.extraction.extractor.EntityExtractor`

**Bases:** object

Pure regex/keyword/heuristic extractor with stable outputs.

#### `extract`

```python
def extract(self, text: str) -> EntitySet
```

- **Returns:** `EntitySet`

#### `compact_text`

```python
def compact_text(self, entity_set: EntitySet) -> str
```

- **Returns:** `str`
- **Description:** Compact structured injection format (intended to replace raw docs in context).

#### `choose_injection`

```python
def choose_injection(raw_text: str, entity_set: EntitySet, prefer_compact: bool = True) -> str
```

- **Returns:** `str`
- **Description:** Return compact entities or raw text when compact mode is disabled.

### `tokenpak.compression.extraction.models.EntitySet`

**Bases:** object

#### `by_type`

```python
def by_type(self, entity_type: EntityType) -> list[Entity]
```

- **Returns:** `list[Entity]`

#### `to_compact_dict`

```python
def to_compact_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.compression.fidelity_tiers.FidelityTier`

**Bases:** str, Enum

#### `ascending`

```python
def ascending(cls) -> List['FidelityTier']
```

- **Returns:** `List['FidelityTier']`

#### `descending`

```python
def descending(cls) -> List['FidelityTier']
```

- **Returns:** `List['FidelityTier']`

### `tokenpak.compression.fidelity_tiers.TierGenerator`

**Bases:** object

Generates all fidelity tiers from a raw source block.

Supports Python source (AST-based extraction) and plain text
(regex / heuristic extraction).

#### `generate`

```python
def generate(cls, source: str, source_id: str = '', *, changed_lines: Optional[List[int]] = None, language: str = 'python', metadata: Optional[Dict[str, object]] = None) -> TieredBlock
```

- **Returns:** `TieredBlock`
- **Description:** Generate all tiers for *source* and return a :class:`TieredBlock`.

### `tokenpak.compression.fidelity_tiers.TierSelector`

**Bases:** object

Selects the cheapest sufficient fidelity tier.

Selection matrix
----------------
complexity_score : float
    0.0–10.0 scale (compatible with ``tokenpak.compression.complexity`` scorer).
budget_remaining : float
    Fraction of token budget still available (0.0 = exhausted, 1.0 = full).
relevance_score : float, optional
    0.0–1.0 relevance of this block to the current task.

Policy
------
- budget_remaining < 0.10 → always L4 (emergency)
- complexity ≥ 7.0 and budget ≥ 0.5 → L0
- complexity ≥ 7.0 and budget ≥ 0.25 → L1
- complexity ≥ 4.0 and budget ≥ 0.4 → L2
- complexity ≥ 4.0                   → L3
- else                               → L4

#### `select`

```python
def select(complexity_score: float, budget_remaining: float, relevance_score: float = 1.0) -> FidelityTier
```

- **Returns:** `FidelityTier`
- **Description:** Return the recommended :class:`FidelityTier`.

#### `select_for_block`

```python
def select_for_block(block: TieredBlock, complexity_score: float, budget_remaining: float, relevance_score: float = 1.0) -> str
```

- **Returns:** `str`
- **Description:** Select the best available tier for *block* and return its text.

### `tokenpak.compression.fidelity_tiers.TierStore`

**Bases:** object

In-memory store for indexed :class:`TieredBlock` objects.

In production this would persist to disk / a database.  For now it acts
as a lightweight dict-backed registry used during a session.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `index`

```python
def index(self, block: TieredBlock) -> None
```

- **Returns:** `None`
- **Description:** Add or replace a TieredBlock in the store.

#### `index_source`

```python
def index_source(self, source: str, source_id: str, *, changed_lines: Optional[List[int]] = None, language: str = 'python', metadata: Optional[Dict[str, object]] = None) -> TieredBlock
```

- **Returns:** `TieredBlock`
- **Description:** Generate tiers from *source* and index the resulting block.

#### `get`

```python
def get(self, source_id: str) -> Optional[TieredBlock]
```

- **Returns:** `Optional[TieredBlock]`
- **Description:** Return the :class:`TieredBlock` for *source_id*, or ``None``.

#### `fetch`

```python
def fetch(self, source_id: str, complexity_score: float, budget_remaining: float, relevance_score: float = 1.0) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Return the cheapest-sufficient text for *source_id*, or ``None`` if unknown.

#### `ids`

```python
def ids(self) -> List[str]
```

- **Returns:** `List[str]`

### `tokenpak.compression.fidelity_tiers.TieredBlock`

**Bases:** object

Holds all fidelity representations for a single source block.

Parameters
----------
source_id:
    Unique identifier for the source (file path, chunk id, etc.).
tiers:
    Mapping of FidelityTier → text representation.
metadata:
    Optional free-form metadata dict (file path, language, etc.).

#### `get`

```python
def get(self, tier: FidelityTier, *, fallback: bool = True) -> str
```

- **Returns:** `str`
- **Description:** Return the text for *tier*, falling back to the next richer tier if missing.

#### `available_tiers`

```python
def available_tiers(self) -> List[FidelityTier]
```

- **Returns:** `List[FidelityTier]`
- **Description:** Return tiers present in this block, sorted cheapest → richest.

#### `token_estimate`

```python
def token_estimate(self, tier: FidelityTier) -> int
```

- **Returns:** `int`
- **Description:** Rough token estimate for *tier* (4 chars ≈ 1 token).

### `tokenpak.compression.fingerprinting.generator.Fingerprint`

**Bases:** object

Structural fingerprint of a prompt — no raw content.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.compression.fingerprinting.generator.FingerprintGenerator`

**Bases:** object

Generates a structural Fingerprint from prompt text or message lists.

    Usage:
        gen = FingerprintGenerator()
        fp = gen.generate("You are a helpful assistant.

What is 2+2?")
        fp = gen.generate_from_messages([{"role": "system", "content": "..."}])

#### `__init__`

```python
def __init__(self, include_hashes: bool = False, model_hint: Optional[str] = None) -> Any
```

- **Returns:** `Any`

#### `generate`

```python
def generate(self, text: str) -> Fingerprint
```

- **Returns:** `Fingerprint`
- **Description:** Generate a fingerprint from a single prompt string.

#### `generate_from_messages`

```python
def generate_from_messages(self, messages: list[dict[str, object]]) -> Fingerprint
```

- **Returns:** `Fingerprint`
- **Description:** Generate a fingerprint from an OpenAI-style messages list.

### `tokenpak.compression.fingerprinting.sync.Directive`

**Bases:** object

A recipe/strategy directive received from the intelligence server.

#### `from_dict`

```python
def from_dict(cls, d: Mapping[str, object]) -> 'Directive'
```

- **Returns:** `'Directive'`

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.compression.fingerprinting.sync.FingerprintSync`

**Bases:** object

Syncs fingerprints to the intelligence server and caches returned directives.

Falls back to cached or OSS directives when offline.

Usage:
    sync = FingerprintSync()
    result = sync.sync(fingerprint)
    result = sync.sync(fingerprint, dry_run=True)
    directives = sync.cached_directives(fingerprint_id)
    sync.clear_cache()

#### `__init__`

```python
def __init__(self, server_url: Optional[str] = None, cache_dir: Optional[Path] = None, ttl: int = _DEFAULT_TTL, privacy_level: PrivacyLevel = PrivacyLevel.STANDARD, timeout: int = _REQUEST_TIMEOUT) -> Any
```

- **Returns:** `Any`

#### `sync`

```python
def sync(self, fingerprint: Fingerprint, dry_run: bool = False, skip_cache: bool = False) -> SyncResult
```

- **Returns:** `SyncResult`
- **Description:** Sync fingerprint to intelligence server and return directives.

#### `cached_directives`

```python
def cached_directives(self, fingerprint_id: str) -> list[Directive]
```

- **Returns:** `list[Directive]`
- **Description:** Return cached directives for a fingerprint_id, or [] if missing/expired.

#### `clear_cache`

```python
def clear_cache(self, fingerprint_id: Optional[str] = None) -> int
```

- **Returns:** `int`
- **Description:** Clear cached directives.

#### `cache_status`

```python
def cache_status(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return a summary of the local directive cache.

### `tokenpak.compression.fingerprinting.sync.SyncResult`

**Bases:** object

Result of a fingerprint sync operation.

#### `from_cache`

```python
def from_cache(self) -> bool
```

- **Returns:** `bool`

#### `is_fallback`

```python
def is_fallback(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.compression.instruction_table.InstructionCompressionStats`

**Bases:** object

#### `total_tokens_saved`

```python
def total_tokens_saved(self) -> int
```

- **Returns:** `int`

### `tokenpak.compression.instruction_table.InstructionTable`

**Bases:** object

#### `__init__`

```python
def __init__(self, path: str | Path | None = None, min_tokens: int = 100, min_occurrences: int = 2, manual_entries: dict[str, str] | None = None) -> None
```

- **Returns:** `None`

#### `compress_messages`

```python
def compress_messages(self, messages: list[dict[str, Any]], *, context_budget_tight: bool = True, persist: bool = True) -> tuple[list[dict[str, Any]], InstructionCompressionStats]
```

- **Returns:** `tuple[list[dict[str, Any]], InstructionCompressionStats]`

#### `expand_messages`

```python
def expand_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`

### `tokenpak.compression.pack.CompiledResult`

**Bases:** object

Return value of ContextPack.compile().

Stack-neutral output methods allow the compiled result to be used
with any LLM provider without requiring the TokenPak gateway.

#### `to_prompt`

```python
def to_prompt(self) -> str
```

- **Returns:** `str`
- **Description:** Return compiled context as plain text.

#### `to_messages`

```python
def to_messages(self) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Return compiled context as OpenAI-format messages list.

#### `to_messages_with_system`

```python
def to_messages_with_system(self, system: Optional[str] = None) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Return compiled context with an optional separate system message.

#### `to_anthropic`

```python
def to_anthropic(self) -> Tuple[str, List[Dict[str, Any]]]
```

- **Returns:** `Tuple[str, List[Dict[str, Any]]]`
- **Description:** Return ``(system_prompt, messages)`` in Anthropic SDK format.

#### `to_json`

```python
def to_json(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return the full compiled result as a JSON-serializable dict.

### `tokenpak.compression.pack.ContextPack`

**Bases:** object

Budget-aware context compiler with full transparency reports.

Args:
    budget:            Total token budget for the compiled output.
    quality_threshold: Blocks with quality < this are REMOVED (default 0.5).
    separator:         String placed between blocks in text output.

#### `__init__`

```python
def __init__(self, budget: int = 8000, quality_threshold: float = 0.5, separator: str = '\n\n---\n\n') -> None
```

- **Returns:** `None`

#### `add`

```python
def add(self, block: PackBlock) -> 'ContextPack'
```

- **Returns:** `'ContextPack'`
- **Description:** Add a block. Returns self for chaining.

#### `clear`

```python
def clear(self) -> 'ContextPack'
```

- **Returns:** `'ContextPack'`
- **Description:** Remove all blocks.

#### `compile`

```python
def compile(self) -> CompiledResult
```

- **Returns:** `CompiledResult`
- **Description:** Compile all blocks into a budgeted output with a full report.

### `tokenpak.compression.pipeline.CompressionPipeline`

**Bases:** object

Orchestrates the TokenPak compression pipeline.

Stages (all optional, enabled by default):
  1. dedup    — remove duplicate / near-duplicate message turns
  2. segment  — classify messages into typed Segment objects
  3. directives — apply directive rules (extensible via DirectiveApplier)

Custom compression hooks can be added via :meth:`add_hook`.

Parameters
----------
enable_dedup : bool
    Whether to run the dedup stage.
enable_segmentation : bool
    Whether to run the segmentizer stage.
enable_directives : bool
    Whether to run the directive-application stage.
trace_id : str
    Optional trace ID forwarded to segmentize().

#### `__init__`

```python
def __init__(self, enable_dedup: bool = True, enable_alias: bool = True, enable_segmentation: bool = True, enable_directives: bool = True, enable_instruction_table: bool = True, instruction_table_path: str | None = None, context_budget_tight: bool = True, trace_id: str = '', alias_min_occurrences: int = 3, alias_min_length: int = 20) -> None
```

- **Returns:** `None`

#### `add_hook`

```python
def add_hook(self, fn: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]) -> None
```

- **Returns:** `None`
- **Description:** Register a custom compression hook (called after built-in stages).

#### `run`

```python
def run(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, *, dry_run: bool = False) -> PipelineResult
```

- **Returns:** `PipelineResult`
- **Description:** Run the full compression pipeline on *messages*.

### `tokenpak.compression.pipeline.PipelineResult`

**Bases:** object

Output of a CompressionPipeline.run() call.

#### `tokens_saved`

```python
def tokens_saved(self) -> int
```

- **Returns:** `int`

#### `savings_pct`

```python
def savings_pct(self) -> float
```

- **Returns:** `float`

### `tokenpak.compression.precompute.PrecomputeStore`

**Bases:** object

Persist and retrieve precomputed artifacts on disk.

Layout: {artifacts_dir}/{artifact_type}/{block_id}.json

#### `__init__`

```python
def __init__(self, artifacts_dir: Optional[Path] = None) -> Any
```

- **Returns:** `Any`

#### `save`

```python
def save(self, artifact: PrecomputedArtifact) -> Path
```

- **Returns:** `Path`
- **Description:** Write artifact to disk. Returns the saved path.

#### `load`

```python
def load(self, artifact_type: str, block_id: str) -> Optional[PrecomputedArtifact]
```

- **Returns:** `Optional[PrecomputedArtifact]`
- **Description:** Load artifact from disk. Returns None if not found.

#### `exists`

```python
def exists(self, artifact_type: str, block_id: str) -> bool
```

- **Returns:** `bool`

#### `delete`

```python
def delete(self, artifact_type: str, block_id: str) -> bool
```

- **Returns:** `bool`

#### `list_block_artifacts`

```python
def list_block_artifacts(self, block_id: str) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Return all artifact types available for a block_id.

### `tokenpak.compression.precompute.PrecomputedArtifact`

**Bases:** object

An intent-ready precomputed artifact.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `from_dict`

```python
def from_dict(cls, d: dict[str, object]) -> 'PrecomputedArtifact'
```

- **Returns:** `'PrecomputedArtifact'`

### `tokenpak.compression.processors.code.CodeProcessor`

**Bases:** object

Extract code structure while dropping implementation details.

#### `process`

```python
def process(self, content: str, path: str = '', mode: CodeCompactionMode = CodeCompactionMode.CODE_API) -> str
```

- **Returns:** `str`
- **Description:** Compress code by extracting structure.

### `tokenpak.compression.processors.code_treesitter.TreeSitterProcessor`

**Bases:** object

Processor that uses tree-sitter to extract code structure.

Drop-in replacement for CodeProcessor for supported languages.
Falls back to CodeProcessor on parse failure or unsupported language.

#### `__init__`

```python
def __init__(self, fallback: _FallbackProcessor | None = None) -> None
```

- **Returns:** `None`

#### `process`

```python
def process(self, content: str, path: str = '') -> str
```

- **Returns:** `str`
- **Description:** Process a code file: extract API surface via tree-sitter, fall back

### `tokenpak.compression.processors.data.DataProcessor`

**Bases:** object

Extract schema and samples from structured data files.

#### `process`

```python
def process(self, content: str, path: str = '') -> str
```

- **Returns:** `str`
- **Description:** Process structured data files into schema + sample.

### `tokenpak.compression.processors.image.ImageProcessor`

**Bases:** object

Processor that wraps :func:`compress` in the tokenpak processor interface.

If Pillow is not installed this class degrades to a **passthrough stub**:
:meth:`process` returns the raw file bytes unchanged and emits a warning.

#### `process`

```python
def process(self, content: bytes, path: str = '') -> bytes
```

- **Returns:** `bytes`
- **Description:** Compress *content* (raw image bytes) using context-aware strategies.

### `tokenpak.compression.processors.text.TextProcessor`

**Bases:** object

Compress text by preserving structure and aggressively reducing verbosity.

#### `__init__`

```python
def __init__(self, aggressive: bool = True) -> Any
```

- **Returns:** `Any`

#### `process`

```python
def process(self, content: str, path: str = '') -> str
```

- **Returns:** `str`
- **Description:** Compress text content while preserving meaning.

### `tokenpak.compression.query_rewriter.QueryRewriter`

**Bases:** object

Pre-process user/system queries for maximum compactness.

Applies a deterministic, rule-based pipeline:

1. Strip greeting / opener phrases
2. Strip trailing pleasantries
3. Remove inline filler words
4. Collapse repeated / near-duplicate sentences
5. Normalise whitespace and capitalisation

All transforms are fully reversible from the original; the original
is always retained in :class:`RewriteResult` for audit/fallback.

Parameters
----------
collapse_threshold : float
    Jaccard similarity above which two sentences in the same query
    are considered duplicates. Default 0.75.
preserve_technical : bool
    When True (default), skip inline-filler stripping on tokens
    that appear inside backticks, angle-bracket tags, or URLs to
    avoid corrupting code / markup.

#### `__init__`

```python
def __init__(self, collapse_threshold: float = 0.7, preserve_technical: bool = True) -> None
```

- **Returns:** `None`

#### `rewrite`

```python
def rewrite(self, text: str) -> RewriteResult
```

- **Returns:** `RewriteResult`
- **Description:** Rewrite a single query string.

#### `rewrite_messages`

```python
def rewrite_messages(self, messages: List[Dict[str, Any]], *, roles: Optional[List[str]] = None) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Rewrite all user (and optionally system) messages in a messages list.

### `tokenpak.compression.recipe_sdk.RecipeSDK`

**Bases:** object

Tooling for developing, testing, and benchmarking custom recipes.

#### `create`

```python
def create(self, name: str, *, output_dir: str | Path = '.', category: str = 'general', description: str = '', match_mode: str = 'extension', ext: str = 'txt', domain_example: str | None = None) -> Path
```

- **Returns:** `Path`
- **Description:** Scaffold a new recipe YAML file.

#### `validate`

```python
def validate(self, recipe_file: str | Path) -> list[str]
```

- **Returns:** `list[str]`
- **Raises:** `RecipeValidationError`
- **Description:** Validate a recipe file against the schema.

#### `test`

```python
def test(self, recipe_file: str | Path, *, input_text: str | None = None, input_file: str | Path | None = None, filename_hint: str = '') -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Test a recipe against sample input.

#### `benchmark`

```python
def benchmark(self, recipe_file: str | Path, *, samples: list[str] | None = None, runs: int = 5) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Benchmark a recipe's compression ratio and speed.

### `tokenpak.compression.recipes.CompressionRecipe`

**Bases:** object

A declarative compression recipe loaded from YAML.

#### `from_dict`

```python
def from_dict(cls, data: dict[str, Any], *, source: str) -> 'CompressionRecipe'
```

- **Returns:** `'CompressionRecipe'`

#### `compression_hint`

```python
def compression_hint(self) -> float
```

- **Returns:** `float`
- **Description:** Expected compression ratio 0.0–1.0 (fraction of content removed).

#### `operations`

```python
def operations(self) -> list[dict[str, object]]
```

- **Returns:** `list[dict[str, object]]`

#### `match_mode`

```python
def match_mode(self) -> str
```

- **Returns:** `str`

#### `matches`

```python
def matches(self, filename: str = '', content_sample: str = '') -> bool
```

- **Returns:** `bool`
- **Description:** Return True if this recipe is applicable to the given file/content.

### `tokenpak.compression.recipes.CompressionRecipeEngine`

**Bases:** object

Loads and indexes OSS compression recipes from YAML files.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `load_from_dir`

```python
def load_from_dir(self, path: str | Path | None = None, *, override_existing: bool = False) -> None
```

- **Returns:** `None`
- **Description:** Load all YAML recipe files from *path* (defaults to bundled OSS dir).

#### `load_defaults`

```python
def load_defaults(self) -> None
```

- **Returns:** `None`
- **Description:** Load packaged OSS recipes plus the optional user overlay directory.

#### `get_recipe`

```python
def get_recipe(self, name: str) -> CompressionRecipe | None
```

- **Returns:** `CompressionRecipe | None`

#### `list_recipes`

```python
def list_recipes(self) -> list[str]
```

- **Returns:** `list[str]`

#### `recipes_for_file`

```python
def recipes_for_file(self, filename: str, content_sample: str = '') -> list[CompressionRecipe]
```

- **Returns:** `list[CompressionRecipe]`
- **Description:** Return recipes applicable to a given file, sorted by compression_hint desc.

#### `by_category`

```python
def by_category(self, category: str) -> list[CompressionRecipe]
```

- **Returns:** `list[CompressionRecipe]`

#### `categories`

```python
def categories(self) -> list[str]
```

- **Returns:** `list[str]`

#### `summary`

```python
def summary(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return a summary dict suitable for CLI display.

### `tokenpak.compression.recipes.CompressionRuleEngine`

**Bases:** object

Deterministic compression rule engine for ContentSegment objects.

Applies text reduction rules in a fixed order:
1. WHITESPACE_COLLAPSE
2. LIST_DEDUP
3. BOILERPLATE_STRIP
4. TRUNCATE_TAIL
5. PHRASE_SUBSTITUTION  (last — ensures phrases not re-introduced)

Usage::

    engine = CompressionRuleEngine()
    recipes = engine.select_recipes(segment)
    compressed = engine.apply_recipes(segment, recipes)

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `select_recipes`

```python
def select_recipes(self, segment: ContentSegment) -> List[RecipeType]
```

- **Returns:** `List[RecipeType]`
- **Description:** Return ordered list of RecipeType that apply to *segment*.

#### `apply_recipes`

```python
def apply_recipes(self, segment: ContentSegment, recipes: List[RecipeType]) -> ContentSegment
```

- **Returns:** `ContentSegment`
- **Description:** Apply *recipes* in order; return new ContentSegment with updated tokens.

### `tokenpak.compression.recipes.ContentSegment`

**Bases:** object

Lightweight segment that carries raw text and its classification.

#### `with_content`

```python
def with_content(self, new_content: str) -> 'ContentSegment'
```

- **Returns:** `'ContentSegment'`
- **Description:** Return a new ContentSegment with updated content (and recounted tokens).

### `tokenpak.compression.recipes.Recipe`

**Bases:** object

#### `from_dict`

```python
def from_dict(cls, data: dict[str, Any], *, source: str) -> 'Recipe'
```

- **Returns:** `'Recipe'`

### `tokenpak.compression.recipes.RecipeEngine`

**Bases:** object

Loads and resolves intent recipes for deterministic context assembly.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `load_recipes`

```python
def load_recipes(self, path: str) -> None
```

- **Returns:** `None`

#### `get_recipe`

```python
def get_recipe(self, intent: str) -> Recipe | None
```

- **Returns:** `Recipe | None`

#### `list_recipes`

```python
def list_recipes(self) -> list[str]
```

- **Returns:** `list[str]`

#### `to_segments`

```python
def to_segments(self, recipe: Recipe, available_blocks: Mapping[str, Any]) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`

### `tokenpak.compression.report.Action`

**Bases:** Enum

#### `icon`

```python
def icon(self) -> str
```

- **Returns:** `str`
- **Description:** Return emoji icon representing this action.

#### `label`

```python
def label(self) -> str
```

- **Returns:** `str`
- **Description:** Return label text representing this action.

### `tokenpak.compression.report.CompileReport`

**Bases:** object

Full report of a single compile() call.

#### `tokens_saved`

```python
def tokens_saved(self) -> int
```

- **Returns:** `int`
- **Description:** Calculate total tokens saved in this compilation.

#### `savings_percent`

```python
def savings_percent(self) -> float
```

- **Returns:** `float`
- **Description:** Calculate percentage of tokens saved.

#### `budget_used_percent`

```python
def budget_used_percent(self) -> float
```

- **Returns:** `float`
- **Description:** Calculate percentage of allocated budget used.

#### `to_text`

```python
def to_text(self) -> str
```

- **Returns:** `str`
- **Description:** Human-readable terminal report.

#### `to_json`

```python
def to_json(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Machine-readable dict. Suitable for json.dumps(), Langfuse metadata, etc.

#### `to_markdown`

```python
def to_markdown(self) -> str
```

- **Returns:** `str`
- **Description:** Markdown-formatted report for documentation or logging.

### `tokenpak.compression.report.Decision`

**Bases:** object

Record of what happened to a single block during compile.

#### `tokens_saved`

```python
def tokens_saved(self) -> int
```

- **Returns:** `int`
- **Description:** Calculate tokens saved by this decision.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert decision to dictionary for serialization.

### `tokenpak.compression.salience.code_extractor.CodeExtractionResult`

**Bases:** object

#### `reduction_pct`

```python
def reduction_pct(self) -> float
```

- **Returns:** `float`

### `tokenpak.compression.salience.code_extractor.CodeExtractor`

**Bases:** object

Extract high-signal sections from source code text.

Parameters
----------
max_fn_body_lines : int
    Maximum lines to include per function body.
include_all_fns : bool
    If True, include all detected functions (not just changed ones).
    Useful for small files.

#### `__init__`

```python
def __init__(self, max_fn_body_lines: int = MAX_FN_BODY_LINES, include_all_fns: bool = False) -> None
```

- **Returns:** `None`

#### `extract`

```python
def extract(self, text: str) -> CodeExtractionResult
```

- **Returns:** `CodeExtractionResult`
- **Description:** Return a :class:`CodeExtractionResult` for *text*.

### `tokenpak.compression.salience.doc_extractor.DocExtractionResult`

**Bases:** object

#### `reduction_pct`

```python
def reduction_pct(self) -> float
```

- **Returns:** `float`

### `tokenpak.compression.salience.doc_extractor.DocExtractor`

**Bases:** object

Extract high-signal content from documentation / markdown text.

Parameters
----------
annotation_context : int
    Lines of context to keep after each TODO/FIXME/NOTE/etc.
include_rst_headings : bool
    Also detect RST-style headings (underline-based).

#### `__init__`

```python
def __init__(self, annotation_context: int = ANNOTATION_CONTEXT_LINES, include_rst_headings: bool = True) -> None
```

- **Returns:** `None`

#### `extract`

```python
def extract(self, text: str) -> DocExtractionResult
```

- **Returns:** `DocExtractionResult`
- **Description:** Return a :class:`DocExtractionResult` for *text*.

### `tokenpak.compression.salience.log_extractor.LogExtractionResult`

**Bases:** object

#### `reduction_pct`

```python
def reduction_pct(self) -> float
```

- **Returns:** `float`

### `tokenpak.compression.salience.log_extractor.LogExtractor`

**Bases:** object

Extract high-signal content from log text.

Parameters
----------
context_lines : int
    Number of lines to keep before/after each error anchor.
max_stack_sigs : int
    Maximum unique stack signatures to include.
include_warnings : bool
    If True, WARN lines are also treated as anchors (lower priority).

#### `__init__`

```python
def __init__(self, context_lines: int = CONTEXT_LINES, max_stack_sigs: int = MAX_STACK_SIGS, include_warnings: bool = False) -> None
```

- **Returns:** `None`

#### `extract`

```python
def extract(self, text: str) -> LogExtractionResult
```

- **Returns:** `LogExtractionResult`
- **Description:** Return a :class:`LogExtractionResult` for *text*.

### `tokenpak.compression.salience.router.SalientResult`

**Bases:** object

Unified output from :func:`extract`.

#### `reduction_pct`

```python
def reduction_pct(self) -> float
```

- **Returns:** `float`

### `tokenpak.compression.schema_extractor.ExtractionResult`

**Bases:** object

Output of :meth:`SchemaExtractor.extract`.

#### `compression_ratio`

```python
def compression_ratio(self) -> float
```

- **Returns:** `float`
- **Description:** Approximate ratio of compact length to original length.

### `tokenpak.compression.schema_extractor.SchemaExtractor`

**Bases:** object

Detect a document type and extract a compact schema from its content.

Parameters
----------
confidence_threshold : float
    Minimum confidence (0–1) required to apply schema substitution.
    Documents below the threshold are passed through unchanged.
templates : dict, optional
    Override or extend the default TEMPLATES mapping.

#### `__init__`

```python
def __init__(self, confidence_threshold: float = CONFIDENCE_THRESHOLD, templates: Optional[Dict[str, List[str]]] = None) -> None
```

- **Returns:** `None`

#### `detect_type`

```python
def detect_type(self, text: str) -> tuple[str, float]
```

- **Returns:** `tuple[str, float]`
- **Description:** Detect the most likely document type for *text*.

#### `extract`

```python
def extract(self, text: str) -> ExtractionResult
```

- **Returns:** `ExtractionResult`
- **Description:** Detect the document type and extract a compact representation.

#### `extract_message`

```python
def extract_message(self, message: Dict[str, Any]) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Apply schema extraction to a single messages-list entry in place.

### `tokenpak.compression.slot_filler.SlotFiller`

**Bases:** object

Extracts slot values from raw text for a given intent.

Slot definitions are loaded from slot_definitions.yaml (co-located with
this module).  All extraction is regex/keyword based — no LLM.

Usage::

    filler = SlotFiller()
    result = filler.fill("summarize", "summarize the vault for last 7 days")

#### `__init__`

```python
def __init__(self, definitions: Optional[dict[str, dict[str, object]]] = None) -> None
```

- **Returns:** `None`

#### `fill`

```python
def fill(self, intent: str, text: str) -> FilledSlots
```

- **Returns:** `FilledSlots`

#### `definitions`

```python
def definitions(self) -> dict[str, dict[str, object]]
```

- **Returns:** `dict[str, dict[str, object]]`

#### `known_intents`

```python
def known_intents(self) -> list[str]
```

- **Returns:** `list[str]`

### `tokenpak.compression.span_extractor.SpanExtractor`

**Bases:** object

Extracts the most relevant sentence spans from a text chunk.

Strategy:
1. Split chunk into sentences
2. Score each sentence against the query
3. Select top-scoring sentences that fit within max_tokens
4. Return extracted span with byte-offset reference

Optional: uses cross-encoder reranker if sentence-transformers is
installed and use_reranker=True.

#### `__init__`

```python
def __init__(self, reranker_model: str = 'cross-encoder/ms-marco-MiniLM-L-6-v2', use_reranker: bool = False) -> Any
```

- **Returns:** `Any`

#### `extract_span`

```python
def extract_span(self, chunk_text: str, query: str, max_tokens: int = 50) -> SpanResult
```

- **Returns:** `SpanResult`
- **Description:** Extract the most relevant span from a chunk.

#### `extract_spans_batch`

```python
def extract_spans_batch(self, chunks: list[dict[str, object]], query: str, max_tokens_each: int = 50) -> list[BatchSpanResult]
```

- **Returns:** `list[BatchSpanResult]`
- **Description:** Extract spans from multiple chunks.

### `tokenpak.compression.teacher.builder.TeacherPackBuilder`

**Bases:** object

#### `__init__`

```python
def __init__(self, source_roots: list[Path], command_roots: list[Path], output_root: Path, version: str = 'v1', default_budget: int = 1600) -> None
```

- **Returns:** `None`

#### `build`

```python
def build(self) -> TeacherPackResult
```

- **Returns:** `TeacherPackResult`

### `tokenpak.config.load_order.LoadOrderResolver`

**Bases:** object

Resolve env keys through the canonical precedence (pure helper).

Every input is injectable so the resolver is fully testable without
touching the real environment, real home, or the network:

- ``environ``      — process-env mapping (defaults to ``os.environ``).
- ``cwd``          — directory whose ``./.env`` is the project dotenv.
- ``home``         — ``<tpk-home>`` (defaults to ``_paths.home()``).
- ``legacy_home``  — ``<legacy-home>`` (defaults to ``_paths.legacy_home()``).
- ``config_lookup``— callable ``key -> Optional[str]`` for config-file
                     layers (6/7). Defaults to "no config layer" so this
                     module never imports the runtime loaders.
- ``cli_flags``    — mapping for layer 1 (CLI-bound values).
- ``openclaw_fallback`` — when True, layer 5 (legacy ``.env``) is consulted.
                     Defaults to honoring ``$TOKENPAK_OPENCLAW_FALLBACK`` in
                     ``environ`` (off unless explicitly truthy). Layer 5 is
                     HELD: it is never consulted with the default-off flag.

The resolver does not write anything and creates no directories.

#### `resolve`

```python
def resolve(self, key: str, default: Optional[str] = None) -> Resolution
```

- **Returns:** `Resolution`
- **Description:** Resolve ``key`` to a :class:`Resolution` (first layer that hits).

#### `get`

```python
def get(self, key: str, default: Optional[str] = None) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Convenience: return only the resolved value (or *default*).

#### `provenance`

```python
def provenance(self, keys: Iterable[str]) -> dict[str, Resolution]
```

- **Returns:** `dict[str, Resolution]`
- **Description:** Resolve many keys, returning ``{key: Resolution}`` (for `config env`).

### `tokenpak.core.auth.oauth_manager.BackgroundOAuthRefresher`

**Bases:** object

Asyncio background task that checks and refreshes OAuth tokens every N seconds.

Runs inside the proxy event loop.
Default interval: 5 minutes (300s).

#### `__init__`

```python
def __init__(self, interval: int = DEFAULT_INTERVAL, manager: Optional[OAuthManager] = None, enabled: bool = True) -> Any
```

- **Returns:** `Any`

#### `start`

```python
async def start(self) -> None
```

- **Returns:** `None`
- **Description:** Start the background task (idempotent).

#### `stop`

```python
async def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Signal the background task to stop and wait for it.

### `tokenpak.core.auth.oauth_manager.OAuthManager`

**Bases:** object

Check OAuth token expiry and refresh tokens proactively.

Reads/writes ~/.tokenpak/auth-profiles.json.
SECURITY: Never logs token values. Only logs metadata.

#### `__init__`

```python
def __init__(self, auth_profiles_file: Optional[Path] = None, refresh_window: int = REFRESH_WINDOW_SECONDS) -> Any
```

- **Returns:** `Any`

#### `get_expiring_profiles`

```python
def get_expiring_profiles(self) -> List[tuple[str, Dict[str, Any], float]]
```

- **Returns:** `List[tuple[str, Dict[str, Any], float]]`
- **Description:** Return list of (name, profile, seconds_remaining) for expiring OAuth tokens.

#### `refresh_profile`

```python
async def refresh_profile(self, profile_name: str, profile: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Attempt to refresh a single profile. Returns True on success.

#### `run_cycle`

```python
async def run_cycle(self) -> Dict[str, bool]
```

- **Returns:** `Dict[str, bool]`
- **Description:** Check all profiles and refresh expiring ones. Returns {name: success}.

### `tokenpak.core.config_validator.ConfigValidationError`

**Bases:** object

Represents a single config validation error.

#### `__init__`

```python
def __init__(self, field: str, expected: str, actual: Any, message: str, suggestion: str) -> Any
```

- **Returns:** `Any`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.core.config_validator.ConfigValidator`

**Bases:** object

Validates TokenPak proxy configuration.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `validate`

```python
def validate(self, config: Dict[str, Any]) -> List[ConfigValidationError]
```

- **Returns:** `List[ConfigValidationError]`
- **Description:** Validate config dict. Returns list of errors (empty if valid).

#### `is_valid`

```python
def is_valid(self, config: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Check if config is valid (no errors).

#### `validate_file`

```python
def validate_file(self, filepath: str) -> bool
```

- **Returns:** `bool`
- **Description:** Load and validate config file. Returns True if valid.

### `tokenpak.core.contracts.cache.CachePolicy`

**Bases:** object

Per-request cache behavior contract.

The proxy optimization pipeline reads this to decide whether to attempt
semantic cache lookup, what scope to use, and whether response reuse
(vs. context reuse only) is permitted.

Safe defaults:
- ``enabled=True`` — cache is on by default (miss is harmless)
- ``semantic_enabled=False`` — semantic matching is off until explicitly
  enabled per route class; avoids wrong-response risk for code tasks
- ``scope="session"`` — isolates cache entries per user session
- ``allow_response_reuse=False`` — context reuse only; response reuse
  requires explicit route-class allowance
- ``allow_context_reuse=True`` — repeated retrieved context/capsules
  can be served from cache without re-fetching

#### `is_active`

```python
def is_active(self) -> bool
```

- **Returns:** `bool`
- **Description:** True when caching is enabled and not explicitly bypassed.

#### `with_bypass`

```python
def with_bypass(self, reason: str) -> 'CachePolicy'
```

- **Returns:** `'CachePolicy'`
- **Description:** Return a copy of this policy with caching bypassed for *reason*.

### `tokenpak.core.contracts.compression.CompressionPolicy`

**Bases:** object

Per-request compression behavior contract.

Recipe selection and fidelity constraints are expressed here so the
proxy stage can apply route-appropriate compression without knowing
adapter specifics.

Fields:
- ``recipe_ids``: ordered list of compression recipe identifiers to
  apply (e.g. ``["cp-git-diff-compression"]``). Empty = no compression.
- ``target_ratio``: desired compression ratio (0.0–1.0). ``None`` means
  "apply recipe defaults".
- ``protected_span_types``: content categories that MUST be preserved
  verbatim. Populated from ``ProtectedSpanType`` constants.
- ``preserve_exact_blocks``: when True, code fences (``` blocks) and
  quoted exact outputs are treated as lossless zones.
- ``bypass_reason``: if set, compression is skipped with this reason.

#### `is_active`

```python
def is_active(self) -> bool
```

- **Returns:** `bool`
- **Description:** True when compression is enabled with at least one recipe and not bypassed.

#### `with_bypass`

```python
def with_bypass(self, reason: str) -> 'CompressionPolicy'
```

- **Returns:** `'CompressionPolicy'`
- **Description:** Return a copy of this policy with compression bypassed for *reason*.

### `tokenpak.core.contracts.context.ContextPackage`

**Bases:** object

A bundle of context delivered to the current AI session.

Frozen — packages are immutable artifacts; resuming or re-targeting
builds a new package referencing the same Paks.

``recall_query`` records the natural-language intent the package was
built for (used for audit + telemetry; never sent on the license-
validation egress path).

``memory_horizon`` is the time-scope the recall searched
(``recent`` / ``historical`` / ``project_lifetime``); informational only.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Render to JSON-serializable form matching the wire schema.

#### `from_dict`

```python
def from_dict(cls, data: Mapping[str, Any]) -> 'ContextPackage'
```

- **Returns:** `'ContextPackage'`
- **Description:** Parse a ContextPackage from its wire form.

#### `is_empty`

```python
def is_empty(self) -> bool
```

- **Returns:** `bool`
- **Description:** True for level-0 / no_memory packages.

#### `has_complete_coverage`

```python
def has_complete_coverage(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.core.contracts.context.OrderingHints`

**Bases:** object

Optional cache-aware ordering preferences for a Context Package.

Additive within TIP-1.x — receivers that don't recognise the field
ignore it and produce a valid (just unoptimised) package.
The Pro Phase 3 Context Package builder is the authoritative consumer;
OSS persists / inspects / exports / validates the field transparently
(OSS = data plane, Pro = enforcement).

Attributes:
    stable_first: Place stable/reusable Pak content before volatile
        task delta. Cache-aware.
    task_delta_after_stable_context: Mandatory and primary Paks appear
        after stable/reusable Paks.
    output_requirements_near_end: Output-shape instructions immediately
        precede the cursor.
    cache_sensitive_blocks: Block IDs whose ordering must not change
        once assembled (cache stability).
    anchor_block_position: Where hydrated anchor snippets sit (see
        :class:`AnchorBlockPosition`). Default ``END``.

The supported hint fields are catalogued in the public registry.

#### `to_wire`

```python
def to_wire(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Wire-form mapping for embedding in a Context Package payload.

#### `from_wire`

```python
def from_wire(cls, data: Mapping[str, Any]) -> 'OrderingHints'
```

- **Returns:** `'OrderingHints'`
- **Description:** Parse from a wire-form mapping.

### `tokenpak.core.contracts.fidelity.FidelityPolicy`

**Bases:** str, Enum

Content preservation requirements for optimization stages.

Inherits from ``str`` for JSON/YAML compatibility.

Enforcement contract (proxy/optimization/):
- Stages MUST check the active ``FidelityPolicy`` before mutating content.
- A stage that cannot satisfy the required policy MUST skip and emit a
  ``StageTrace`` with ``skip_reason`` explaining the bypass.
- Policy is determined per-request from the ``OptimizationRouteClass``
  and any adapter-supplied overrides.

#### `allows_response_reuse`

```python
def allows_response_reuse(self) -> bool
```

- **Returns:** `bool`

#### `allows_compression`

```python
def allows_compression(self) -> bool
```

- **Returns:** `bool`

#### `requires_protected_span_check`

```python
def requires_protected_span_check(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.core.contracts.measured.Measured`

**Bases:** object

A quantity that knows whether it was actually measured.

Prefer the module-level constructors (:func:`measured`, :func:`no_data`,
:func:`unavailable`, :func:`error`) over calling this directly.

#### `is_measured`

```python
def is_measured(self) -> bool
```

- **Returns:** `bool`

#### `render`

```python
def render(self, fmt: str = 'auto', placeholder: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Render for a terminal surface.

#### `explain`

```python
def explain(self) -> str
```

- **Returns:** `str`
- **Description:** Render with the reason appended, for diagnostic surfaces.

#### `to_json`

```python
def to_json(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Machine-readable form. ``value`` is ``null`` unless measured.

### `tokenpak.core.contracts.optimization.OptimizationContract`

**Bases:** object

Per-request optimization specification.

Fields:
- ``request_id``: unique identifier for this request (for trace correlation).
- ``adapter_format``: the format adapter's ``source_format`` string.
- ``platform``: platform identifier from platform adapter, or None.
- ``model``: model identifier as seen in the canonical request.
- ``capabilities``: frozenset of TIP capability labels declared by
  the format adapter (from ``FormatAdapter.capabilities``).
- ``route_class``: semantic request type (from content classification).
- ``fidelity_policy``: content preservation constraint for this request.
- ``cache_policy``: cache behavior contract.
- ``compression_policy``: compression behavior contract.
- ``telemetry_policy``: telemetry and attribution contract.
- ``safety_flags``: list of safety signal identifiers that constrain
  what the pipeline may do (e.g. ``["dlp_redaction_required"]``).

Invariants enforced at construction:
- ``route_class`` must be a valid ``OptimizationRouteClass`` value.
- ``fidelity_policy`` must be a valid ``FidelityPolicy`` value.
- Adapters that do not declare ``TIP_CACHE_PROXY_MANAGED`` will have
  ``cache_policy.enabled`` forced to False by the contract builder.
  (This enforcement happens in proxy/optimization/contract_builder.py,
  not here — the contract itself is a value object.)

#### `has_capability`

```python
def has_capability(self, capability: str) -> bool
```

- **Returns:** `bool`
- **Description:** True when the adapter declared this TIP capability label.

#### `is_optimization_eligible`

```python
def is_optimization_eligible(self) -> bool
```

- **Returns:** `bool`
- **Description:** True when at least one optimization stage could run for this request.

#### `effective_route_class_str`

```python
def effective_route_class_str(self) -> str
```

- **Returns:** `str`

#### `effective_fidelity_str`

```python
def effective_fidelity_str(self) -> str
```

- **Returns:** `str`

### `tokenpak.core.contracts.pak.Pak`

**Bases:** object

A Portable AI Knowledge bundle.

Frozen by design — Paks are immutable once captured; mutations create a
new Pak with ``supersedes`` pointing at the predecessor. This matches
the recall ranking model where ``conflict_penalty`` and
``stale_penalty`` apply to Paks superseded by newer revisions.

See ``pak-v1.json`` (registry) for the JSON Schema canonical form.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Render to a JSON-serializable dict matching the wire schema.

#### `from_dict`

```python
def from_dict(cls, data: Mapping[str, Any]) -> 'Pak'
```

- **Returns:** `'Pak'`
- **Description:** Parse a Pak from its wire form. Unknown enum values raise ``ValueError``;

### `tokenpak.core.contracts.pak.PakSubtype`

**Bases:** str, Enum

Canonical Pak subtype taxonomy.

The 5 values are the canonical taxonomy. Receivers parsing a
Pak with an unknown subtype string MUST fall back gracefully (per the
capability-codes rule); never raise on an unrecognized value.
Use :func:`PakSubtype.parse` to normalize legacy/aliased values.

#### `parse`

```python
def parse(cls, value: str) -> 'PakSubtype'
```

- **Returns:** `'PakSubtype'`
- **Description:** Parse a subtype string, resolving deprecated aliases with a warning.

### `tokenpak.core.contracts.route.OptimizationRouteClass`

**Bases:** str, Enum

Semantic taxonomy of LLM request content types.

Inherits from ``str`` so values are naturally JSON-serialisable and
usable as YAML/config keys without conversion.

Policy implications (implemented in proxy/optimization/):
- Cache eligibility varies by class (status_check: response-reusable;
  code_edit: context-reusable only).
- Compression recipe selection is class-driven (debugging → exception
  recipes; git_diff_review → diff recipes).
- Fidelity defaults differ (code_edit → lossless_required;
  summarization → aggressive_ok).

#### `is_code_task`

```python
def is_code_task(self) -> bool
```

- **Returns:** `bool`
- **Description:** True for classes where code integrity is paramount.

#### `allows_response_reuse_by_default`

```python
def allows_response_reuse_by_default(self) -> bool
```

- **Returns:** `bool`
- **Description:** True for classes where response reuse is safe with conservative thresholds.

### `tokenpak.core.contracts.telemetry.TelemetryPolicy`

**Bases:** object

Per-request telemetry and attribution behavior contract.

Fields:
- ``enabled``: master switch; when False, no telemetry is emitted.
- ``hash_prompts``: when True (default), prompt content is hashed
  before storage. Raw prompt storage requires explicit opt-in.
- ``attribute_savings``: when True, per-source savings are computed
  and stored in ``savings_attribution`` telemetry table.
- ``emit_trace``: when True, full ``OptimizationTrace`` is persisted
  (may be large; gated separately from lightweight savings records).
- ``session_id``: opaque session identifier for scoping telemetry rows.
  None means telemetry is unscoped (allowed but limits recommendations).

#### `is_active`

```python
def is_active(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.core.contracts.trace.CompressionTrace`

**Bases:** object

Compression behavior summary for one request.

#### `compression_ratio`

```python
def compression_ratio(self) -> Optional[float]
```

- **Returns:** `Optional[float]`

### `tokenpak.core.contracts.trace.OptimizationTrace`

**Bases:** object

Top-level per-request optimization trace.

Produced by every request through the optimization pipeline (Component B).
Stored in the ``optimization_traces`` telemetry table.

#### `tokenpak_saved_tokens`

```python
def tokenpak_saved_tokens(self) -> int
```

- **Returns:** `int`
- **Description:** Total tokens saved by TokenPak-managed stages only.

#### `add_stage`

```python
def add_stage(self, stage: StageTrace) -> None
```

- **Returns:** `None`

#### `add_savings`

```python
def add_savings(self, attribution: SavingsAttribution) -> None
```

- **Returns:** `None`

### `tokenpak.core.contracts.trace.StageTrace`

**Bases:** object

Record of one optimization stage's decision for a request.

Fields:
- ``name``: stage identifier (e.g. ``"semantic_cache"``, ``"compression"``).
- ``applied``: True if the stage mutated the request or served from cache.
- ``skip_reason``: populated when ``applied=False`` (required if skipped).
- ``tokens_before``: input token count before this stage (if measurable).
- ``tokens_after``: output token count after this stage (if measurable).
- ``latency_ms``: wall-clock time for this stage in milliseconds.
- ``metadata``: stage-specific detail (cache hit score, recipe used, etc.).

#### `tokens_saved`

```python
def tokens_saved(self) -> Optional[int]
```

- **Returns:** `Optional[int]`

### `tokenpak.core.cooldown.BackgroundCooldownClearer`

**Bases:** object

Asyncio background task that auto-clears expired cooldowns every N seconds.

Runs inside the proxy event loop (no extra threads needed).

Config key: auth.auto_clear_cooldowns (bool, default True)
Backoff: skips clear if any key has errorCount >= HIGH_ERROR_THRESHOLD.

#### `__init__`

```python
def __init__(self, interval: int = 60, manager: CooldownManager | None = None, enabled: bool = True) -> None
```

- **Returns:** `None`

#### `start`

```python
async def start(self) -> None
```

- **Returns:** `None`
- **Description:** Start the background task (idempotent).

#### `stop`

```python
async def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Signal the background task to stop and wait for it.

### `tokenpak.core.cooldown.CooldownManager`

**Bases:** object

Load, inspect, and clear expired auth cooldowns from disk.

Cooldown entry format (cooldowns.json):
{
    "anthropic:default": {"cooldownUntil": 1709000000, "errorCount": 3},
    ...
}
Entry is cleared when: cooldownUntil < now AND errorCount < HIGH_ERROR_THRESHOLD

#### `__init__`

```python
def __init__(self, cooldowns_file: Optional[Path] = None, auth_profiles_file: Optional[Path] = None) -> None
```

- **Returns:** `None`

#### `clear_expired`

```python
def clear_expired(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Clear cooldowns where cooldownUntil < now (and errorCount is low).

#### `clear_expired_from_profiles`

```python
def clear_expired_from_profiles(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Clear cooldownUntil fields from auth-profiles.json when expired.

#### `get_active_cooldowns`

```python
def get_active_cooldowns(self) -> dict[str, float]
```

- **Returns:** `dict[str, float]`
- **Description:** Return map of profile key → seconds remaining for active cooldowns.

#### `run_cycle`

```python
def run_cycle(self) -> int
```

- **Returns:** `int`
- **Description:** Run one clear cycle across both sources. Returns count of cleared entries.

### `tokenpak.core.debug.DebugLogger`

**Bases:** object

Write JSONL debug records for each request when debug mode is active.

#### `__init__`

```python
def __init__(self, log_path: Optional[Path] = None) -> None
```

- **Returns:** `None`

#### `record`

```python
def record(self) -> Iterator[_DebugRecord]
```

- **Returns:** `Iterator[_DebugRecord]`
- **Description:** Context manager: yields a _DebugRecord; appends to log on exit.

### `tokenpak.core.debug.DebugState`

**Bases:** object

Manage debug mode state persisted to disk.

Schema:
    {
        "enabled": bool,
        "requests_remaining": int | null   # null = unlimited
    }

#### `__init__`

```python
def __init__(self, path: Optional[Path] = None) -> None
```

- **Returns:** `None`

#### `enable`

```python
def enable(self, requests: Optional[int] = None) -> None
```

- **Returns:** `None`
- **Description:** Enable debug mode. If *requests* is given, auto-disable after N requests.

#### `disable`

```python
def disable(self) -> None
```

- **Returns:** `None`
- **Description:** Disable debug mode.

#### `is_enabled`

```python
def is_enabled(self) -> bool
```

- **Returns:** `bool`

#### `requests_remaining`

```python
def requests_remaining(self) -> Optional[int]
```

- **Returns:** `Optional[int]`
- **Description:** Return remaining request count, or None if unlimited.

#### `decrement`

```python
def decrement(self) -> None
```

- **Returns:** `None`
- **Description:** Decrement the request counter; auto-disable when it hits zero.

#### `status`

```python
def status(self) -> dict[str, bool | int | str | None]
```

- **Returns:** `dict[str, bool | int | str | None]`
- **Description:** Return a dict suitable for display.

### `tokenpak.core.error_handling.CLIError`

**Bases:** TokenPakError

CLI-specific error. (TP-E602)

#### `__init__`

```python
def __init__(self, message: str, suggestion: Optional[str] = None, context: Optional[str] = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.CacheCorruptedError`

**Bases:** CacheError

Cache data is corrupted. (TP-E402)

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.CircuitOpenError`

**Bases:** ProxyError

Circuit breaker is open — requests blocked until cooldown expires.

#### `__init__`

```python
def __init__(self, provider: str, retry_after: float | None = None, detail: object = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.ConfigError`

**Bases:** TokenPakError

Configuration is invalid or could not be loaded. (TP-E001)

#### `__init__`

```python
def __init__(self, message: str, config_path: str | None = None, detail: object = None, suggestion: Optional[str] = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.ConfigValidationError`

**Bases:** ConfigError

Config validation failed. (TP-E002)

#### `__init__`

```python
def __init__(self, field: str, reason: str, suggestion: Optional[str] = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.InvalidAPIKeyError`

**Bases:** AuthError

API key is invalid or expired. (TP-E202)

#### `__init__`

```python
def __init__(self, provider: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.InvalidConfigFileError`

**Bases:** ConfigError

Config file is invalid JSON or doesn't exist. (TP-E004)

#### `__init__`

```python
def __init__(self, filepath: str, reason: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.LicenseError`

**Bases:** TokenPakError

License is invalid, expired, or insufficient. (TP-E700)

#### `__init__`

```python
def __init__(self, message: str, required_tier: str | None = None, current_tier: str | None = None, detail: object = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.MissingAPIKeyError`

**Bases:** AuthError

API key is missing. (TP-E203)

#### `__init__`

```python
def __init__(self, provider: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.MissingConfigError`

**Bases:** ConfigError

Required config field is missing. (TP-E003)

#### `__init__`

```python
def __init__(self, field: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.MissingDependencyError`

**Bases:** ProxyStartupError

Required dependency not installed.

#### `__init__`

```python
def __init__(self, dependency: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.PermissionDeniedError`

**Bases:** ProxyStartupError

Insufficient permissions for proxy operation.

#### `__init__`

```python
def __init__(self, message: str = 'Permission denied') -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.PortInUseError`

**Bases:** ProxyStartupError

Proxy port is already in use. (TP-E100)

#### `__init__`

```python
def __init__(self, port: int) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.ProviderConnectionError`

**Bases:** NetworkConnectionError

Failed to connect to provider. (TP-E102)

#### `__init__`

```python
def __init__(self, provider: str, reason: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.ProviderError`

**Bases:** TokenPakError

Upstream provider error. (TP-E501)

#### `__init__`

```python
def __init__(self, provider: str, status_code: int, reason: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.ProviderUnknownError`

**Bases:** TokenPakError

Provider name is not recognized by TokenPak. (TP-E502)

#### `__init__`

```python
def __init__(self, provider: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.ProxyStartupError`

**Bases:** ProxyError

Error during proxy server startup. (TP-E100)

#### `__init__`

```python
def __init__(self, message: str, suggestion: Optional[str] = None, context: Optional[str] = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.RateLimitError`

**Bases:** TokenPakError

Rate limit exceeded. (TP-E301)

#### `__init__`

```python
def __init__(self, message: str, retry_after: float | None = None, provider: str | None = None, detail: object = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.RequestTimeoutError`

**Bases:** NetworkConnectionError

Request or connection timed out. (TP-E103)

#### `__init__`

```python
def __init__(self, service: str, timeout_seconds: int) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.SpendGuardBlocked`

**Bases:** ProxyError

Request held by TIP Spend Guard before provider send. (TP-ESG01)

Recoverable — the caller can release with Yes/No or ``[TIP: allow=once]``.
See standards/29-spend-guard-agent-contract.md for the structured-error
contract agents must honor.

#### `__init__`

```python
def __init__(self, message: str = 'TIP Spend Guard blocked this request before provider send.', *, pending_id: str | None = None, projected_cost_usd: float | None = None, projected_tokens: int | None = None, threshold_hit: str | None = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.SpendGuardHardBlocked`

**Bases:** ProxyError

Hard-block ceiling exceeded — cannot be released. (TP-ESG02)

#### `__init__`

```python
def __init__(self, message: str = 'TIP Spend Guard hard-blocked this request.', *, projected_cost_usd: float | None = None, projected_tokens: int | None = None, threshold_hit: str | None = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.TokenPakError`

**Bases:** Exception

Base class for all TokenPak exceptions.

Attributes:
    message: Human-readable error description.
    detail: Optional machine-readable detail (dict or str).
    error_type: Short identifier for the error type (defaults to class name).

#### `__init__`

```python
def __init__(self, message: str, detail: object = None, error_type: str | None = None) -> None
```

- **Returns:** `None`

#### `code`

```python
def code(self) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Structured error code (TP-Exxx), derived from class error_code.

#### `suggestion`

```python
def suggestion(self) -> str
```

- **Returns:** `str`
- **Description:** User-facing suggestion for resolving the error.

#### `context`

```python
def context(self) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Optional context string for debugging.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return structured error response dict for API responses.

### `tokenpak.core.error_handling.TokenPakNotImplementedError`

**Bases:** InternalError

Feature is not yet implemented. (TP-E602)

#### `__init__`

```python
def __init__(self, feature: str) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.UnknownCommandError`

**Bases:** CLIError

Unknown CLI command.

#### `__init__`

```python
def __init__(self, command: str, suggestion: Optional[str] = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.error_handling.UpstreamError`

**Bases:** ProxyError

Upstream provider returned an error response.

#### `__init__`

```python
def __init__(self, message: str, status_code: int | None = None, provider: str | None = None, detail: object = None) -> None
```

- **Returns:** `None`

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.core.error_handling.ValidationError`

**Bases:** TokenPakError

Input validation failed. (TP-E601)

#### `__init__`

```python
def __init__(self, message: str, field: str | None = None, detail: object = None, suggestion: Optional[str] = None) -> None
```

- **Returns:** `None`

### `tokenpak.core.registry.__init__.BlockRegistry`

**Bases:** object

SQLite-backed registry with connection pooling and batch transactions.

Optimizations:
- Connection pooling (reuse instead of open/close per operation)
- WAL mode for better concurrent read/write
- Batch transaction context manager
- Busy timeout for lock contention
- Prepared statement caching (SQLite handles this)

Stability:
- Thread-local connections
- Graceful cleanup on exit
- Error recovery in transactions

#### `__init__`

```python
def __init__(self, db_path: Optional[str] = None) -> Any
```

- **Returns:** `Any`

#### `batch_transaction`

```python
def batch_transaction(self) -> Generator[sqlite3.Connection, None, None]
```

- **Returns:** `Generator[sqlite3.Connection, None, None]`
- **Description:** Context manager for batched writes.

#### `has_changed`

```python
def has_changed(self, path: str, content: str) -> bool
```

- **Returns:** `bool`
- **Description:** Check if file content has changed since last processing.

#### `add_block`

```python
def add_block(self, block: Block) -> Block
```

- **Returns:** `Block`
- **Description:** Add or update a block (auto-commit per call).

#### `add_block_batch`

```python
def add_block_batch(self, block: Block, conn: sqlite3.Connection) -> Block
```

- **Returns:** `Block`
- **Description:** Add or update a block within a batch transaction (no auto-commit).

#### `get_block`

```python
def get_block(self, path: str) -> Optional[Block]
```

- **Returns:** `Optional[Block]`
- **Description:** Retrieve a block by path.

#### `list_blocks`

```python
def list_blocks(self, file_type: Optional[str] = None) -> List[Block]
```

- **Returns:** `List[Block]`
- **Description:** List all blocks, optionally filtered by type.

#### `search`

```python
def search(self, query: str, top_k: int = 10) -> List[Block]
```

- **Returns:** `List[Block]`
- **Description:** Simple keyword search across compressed content.

#### `get_stats`

```python
def get_stats(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Get registry statistics.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Clear all blocks.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`
- **Description:** Close the connection pool.

### `tokenpak.core.registry.claude_code.adapter.ClaudeCodeAdapter`

**Bases:** object

Registry adapter encapsulating Claude Code pass-through integration.

Wraps the byte-level pass-through logic from proxy.py with configuration,
environment-variable building, and platform identification for telemetry.

The adapter registers itself under the name ``"claude-code"`` in the
TokenPak extensions registry when :func:`register` is called.

#### `__init__`

```python
def __init__(self, config: Optional[ClaudeCodeConfig] = None) -> None
```

- **Returns:** `None`
- **Description:** Initialise the adapter.

#### `handle_request`

```python
def handle_request(self, request: ProxyRequest, model: Optional[str] = None) -> ProxyResponse
```

- **Returns:** `ProxyResponse`
- **Description:** Forward *request* through the proxy using the Claude Code route.

#### `build_env`

```python
def build_env(self) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`
- **Description:** Build the environment variables required to point Claude Code at the proxy.

#### `platform_info`

```python
def platform_info(self) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`
- **Description:** Read-only platform identification dict for telemetry.

### `tokenpak.core.registry.claude_code.config.ClaudeCodeConfig`

**Bases:** object

Configuration for the Claude Code pass-through adapter.

Attributes:
    proxy_host: Host where the TokenPak proxy is listening.
    proxy_port: Port where the TokenPak proxy is listening.
    inject_budget: Max characters to inject from vault context per request.
    min_query_tokens: Minimum token count below which vault injection is skipped.
    enable_tool_search: Whether to enable MCP tool search (ENABLE_TOOL_SEARCH).

#### `proxy_url`

```python
def proxy_url(self) -> str
```

- **Returns:** `str`
- **Description:** Base URL of the TokenPak proxy.

### `tokenpak.core.runtime.cache_telemetry.CacheTelemetry`

**Bases:** object

Thread-safe per-provider cache hit/miss/mode telemetry collector.

Usage::

    telemetry = CacheTelemetry()

    # After processing a response:
    read_tok, create_tok = CacheTelemetry.extract_anthropic_signals(response_json)
    telemetry.record(
        provider="anthropic",
        mode="block_explicit",
        cache_read_tokens=read_tok,
        cache_creation_tokens=create_tok,
        savings_usd=0.001,
    )

    # In /status handler:
    stats_dict["cache_telemetry"] = telemetry.to_dict()

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `record`

```python
def record(self, provider: str, mode: str | None, cache_read_tokens: int, cache_creation_tokens: int, savings_usd: float = 0.0) -> None
```

- **Returns:** `None`
- **Description:** Record a cache result for a provider.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return current telemetry state as a JSON-serializable dict.

#### `reset`

```python
def reset(self) -> None
```

- **Returns:** `None`
- **Description:** Reset all counters (for testing or manual resets).

#### `extract_anthropic_signals`

```python
def extract_anthropic_signals(response_body: Mapping[str, object]) -> tuple[int, int]
```

- **Returns:** `tuple[int, int]`
- **Description:** Extract (cache_read_tokens, cache_creation_tokens) from Anthropic response.

#### `extract_openai_signals`

```python
def extract_openai_signals(response_body: Mapping[str, object]) -> tuple[int, int]
```

- **Returns:** `tuple[int, int]`
- **Description:** Extract (cache_read_tokens, 0) from OpenAI response.

#### `extract_gemini_signals`

```python
def extract_gemini_signals(response_body: Mapping[str, object]) -> tuple[int, int]
```

- **Returns:** `tuple[int, int]`
- **Description:** Extract (cache_read_tokens, 0) from Gemini response.

#### `extract_bedrock_signals`

```python
def extract_bedrock_signals(response_body: Mapping[str, object]) -> tuple[int, int]
```

- **Returns:** `tuple[int, int]`
- **Description:** Extract (cache_read_tokens, cache_creation_tokens) from Bedrock response.

#### `extract_signals_from_headers`

```python
def extract_signals_from_headers(headers: Mapping[str, object]) -> tuple[int, int]
```

- **Returns:** `tuple[int, int]`
- **Description:** Extract Anthropic cache signals from HTTP response headers.

### `tokenpak.core.runtime.cache_telemetry.ProviderCacheStats`

**Bases:** object

Accumulated cache stats for a single provider.

#### `total`

```python
def total(self) -> int
```

- **Returns:** `int`

#### `hit_rate`

```python
def hit_rate(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.core.runtime.lifecycle.LifecycleSnapshot`

**Bases:** object

Observed lifecycle state. Every field is an observation, not a guess.

#### `running`

```python
def running(self) -> bool
```

- **Returns:** `bool`
- **Description:** True only when a live PID *and* a healthy endpoint agree.

#### `foreign_listener`

```python
def foreign_listener(self) -> bool
```

- **Returns:** `bool`
- **Description:** Something else owns our port.

#### `to_json`

```python
def to_json(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.core.runtime.proxy.Monitor`

**Bases:** _BaseMonitor

Monitor with schema additions (session_id column + mutation_audit table)
and session_id support in log().

#### `log`

```python
def log(self, model: object, input_tokens: object, output_tokens: object, cost: object, latency_ms: object, status_code: object, endpoint: object, compilation_mode: object = '', protected_tokens: object = 0, compressed_tokens: object = 0, injected_tokens: object = 0, injected_sources: object = '', cache_read_tokens: object = 0, cache_creation_tokens: object = 0, would_have_saved: object = 0, session_id: object = '', stable_hash: object = '', volatile_hash: object = '', cache_origin: object = 'unknown', user_id: object = '', cache_creation_ephemeral_1h_tokens: object = 0, cache_creation_ephemeral_5m_tokens: object = 0, ttl_attribution: object | None = None, agent_id: object = '', cycle_id: object = '', attribution_source: object = '', stop_reason: object = '') -> None
```

- **Returns:** `None`
- **Description:** Log a request; extends parent with session_id and fingerprints.

### `tokenpak.core.schemas.artifact.ArtifactSchema`

**Bases:** object

Artifact schema: large content you don't want to resend.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert to dict for serialization.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> ArtifactSchema
```

- **Returns:** `ArtifactSchema`
- **Description:** Create from dict.

### `tokenpak.core.schemas.chunk.ChunkSchema`

**Bases:** object

Chunk schema: searchable retrieval unit.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert to dict for serialization.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> ChunkSchema
```

- **Returns:** `ChunkSchema`
- **Description:** Create from dict.

### `tokenpak.core.schemas.retrieval_cache.RetrievalCacheSchema`

**Bases:** object

Retrieval cache entry with TTL and coverage tracking.

#### `is_expired`

```python
def is_expired(self) -> bool
```

- **Returns:** `bool`
- **Description:** Check if cache entry has expired.

#### `touch`

```python
def touch(self) -> None
```

- **Returns:** `None`
- **Description:** Update last_used_at and increment use_count.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert to dict for serialization.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> RetrievalCacheSchema
```

- **Returns:** `RetrievalCacheSchema`
- **Description:** Create from dict.

### `tokenpak.core.schemas.source_map.SourceMapSchema`

**Bases:** object

Source map: truth preference and conflict resolution.

#### `resolve`

```python
def resolve(self, path: str, artifact_id: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Resolve where truth should come from.

#### `bind_artifact`

```python
def bind_artifact(self, path: str, artifact_id: str) -> None
```

- **Returns:** `None`
- **Description:** Mark artifact as written back to repo path.

#### `record_conflict`

```python
def record_conflict(self, path: str, artifact_id: str) -> None
```

- **Returns:** `None`
- **Description:** Record a conflict for auditing.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert to dict for serialization.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> SourceMapSchema
```

- **Returns:** `SourceMapSchema`
- **Description:** Create from dict.

### `tokenpak.core.state_manager.IntentStateManager`

**Bases:** object

Intent-specific state manager.

Maintains a separate compact state blob per intent, injecting only
the fields relevant to that intent into the LLM context.

Persists to: .tpk/state/session_<id>.<intent>.state.json
Wire format: compact JSON, prefixed with STATE_JSON[<intent>]:

#### `__init__`

```python
def __init__(self, session_id: str, intent: str, base_dir: str = '.tpk') -> None
```

- **Returns:** `None`

#### `load`

```python
def load(self) -> StateDict
```

- **Returns:** `StateDict`
- **Description:** Load persisted state or initialize from defaults.

#### `validate`

```python
def validate(self) -> None
```

- **Returns:** `None`
- **Description:** Validate intent state against its JSON schema.

#### `save`

```python
def save(self) -> None
```

- **Returns:** `None`
- **Description:** Validate and persist state.

#### `set`

```python
def set(self, key: str, value: object) -> None
```

- **Returns:** `None`
- **Description:** Set a field in the current intent state.

#### `get`

```python
def get(self, key: str, default: object = None) -> object
```

- **Returns:** `object`
- **Description:** Get a field from the current intent state.

#### `update`

```python
def update(self, patch: Mapping[str, object]) -> None
```

- **Returns:** `None`
- **Description:** Shallow-merge a dict of updates into state.

#### `to_wire_format`

```python
def to_wire_format(self) -> str
```

- **Returns:** `str`
- **Description:** Compact JSON (no whitespace) — only fields relevant to this intent.

#### `to_wire_section`

```python
def to_wire_section(self) -> str
```

- **Returns:** `str`
- **Description:** Full STATE_JSON section tagged with intent.

### `tokenpak.core.state_manager.MultiSchemaStateManager`

**Bases:** object

Facade that manages multiple IntentStateManagers for a session.

Usage::

    mgr = MultiSchemaStateManager("sess-abc123")
    mgr.for_intent("debug").set("error", "NullPointerException in auth.py")
    mgr.for_intent("plan").set("objective", "migrate database to Postgres")
    wire = mgr.build_wire_section("debug")  # only injects debug fields

#### `__init__`

```python
def __init__(self, session_id: str, base_dir: str = '.tpk') -> None
```

- **Returns:** `None`

#### `for_intent`

```python
def for_intent(self, intent: str) -> IntentStateManager
```

- **Returns:** `IntentStateManager`
- **Description:** Get or create the IntentStateManager for a given intent.

#### `save_all`

```python
def save_all(self) -> None
```

- **Returns:** `None`
- **Description:** Persist all active intent states.

#### `build_wire_section`

```python
def build_wire_section(self, intent: str) -> str
```

- **Returns:** `str`
- **Description:** Build the STATE_JSON wire section for the given intent.

#### `active_intents`

```python
def active_intents(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return the list of intents with active state managers.

### `tokenpak.core.state_manager.StateManager`

**Bases:** object

Manages compact JSON session state for the TokenPak Integrity Protocol (TIP).

Persists to: .tpk/state/session_<id>.state.json   (`.tpk/` is the brand-shortform
on-disk path, frozen by the 2026-05-06 terminology transition; do not rename.
The protocol layer is **TIP** — `TPK` is brand-shortform only, not an acronym.
See `08-naming-glossary.md` §TPK and §TIP.)
Wire format: compact JSON (no whitespace), prefixed with STATE_JSON:

#### `__init__`

```python
def __init__(self, session_id: str, base_dir: str = '.tpk') -> None
```

- **Returns:** `None`

#### `load`

```python
def load(self) -> StateDict
```

- **Returns:** `StateDict`
- **Description:** Load state from disk, or initialize empty state.

#### `validate`

```python
def validate(self) -> None
```

- **Returns:** `None`
- **Description:** Validate state against schema. Raises ValidationError on failure.

#### `save`

```python
def save(self) -> None
```

- **Returns:** `None`
- **Description:** Validate then persist state to disk.

#### `set_goal`

```python
def set_goal(self, goal: str) -> None
```

- **Returns:** `None`
- **Description:** Set the high-level goal for the session.

#### `set_current_task`

```python
def set_current_task(self, task: str) -> None
```

- **Returns:** `None`
- **Description:** Set the currently-active task.

#### `mark_done`

```python
def mark_done(self, item: str) -> None
```

- **Returns:** `None`
- **Description:** Move item from open → done (if present), or just append to done.

#### `add_open`

```python
def add_open(self, item: str) -> None
```

- **Returns:** `None`
- **Description:** Add a new open/in-progress item to the state.

#### `add_next`

```python
def add_next(self, item: str) -> None
```

- **Returns:** `None`
- **Description:** Add a queued item for next action.

#### `add_constraint`

```python
def add_constraint(self, constraint: str) -> None
```

- **Returns:** `None`
- **Description:** Add a constraint or limitation to the session state.

#### `set_def`

```python
def set_def(self, key: str, value: object) -> None
```

- **Returns:** `None`
- **Description:** Set a key-value definition or config in the state defs map.

#### `apply_patch`

```python
def apply_patch(self, patch: Mapping[str, object]) -> None
```

- **Returns:** `None`
- **Description:** Apply a Phase 3 patch operation.

#### `to_wire_format`

```python
def to_wire_format(self) -> str
```

- **Returns:** `str`
- **Description:** Compact JSON for LLM payload (no whitespace).

#### `to_wire_section`

```python
def to_wire_section(self) -> str
```

- **Returns:** `str`
- **Description:** Full STATE_JSON section ready to embed in request payload.

#### `from_wire`

```python
def from_wire(cls, wire_text: str, session_id: str, base_dir: str = '.tpk') -> 'StateManager'
```

- **Returns:** `'StateManager'`
- **Description:** Parse a STATE_JSON wire section back into a StateManager.

### `tokenpak.core.validation.frontmatter.FrontmatterDiagnostics`

**Bases:** object

Structured parsing diagnostics for frontmatter.

#### `has_issues`

```python
def has_issues(self) -> bool
```

- **Returns:** `bool`

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.core.validation.request_validator.RequestValidationResult`

**Bases:** object

Result of a request validation check.

#### `__init__`

```python
def __init__(self, valid: bool, provider: str = 'unknown', errors: Optional[List[Dict[str, Any]]] = None, warnings: Optional[List[Dict[str, Any]]] = None) -> Any
```

- **Returns:** `Any`

#### `to_error_response`

```python
def to_error_response(self, docs_base: str = 'https://docs.tokenpak.ai/API/') -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Build a structured 400 error body (matches OpenAI/Anthropic style).

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.core.validation.request_validator.RequestValidator`

**Bases:** object

Validates incoming LLM proxy requests against provider schemas.

Args:
    mode: "strict" | "warn" | "off"
          strict → reject invalid requests (caller must return HTTP 400)
          warn   → log errors but treat as valid (default)
          off    → always return valid=True, skip all work

#### `__init__`

```python
def __init__(self, mode: str = 'warn') -> Any
```

- **Returns:** `Any`

#### `validate`

```python
def validate(self, body: bytes, provider: str) -> RequestValidationResult
```

- **Returns:** `RequestValidationResult`
- **Description:** Validate a raw request body for the given provider.

#### `validate_bytes`

```python
def validate_bytes(self, body: bytes, target_url: str, provider: str) -> RequestValidationResult
```

- **Returns:** `RequestValidationResult`
- **Description:** Convenience method — infers whether to validate based on URL pattern.

### `tokenpak.core.validation.validator.ResponseValidator`

**Bases:** object

Validates TokenPak responses against the schema contract.

Usage:
    validator = ResponseValidator()
    result = validator.validate(response_dict)
    if not result.valid:
        for error in result.errors:
            print(f"{error['field']}: {error['reason']}")

#### `__init__`

```python
def __init__(self, schema: Optional[Dict[str, Any]] = None, strict: bool = False, log_errors: bool = True) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize validator.

#### `validate`

```python
def validate(self, response: Dict[str, Any]) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Validate a response against the schema.

### `tokenpak.core.validation.validator.ValidationResult`

**Bases:** object

Result of a validation check.

#### `__init__`

```python
def __init__(self, valid: bool, errors: Optional[List[Dict[str, Any]]] = None, warnings: Optional[List[Dict[str, Any]]] = None) -> Any
```

- **Returns:** `Any`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.core.validation_gate.ValidationGate`

**Bases:** object

Final pre-forward guardrail checks for deterministic routing paths.

#### `__init__`

```python
def __init__(self, enabled: bool = True, token_budget_cap: int = 120000) -> Any
```

- **Returns:** `Any`

#### `validate`

```python
def validate(self, capsule: Any, dry_run: bool = False) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Compatibility entry point used by telemetry capsule endpoint.

#### `validate_request`

```python
def validate_request(self, request_body: bytes, model: str, input_tokens: int, router_meta: Optional[Mapping[str, Any]] = None) -> ValidationResult
```

- **Returns:** `ValidationResult`

### `tokenpak.core.validator.TokenPakValidator`

**Bases:** object

Validate Paks against the bundled JSON schema v1.0.

#### `validate`

```python
def validate(self, pack: Mapping[str, object], verbose: bool = False) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Validate a parsed pack dict. Returns a ValidationResult.

#### `validate_file`

```python
def validate_file(self, path: str | Path, verbose: bool = False) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Load and validate a JSON file.

### `tokenpak.core.validator.ValidationIssue`

**Bases:** object

A single validation error or warning.

#### `__init__`

```python
def __init__(self, level: str, field: str, message: str) -> None
```

- **Returns:** `None`

#### `to_dict`

```python
def to_dict(self) -> dict[str, str]
```

- **Returns:** `dict[str, str]`

### `tokenpak.core.validator.ValidationResult`

**Bases:** object

Complete result of a pack validation.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `error`

```python
def error(self, field: str, message: str) -> None
```

- **Returns:** `None`

#### `warning`

```python
def warning(self, field: str, message: str) -> None
```

- **Returns:** `None`

#### `info`

```python
def info(self, field: str, message: str) -> None
```

- **Returns:** `None`

#### `valid`

```python
def valid(self) -> bool
```

- **Returns:** `bool`

#### `errors`

```python
def errors(self) -> list[ValidationIssue]
```

- **Returns:** `list[ValidationIssue]`

#### `warnings`

```python
def warnings(self) -> list[ValidationIssue]
```

- **Returns:** `list[ValidationIssue]`

#### `summary`

```python
def summary(self) -> str
```

- **Returns:** `str`

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.creds.model.Credential`

**Bases:** object

A discovered credential.

Immutable snapshot of what a provider found at a given moment.
Re-run discovery to pick up changes (token refresh, new BYOK key).

#### `is_stale`

```python
def is_stale(self, now: int, grace_seconds: int = 0) -> bool
```

- **Returns:** `bool`
- **Description:** True if this is an OAuth cred past its expiry.

### `tokenpak.dashboard.export_api.ExportAPI`

**Bases:** object

Handles POST /v1/export/csv requests.

Usage (from _ProxyHandler.do_POST)::

    body, status, headers = ExportAPI.handle(
        raw_body=body_bytes,
        traces=[t.to_dict() for t in ps.trace_storage.get_all()],
        session_stats=ps.session_stats(),
    )
    self.send_response(status)
    for k, v in headers.items():
        self.send_header(k, v)
    self.end_headers()
    self.wfile.write(body)

#### `handle`

```python
def handle(raw_body: bytes, traces: Optional[List[Dict[str, Any]]] = None, session_stats: Optional[Dict[str, Any]] = None) -> Tuple[bytes, int, Dict[str, str]]
```

- **Returns:** `Tuple[bytes, int, Dict[str, str]]`
- **Description:** Process a /v1/export/csv request.

### `tokenpak.dashboard.export_csv.CSVExporter`

**Bases:** object

Generate CSV files from tokenpak proxy data.

Usage::

    exporter = CSVExporter(traces, session_stats)
    csv_bytes, filename = exporter.export(
        data_type=ExportDataType.TRACES,
        fmt=ExportFormat.FULL,
    )

#### `__init__`

```python
def __init__(self, traces: Optional[List[Dict[str, Any]]] = None, session_stats: Optional[Dict[str, Any]] = None) -> None
```

- **Returns:** `None`

#### `export`

```python
def export(self, data_type: ExportDataType = ExportDataType.TRACES, fmt: ExportFormat = ExportFormat.FULL, ts: Optional[datetime] = None) -> tuple[bytes, str]
```

- **Returns:** `tuple[bytes, str]`
- **Description:** Generate CSV.

### `tokenpak.dashboard.session_filter.FilterParams`

**Bases:** object

Parsed and validated filter parameters.

#### `__init__`

```python
def __init__(self, model: Optional[str] = None, from_dt: Optional[str] = None, to_dt: Optional[str] = None, status: Optional[str] = None, limit: Optional[int] = None, offset: Optional[int] = None) -> None
```

- **Returns:** `None`

#### `from_query_string`

```python
def from_query_string(cls, qs: str) -> 'FilterParams'
```

- **Returns:** `'FilterParams'`
- **Description:** Parse from a URL query string (e.g. 'model=gpt-4o&status=success').

### `tokenpak.dashboard.session_filter.SessionFilter`

**Bases:** object

Server-side session filter backed by SQLite.

Usage::

    sf = SessionFilter()
    result = sf.query(FilterParams(model="gpt-4o", status="success"))
    # result = {"sessions": [...], "total": N, "limit": 50, "offset": 0}

#### `__init__`

```python
def __init__(self, db_path: Optional[Path] = None) -> None
```

- **Returns:** `None`

#### `query`

```python
def query(self, params: FilterParams) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Execute a filtered + paginated query.

#### `distinct_models`

```python
def distinct_models(self) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Return sorted list of distinct model names in the DB.

### `tokenpak.debug.capture.CaptureMode`

**Bases:** enum.Enum

#### `from_env`

```python
def from_env(cls) -> 'CaptureMode'
```

- **Returns:** `'CaptureMode'`

### `tokenpak.licensing.__init__.License`

**Bases:** object

Current active license — Free by default, unless a key is stored.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> 'License'
```

- **Returns:** `'License'`

### `tokenpak.licensing.usage_meter.UsageEvent`

**Bases:** object

One usage event — what we POST to /usage.

#### `to_payload`

```python
def to_payload(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.licensing.usage_meter.UsageMeter`

**Bases:** object

Client-side usage meter with local buffering and graceful degradation.

Lifecycle:
    meter = UsageMeter(license_id="TPAK-...", server_url=...)
    meter.record(tokens_in=100, tokens_out=20, model="gpt-4o")
    meter.flush()              # explicit
    meter.start_heartbeat()    # 24h cadence
    meter.stop_heartbeat()

#### `__init__`

```python
def __init__(self, license_id: Optional[str] = None, server_url: str = DEFAULT_LICENSE_SERVER, spool_dir: Optional[Path] = None, http_timeout: float = DEFAULT_HTTP_TIMEOUT, heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS) -> None
```

- **Returns:** `None`

#### `record`

```python
def record(self, tokens_in: int, tokens_out: int, model: str, license_id: Optional[str] = None, ts: Optional[str] = None) -> None
```

- **Returns:** `None`
- **Description:** Append one usage event to the spool. Cheap, lock-protected.

#### `flush`

```python
def flush(self) -> dict
```

- **Returns:** `dict`
- **Description:** Drain the spool to the license server. Best-effort.

#### `start_heartbeat`

```python
def start_heartbeat(self) -> None
```

- **Returns:** `None`
- **Description:** Start the 24h flush heartbeat in a background daemon thread.

#### `stop_heartbeat`

```python
def stop_heartbeat(self) -> None
```

- **Returns:** `None`

### `tokenpak.models._discovery.ModelDiscovery`

**Bases:** object

Background thread that polls provider APIs for new model IDs.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `start`

```python
def start(self) -> None
```

- **Returns:** `None`
- **Description:** Start the discovery background thread.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Stop the discovery thread.

### `tokenpak.models._families.FamilyRule`

**Bases:** object

A pattern-based rule for inferring model properties.

#### `matches`

```python
def matches(self, model_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Check if model_id matches this family rule.

#### `infer_cache_read`

```python
def infer_cache_read(self, input_cost: float) -> float | None
```

- **Returns:** `float | None`

#### `infer_cache_write`

```python
def infer_cache_write(self, input_cost: float) -> float | None
```

- **Returns:** `float | None`

#### `infer_translation`

```python
def infer_translation(self, model_id: str) -> dict[str, str]
```

- **Returns:** `dict[str, str]`
- **Description:** Generate provider-specific translations from templates.

### `tokenpak.models._registry.ModelRegistry`

**Bases:** object

Thread-safe, hot-reloadable model registry.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `resolve`

```python
def resolve(self, model: str) -> ModelInfo
```

- **Returns:** `ModelInfo`
- **Description:** Resolve a model ID to its ModelInfo.

#### `get_max_context`

```python
def get_max_context(self, model: str | None) -> int | None
```

- **Returns:** `int | None`
- **Description:** Resolve the max input-context window in tokens for a model id.

#### `context_window_models`

```python
def context_window_models(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return the context-window table's model-id keys (sorted).

#### `detect_provider`

```python
def detect_provider(self, model: str) -> str
```

- **Returns:** `str`
- **Description:** Detect provider from model name using prefix matching.

#### `translate_model`

```python
def translate_model(self, model_id: str, provider: str) -> str
```

- **Returns:** `str`
- **Description:** Translate Anthropic model ID to provider-specific ID.

#### `get_shadow_target`

```python
def get_shadow_target(self, shadow_provider: str) -> tuple[str, str]
```

- **Returns:** `tuple[str, str]`
- **Description:** Map shadow provider string to (upstream_url, model_name).

#### `get_cheaper_alternative`

```python
def get_cheaper_alternative(self, model: str) -> tuple[str, float] | None
```

- **Returns:** `tuple[str, float] | None`
- **Description:** Find a cheaper model in the same provider, return (model_id, savings_fraction).

#### `get_all_tiers`

```python
def get_all_tiers(self) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Return model_id → tier mapping for all known models.

#### `get_default_routes`

```python
def get_default_routes(self) -> dict[str, str]
```

- **Returns:** `dict[str, str]`
- **Description:** Return model_id → provider mapping for all known models.

#### `all_models`

```python
def all_models(self) -> list[ModelInfo]
```

- **Returns:** `list[ModelInfo]`
- **Description:** Return all registered models.

#### `register`

```python
def register(self, info: ModelInfo) -> None
```

- **Returns:** `None`
- **Description:** Register or update a model at runtime (e.g. from discovery).

#### `reload`

```python
def reload(self, path: Path | None = None) -> None
```

- **Returns:** `None`
- **Description:** Reload the seed catalog (hot-reload for config changes).

#### `provider_cache_multipliers`

```python
def provider_cache_multipliers(self) -> dict[str, dict[str, float]]
```

- **Returns:** `dict[str, dict[str, float]]`

### `tokenpak.orchestration.capabilities.AgentCapabilities`

**Bases:** object

Standard capability schema for agents.

Attributes:
    gpu: Whether agent has GPU access
    memory_gb: Available memory in GB
    specialties: List of specialty tags (e.g., "code", "research", "data")
    max_concurrent: Maximum concurrent tasks
    provider_access: List of providers agent can use (e.g., ["anthropic", "openai"])
    custom: Additional custom capabilities

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'AgentCapabilities'
```

- **Returns:** `'AgentCapabilities'`

### `tokenpak.orchestration.capabilities.CapabilityMatcher`

**Bases:** object

Match task requirements against registered agents.

Usage:
    matcher = CapabilityMatcher()
    requirements = TaskRequirements(requires_gpu=True, min_memory_gb=8)
    matches = matcher.match(requirements)
    # matches is List[MatchResult], sorted by score descending

#### `__init__`

```python
def __init__(self, registry: Optional[AgentRegistry] = None) -> Any
```

- **Returns:** `Any`

#### `match`

```python
def match(self, requirements: TaskRequirements, include_stale: bool = False) -> List[MatchResult]
```

- **Returns:** `List[MatchResult]`
- **Description:** Find agents matching the requirements.

#### `find_best`

```python
def find_best(self, requirements: TaskRequirements) -> Optional[AgentInfo]
```

- **Returns:** `Optional[AgentInfo]`
- **Description:** Find the single best agent for requirements, or None if no match.

#### `find_by_specialty`

```python
def find_by_specialty(self, specialty: str) -> List[AgentInfo]
```

- **Returns:** `List[AgentInfo]`
- **Description:** Find all agents with a given specialty.

#### `find_with_provider`

```python
def find_with_provider(self, provider: str) -> List[AgentInfo]
```

- **Returns:** `List[AgentInfo]`
- **Description:** Find all agents that can access a given provider.

### `tokenpak.orchestration.capabilities.MatchResult`

**Bases:** object

Result of capability matching.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.orchestration.capabilities.TaskRequirements`

**Bases:** object

Requirements a task has for agent capabilities.

All fields are optional — unset means "any".

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.orchestration.case_memory.CaseMemoryDB`

**Bases:** object

Persistent case-based reasoning store with CRUD, search, and learning.

Args:
    storage_path: Path to the JSON file. Defaults to
                  ``~/.tokenpak/case_memory.json``.

#### `__init__`

```python
def __init__(self, storage_path: Optional[Path | str] = None) -> None
```

- **Returns:** `None`

#### `add`

```python
def add(self, case: CaseRecord) -> str
```

- **Returns:** `str`
- **Description:** Add or overwrite a case record.

#### `get`

```python
def get(self, case_id: str) -> Optional[CaseRecord]
```

- **Returns:** `Optional[CaseRecord]`
- **Description:** Return a case by ID, or None if not found.

#### `update`

```python
def update(self, case: CaseRecord) -> bool
```

- **Returns:** `bool`
- **Description:** Update an existing case (must exist). Returns True if succeeded.

#### `delete`

```python
def delete(self, case_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove a case. Returns True if it existed.

#### `all`

```python
def all(self) -> list[CaseRecord]
```

- **Returns:** `list[CaseRecord]`
- **Description:** Return all cases.

#### `by_type`

```python
def by_type(self, case_type: str) -> list[CaseRecord]
```

- **Returns:** `list[CaseRecord]`
- **Description:** Return cases of a given type.

#### `active`

```python
def active(self) -> list[CaseRecord]
```

- **Returns:** `list[CaseRecord]`
- **Description:** Return only active cases (status='active').

#### `count`

```python
def count(self) -> int
```

- **Returns:** `int`

#### `search`

```python
def search(self, query: str, case_type: Optional[str] = None, top_k: int = 5) -> list[CaseRecord]
```

- **Returns:** `list[CaseRecord]`
- **Description:** Search cases by entity overlap.

#### `record_outcome`

```python
def record_outcome(self, case_id: str, success: bool) -> Optional[CaseRecord]
```

- **Returns:** `Optional[CaseRecord]`
- **Description:** Update a case after it's been applied.

#### `reload`

```python
def reload(self) -> None
```

- **Returns:** `None`
- **Description:** Re-read the on-disk store (useful after external edits).

### `tokenpak.orchestration.commands.CommandAction`

**Bases:** object

A parsed, governed command action.

Either an argv vector (``use_shell=False`` — the default, ``shell=False``) or a
legacy shell command string (``use_shell=True`` — opt-in, ``shell=True``).

#### `display`

```python
def display(self) -> str
```

- **Returns:** `str`
- **Description:** Human-readable form for logs and dry-run output.

#### `is_empty`

```python
def is_empty(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.commands.CommandResult`

**Bases:** object

Result of executing a :class:`CommandAction`.

``output`` is the combined, stripped stdout+stderr that existing fire logs
record via ``store.log_fire(trigger, returncode, output)``.

#### `success`

```python
def success(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.dispatch.context.provider.ContextProvider`

**Bases:** Protocol

Dispatch context-assembly interface.

A provider turns a manifest + station into a :class:`ContextBundle`. The
OSS implementation is :class:`LocalContextProvider`; the Pro implementation
is :class:`PaidContextProvider` (stub in v0.1-alpha). Phase D activation is
a swap of the provider instance, not a rewrite.

#### `build_context`

```python
def build_context(self, manifest: DispatchManifest, station: RouteStation) -> ContextBundle
```

- **Returns:** `ContextBundle`
- **Description:** Assemble and return a :class:`ContextBundle` for ``station``.

### `tokenpak.orchestration.dispatch.context.provider.GitignoreFilter`

**Bases:** object

Lightweight gitignore evaluator over a single repo root (stdlib only).

Loads patterns from the root ``.gitignore`` (the common case for a Dispatch
repo scan) and evaluates whether a repo-relative path is ignored. Negation
(``!pat``) is honored using last-match-wins, mirroring git semantics for
the single-file case. ``.git/`` is always ignored independently of any
``.gitignore`` content.

This is intentionally a subset of full git ignore semantics (no nested
per-directory ``.gitignore`` chaining, no ``\`` escaping edge cases) —
sufficient for the deterministic local context scan and free of any third
party dependency.

#### `__init__`

```python
def __init__(self, rules: list[_GitignoreRule]) -> None
```

- **Returns:** `None`

#### `from_root`

```python
def from_root(cls, root: Path) -> 'GitignoreFilter'
```

- **Returns:** `'GitignoreFilter'`
- **Description:** Build a filter from ``<root>/.gitignore`` (empty filter if absent).

#### `is_ignored`

```python
def is_ignored(self, rel_posix: str, is_dir: bool = False) -> bool
```

- **Returns:** `bool`
- **Description:** Is ``rel_posix`` (repo-relative POSIX path) ignored?

### `tokenpak.orchestration.dispatch.context.provider.LocalContextProvider`

**Bases:** object

Deterministic, offline OSS context provider.

Construction:

* ``repo_root`` — directory the simple repo scan runs over, and the base
  every file path is resolved/relativized against. ``None`` disables the
  repo scan and disables resolving relative declared paths.
* ``budget`` — per-station :class:`ContextBudget`. The ``token_budget``
  inherits the Spend Guard cap at runtime; pass the
  live cap here. Defaults to the conservative module placeholders.
* ``explicit_files`` — extra explicit files (highest precedence) beyond
  anything the manifest declares. Paths may be absolute or repo-relative.
* ``frontmatter_files`` — current-task / frontmatter files to attach.
* ``manual_attachments`` — manually attached files.
* ``enable_repo_scan`` — include the gitignore-aware repo scan (default
  ``True`` when ``repo_root`` is set).
* ``scan_suffixes`` — restrict the repo scan to these suffixes (e.g.
  ``{".py", ".md"}``); ``None`` scans all readable text files.

Determinism: every list of candidates is processed in a fixed, sorted order
and budgets are applied greedily, so identical inputs yield an equal
bundle. No LLM, no network, no Pak dependency.

#### `__init__`

```python
def __init__(self, repo_root: str | Path | None = None, *, budget: ContextBudget | None = None, explicit_files: list[str | Path] | None = None, frontmatter_files: list[str | Path] | None = None, manual_attachments: list[str | Path] | None = None, enable_repo_scan: bool = True, scan_suffixes: set[str] | None = None) -> None
```

- **Returns:** `None`

#### `build_context`

```python
def build_context(self, manifest: DispatchManifest, station: RouteStation) -> ContextBundle
```

- **Returns:** `ContextBundle`
- **Description:** Assemble the :class:`ContextBundle` for ``station``.

### `tokenpak.orchestration.dispatch.context.provider.PaidContextProvider`

**Bases:** object

Pro context provider — NOT implemented in v0.1-alpha.

Exists from day one so the OSS/Pro boundary is visible and
Phase D activation is a constructor swap, not a rewrite. The real
implementation delegates to the ``tokenpak-paid`` Context Package Builder
over the loopback Pro daemon, falling back to :class:`LocalContextProvider`
when the daemon is absent. None of that ships in OSS v0.1-alpha.

Instantiating this class (or calling :meth:`build_context`) raises
``NotImplementedError`` so any accidental wiring fails loud rather than
silently degrading.

#### `__init__`

```python
def __init__(self, *args: object, **kwargs: object) -> None
```

- **Returns:** `None`

#### `build_context`

```python
def build_context(self, manifest: DispatchManifest, station: RouteStation) -> ContextBundle
```

- **Returns:** `ContextBundle`
- **Description:** Always raises ``NotImplementedError`` (Pro path not in v0.1-alpha).

### `tokenpak.orchestration.dispatch.dispatch.DispatchRuntime`

**Bases:** object

Wires FrontDock output → a selected route.

Does NOT execute stations (P-EXEC-01). Construct with a route registry +
worker registry (defaults load the packaged profiles) and an optional
:class:`RouteSuggester` (the LLM boundary; ``None`` ⇒ deterministic-only).
The single entry point is :meth:`select_route`.

#### `__init__`

```python
def __init__(self, route_registry: Optional[RouteRegistry] = None, worker_registry: Optional[DispatchWorkerRegistry] = None, suggester: Optional[RouteSuggester] = None) -> None
```

- **Returns:** `None`

#### `routes`

```python
def routes(self) -> RouteRegistry
```

- **Returns:** `RouteRegistry`

#### `workers`

```python
def workers(self) -> DispatchWorkerRegistry
```

- **Returns:** `DispatchWorkerRegistry`

#### `select_route`

```python
def select_route(self, intake: FrontDockResult, *, explicit_route: Optional[str] = None, project_rules: Optional[ProjectRules] = None, now: Optional[datetime] = None) -> SelectionOutcome
```

- **Returns:** `SelectionOutcome`
- **Description:** Select a route for an intake bundle using the precedence order.

### `tokenpak.orchestration.dispatch.dispatch.ProjectRules`

**Bases:** object

Project-level route overrides.

A thin, in-memory representation of ``.tpk/dispatch/project_rules.yaml``.
v0.1-alpha supports the one rule the precedence contract names: a
per-intent forced route (``intent_routes``). Absent rules (``None`` /
empty) make this step a no-op. Loading the YAML file itself is a later
CLI-layer concern; the runtime takes the already-parsed mapping so it stays
file-system-free and deterministic in tests.

#### `route_for_intent`

```python
def route_for_intent(self, intent: str) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Return the project-forced route id for ``intent``, or ``None``.

#### `is_empty`

```python
def is_empty(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.dispatch.dispatch.RouteRegistry`

**Bases:** Protocol

Runtime contract consumed by deterministic route selection.

#### `ids`

```python
def ids(self) -> list[str]
```

- **Returns:** `list[str]`

#### `all`

```python
def all(self) -> list[DispatchRoute]
```

- **Returns:** `list[DispatchRoute]`

#### `get`

```python
def get(self, route_id: str) -> DispatchRoute
```

- **Returns:** `DispatchRoute`

#### `has`

```python
def has(self, route_id: str) -> bool
```

- **Returns:** `bool`

#### `for_intent`

```python
def for_intent(self, intent: str) -> list[DispatchRoute]
```

- **Returns:** `list[DispatchRoute]`

### `tokenpak.orchestration.dispatch.dispatch.RouteScore`

**Bases:** object

A single candidate route's alpha score + its component breakdown.

#### `confidence`

```python
def confidence(self) -> int
```

- **Returns:** `int`
- **Description:** Confidence is the score clamped to [0, 100] (threshold domain).

### `tokenpak.orchestration.dispatch.dispatch.RouteSuggestClient`

**Bases:** Protocol

LLM boundary for route suggestion — routes through TIP at runtime.

Mirrors / extends the FrontDock :class:`TipClient` Protocol: the production
binding is the TIP client (Spend Guard enforced); in tests it is a
deterministic mock. **No provider SDK is imported or called by this module.**
A client returns a raw payload that :meth:`RouteSuggestion.from_payload`
validates; the LLM only ever *suggests*.

#### `suggest_route`

```python
def suggest_route(self, request: str, candidate_route_ids: list[str]) -> Union[Mapping[str, Any], 'RouteSuggestion']
```

- **Returns:** `Union[Mapping[str, Any], 'RouteSuggestion']`
- **Description:** Return a route suggestion (mapping or RouteSuggestion) for ``request``.

### `tokenpak.orchestration.dispatch.dispatch.RouteSuggester`

**Bases:** object

Consult an injected LLM client for a *suggestion* only (never dispatch).

Wraps a :class:`RouteSuggestClient` (or a FrontDock-style :class:`TipClient`
exposing ``suggest_route``). ``client=None`` is legal: :meth:`suggest`
returns ``None`` and the deterministic layer proceeds without LLM input.
A malformed / out-of-vocabulary suggestion is discarded (returns ``None``),
so the LLM can never push an unknown route through.

#### `__init__`

```python
def __init__(self, client: Optional[RouteSuggestClient] = None) -> None
```

- **Returns:** `None`

#### `suggest`

```python
def suggest(self, request: str, candidate_route_ids: list[str]) -> Optional[RouteSuggestion]
```

- **Returns:** `Optional[RouteSuggestion]`
- **Description:** Return a validated :class:`RouteSuggestion`, or ``None`` if unavailable.

### `tokenpak.orchestration.dispatch.dispatch.RouteSuggestion`

**Bases:** object

Schema-bound LLM route suggestion (advisory tie-break input).

The LLM may *suggest* a route; it never dispatches. This is the strict,
validated shape a suggestion must take: ``route_id`` + ``confidence`` +
``reasons`` + ``missing_info`` + ``risk_flags``. The deterministic layer
treats it as advisory tie-break input and discards it if ``route_id`` is not
a known, bindable route.

#### `from_payload`

```python
def from_payload(cls, payload: Mapping[str, Any]) -> 'RouteSuggestion'
```

- **Returns:** `'RouteSuggestion'`
- **Description:** Parse + validate a raw suggestion payload (fail-loud on bad shape).

### `tokenpak.orchestration.dispatch.dispatch.SelectionOutcome`

**Bases:** object

The result of route selection.

Exactly one of ``route`` (a selected, bound route) or ``decision`` (a
DispatchDecision asking the user) is set, per ``status``:

* ``auto_dispatch`` — route selected, confidence >= 60, autonomy permits;
* ``needs_approval`` — route selected, confidence >= 60, but autonomy is
  advisory/draft (the route is chosen but not auto-dispatched);
* ``decision`` — confidence 40..59 (or no decisive route): a
  :class:`DispatchDecision` is attached for the user;
* ``refused`` — confidence < 40: no route is dispatchable.

#### `is_auto_dispatch`

```python
def is_auto_dispatch(self) -> bool
```

- **Returns:** `bool`

#### `is_refused`

```python
def is_refused(self) -> bool
```

- **Returns:** `bool`

#### `needs_decision`

```python
def needs_decision(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.dispatch.frontdock.FrontDock`

**Bases:** object

Request intake: raw request → DispatchJob + draft manifest (+ optional decision).

Deterministic-first: :meth:`intake` resolves intent by rules and only consults
the injected :class:`TipClient` when the rules cannot. It is **not a worker**:
it produces records, never executes them. Construct with an optional TIP
client (``None`` is legal — the deterministic path needs no LLM; an ambiguous
request with no client resolves to ``unknown`` rather than calling a provider).

#### `__init__`

```python
def __init__(self, tip_client: TipClient | None = None) -> None
```

- **Returns:** `None`

#### `resolve_intent`

```python
def resolve_intent(self, request: str) -> IntentResolution
```

- **Returns:** `IntentResolution`
- **Description:** Resolve intent: rules first, LLM fallback only when ambiguous.

#### `intake`

```python
def intake(self, raw_request: str, *, autonomy_mode: AutonomyMode | str = AutonomyMode.DISPATCH_WITH_APPROVAL, source_task_packet_id: str | None = None, job_id: str | None = None, manifest_id: str | None = None, now: datetime | None = None) -> FrontDockResult
```

- **Returns:** `FrontDockResult`
- **Description:** Run intake over ``raw_request`` and return the output bundle.

### `tokenpak.orchestration.dispatch.frontdock.FrontDockResult`

**Bases:** object

The Front Dock's output bundle.

Carries the intake :class:`DispatchJob`, the draft
:class:`DispatchManifest`, and an optional blocking
:class:`DispatchDecision` (present iff a high-risk gap was detected). All
three are schema-valid Pydantic models.

#### `__init__`

```python
def __init__(self, job: DispatchJob, manifest: DispatchManifest, decision: DispatchDecision | None, intent_resolution: IntentResolution) -> None
```

- **Returns:** `None`

#### `is_blocked`

```python
def is_blocked(self) -> bool
```

- **Returns:** `bool`
- **Description:** True iff the Front Dock produced a blocking decision (high-risk gap).

### `tokenpak.orchestration.dispatch.frontdock.IntentResolution`

**Bases:** object

Result of intent detection: the intent plus how it was resolved.

``source`` is ``"deterministic"`` when the rule battery resolved it (no LLM
call), ``"llm"`` when the injected TIP client was consulted, or
``"unknown"`` when neither could resolve a confident intent.

#### `__init__`

```python
def __init__(self, intent: str, source: str, matched_keywords: frozenset[str] | None = None) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.frontdock.TipClient`

**Bases:** Protocol

Injected LLM boundary for FrontDock — routes through TIP at runtime.

The Front Dock is deterministic-first; this client is consulted **only** when
the rule battery cannot confidently resolve an intent. In production the
concrete binding is the TIP client (wired by P-RUNTIME-01 / P-EXEC-01); in
tests it is a deterministic mock. **No real provider SDK is imported or called
by this module** — all LLM access goes through this contract, which itself
goes through TIP (and therefore Spend Guard) at runtime.

A conforming client implements at least :meth:`classify_intent`; the optional
:meth:`complete` method is reserved for future judgment calls (assumption
refinement, etc.) and is not required for v0.1-alpha intent resolution.

#### `classify_intent`

```python
def classify_intent(self, request: str, candidates: list[str]) -> str
```

- **Returns:** `str`
- **Description:** Return one of ``candidates`` for ``request`` (the resolved intent).

#### `complete`

```python
def complete(self, prompt: str) -> Union[str, dict[str, Any]]
```

- **Returns:** `Union[str, dict[str, Any]]`
- **Description:** Optional free-form completion (reserved; not used by v0.1-alpha intake).

### `tokenpak.orchestration.dispatch.frontdock.UnknownRiskFlagError`

**Bases:** ValueError

Raised when a risk flag is not in :data:`RISK_FLAG_REGISTRY`.

#### `__init__`

```python
def __init__(self, flag: str) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.gatehouse.Gatehouse`

**Bases:** object

Deterministic structural validator + Delivery Gate.

No LLM, no network, no semantic correctness claims. Each ``check_*`` method
is a pure deterministic predicate returning a :class:`GatehouseCheckResult`.
:meth:`run_checks` runs the full battery; :meth:`evaluate_delivery` combines
the structural report with a :class:`ReviewerStationResult` to produce a
:class:`DeliveryPackage` per the handoff table.

#### `check_manifest_completeness`

```python
def check_manifest_completeness(self, manifest: DispatchManifest) -> GatehouseCheckResult
```

- **Returns:** `GatehouseCheckResult`
- **Description:** manifest_completeness — required manifest fields are present & non-empty.

#### `check_route_station_schema`

```python
def check_route_station_schema(self, route: Any) -> GatehouseCheckResult
```

- **Returns:** `GatehouseCheckResult`
- **Description:** route/station schema validity — the route parses as a DispatchRoute.

#### `check_acceptance_criteria_presence`

```python
def check_acceptance_criteria_presence(self, manifest: DispatchManifest) -> GatehouseCheckResult
```

- **Returns:** `GatehouseCheckResult`
- **Description:** acceptance-criteria presence — at least one acceptance criterion exists.

#### `check_station_output_schema`

```python
def check_station_output_schema(self, station_runs: list[DispatchStationRun], *, validators: dict[str, Callable[[dict[str, Any]], Any]] | None = None) -> GatehouseCheckResult
```

- **Returns:** `GatehouseCheckResult`
- **Description:** station output schema validity — completed stations carry valid output.

#### `check_permission_constraints`

```python
def check_permission_constraints(self, manifest: DispatchManifest) -> GatehouseCheckResult
```

- **Returns:** `GatehouseCheckResult`
- **Description:** permission constraints — manifest permissions are internally consistent.

#### `check_delivery_package_completeness`

```python
def check_delivery_package_completeness(self, route: DispatchRoute, package_fields: dict[str, Any]) -> GatehouseCheckResult
```

- **Returns:** `GatehouseCheckResult`
- **Description:** delivery package completeness — every route-required piece is present.

#### `run_checks`

```python
def run_checks(self, *, manifest: DispatchManifest, route: DispatchRoute, station_runs: list[DispatchStationRun] | None = None, delivery_package_fields: dict[str, Any] | None = None, station_output_validators: dict[str, Callable[[dict[str, Any]], Any]] | None = None) -> GatehouseReport
```

- **Returns:** `GatehouseReport`
- **Description:** Run every deterministic check and return the aggregate report.

#### `evaluate_delivery`

```python
def evaluate_delivery(self, *, job_id: str, manifest: DispatchManifest, route: DispatchRoute, reviewer_result: ReviewerStationResult, report: GatehouseReport | None = None, station_runs: list[DispatchStationRun] | None = None, delivery_package_fields: dict[str, Any] | None = None, station_output_validators: dict[str, Callable[[dict[str, Any]], Any]] | None = None, warning_decision_resolution: bool | None = None, route_uses_reviewer: bool = True, now: datetime | None = None) -> DeliveryPackage
```

- **Returns:** `DeliveryPackage`
- **Description:** Combine structural checks + the reviewer verdict into a DeliveryPackage.

### `tokenpak.orchestration.dispatch.gatehouse.GatehouseReport`

**Bases:** DispatchBaseModel

Aggregate of every deterministic Gatehouse check for one delivery.

#### `passed`

```python
def passed(self) -> bool
```

- **Returns:** `bool`
- **Description:** True iff every deterministic check passed (structurally shippable).

#### `failures`

```python
def failures(self) -> list[GatehouseCheckResult]
```

- **Returns:** `list[GatehouseCheckResult]`
- **Description:** The failed deterministic checks (empty when structurally clean).

### `tokenpak.orchestration.dispatch.ledger.db.RunLedger`

**Bases:** object

SQLite-backed store for Dispatch execution records.

Open the ledger at the canonical path with no arguments, or pass an explicit
``db_path`` (used by tests against ``tmp_path``)::

    ledger = RunLedger()                     # ~/.tpk/dispatch/runs.db
    ledger = RunLedger(db_path=tmp / "runs.db")

On open the parent directory is created (mode 0700, matching the home
contract) and the migration ladder is applied so the schema is current.

#### `__init__`

```python
def __init__(self, db_path: Optional[Path | str] = None) -> None
```

- **Returns:** `None`

#### `schema_version`

```python
def schema_version(self) -> int
```

- **Returns:** `int`
- **Description:** The migrated-to schema version recorded in the database header.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`
- **Description:** Close the underlying SQLite connection.

#### `write_job`

```python
def write_job(self, job: DispatchJob) -> None
```

- **Returns:** `None`

#### `read_job`

```python
def read_job(self, job_id: str) -> Optional[DispatchJob]
```

- **Returns:** `Optional[DispatchJob]`

#### `write_manifest`

```python
def write_manifest(self, manifest: DispatchManifest) -> None
```

- **Returns:** `None`

#### `read_manifest`

```python
def read_manifest(self, manifest_id: str) -> Optional[DispatchManifest]
```

- **Returns:** `Optional[DispatchManifest]`

#### `write_route`

```python
def write_route(self, route: DispatchRoute) -> None
```

- **Returns:** `None`

#### `read_route`

```python
def read_route(self, route_id: str) -> Optional[DispatchRoute]
```

- **Returns:** `Optional[DispatchRoute]`

#### `write_run`

```python
def write_run(self, run: DispatchRun) -> None
```

- **Returns:** `None`

#### `read_run`

```python
def read_run(self, run_id: str) -> Optional[DispatchRun]
```

- **Returns:** `Optional[DispatchRun]`

#### `write_station_run`

```python
def write_station_run(self, station_run: DispatchStationRun) -> None
```

- **Returns:** `None`

#### `read_station_run`

```python
def read_station_run(self, station_run_id: str) -> Optional[DispatchStationRun]
```

- **Returns:** `Optional[DispatchStationRun]`

#### `read_station_runs_for_run`

```python
def read_station_runs_for_run(self, run_id: str) -> list[DispatchStationRun]
```

- **Returns:** `list[DispatchStationRun]`
- **Description:** Return every station run for *run_id*, ordered by insertion (rowid).

#### `read_effects_for_station_run`

```python
def read_effects_for_station_run(self, station_run_id: str) -> list[DispatchEffect]
```

- **Returns:** `list[DispatchEffect]`
- **Description:** Return every effect recorded for *station_run_id* (ordered by created_at).

#### `write_decision`

```python
def write_decision(self, decision: DispatchDecision) -> None
```

- **Returns:** `None`

#### `read_decision`

```python
def read_decision(self, decision_id: str) -> Optional[DispatchDecision]
```

- **Returns:** `Optional[DispatchDecision]`

#### `write_artifact`

```python
def write_artifact(self, artifact: DispatchArtifact) -> None
```

- **Returns:** `None`

#### `read_artifact`

```python
def read_artifact(self, artifact_id: str) -> Optional[DispatchArtifact]
```

- **Returns:** `Optional[DispatchArtifact]`

#### `write_receipt`

```python
def write_receipt(self, receipt: DispatchReceipt) -> None
```

- **Returns:** `None`

#### `read_receipt`

```python
def read_receipt(self, receipt_id: str) -> Optional[DispatchReceipt]
```

- **Returns:** `Optional[DispatchReceipt]`

#### `write_late_result`

```python
def write_late_result(self, late_result: LateResult) -> None
```

- **Returns:** `None`

#### `read_late_result`

```python
def read_late_result(self, late_result_id: str) -> Optional[LateResult]
```

- **Returns:** `Optional[LateResult]`

#### `write_effect`

```python
def write_effect(self, effect: DispatchEffect) -> None
```

- **Returns:** `None`
- **Description:** Persist a DispatchEffect at whatever lifecycle state it carries.

#### `read_effect`

```python
def read_effect(self, effect_id: str) -> Optional[DispatchEffect]
```

- **Returns:** `Optional[DispatchEffect]`

#### `record_planned_effect`

```python
def record_planned_effect(self, effect: DispatchEffect) -> DispatchEffect
```

- **Returns:** `DispatchEffect`
- **Description:** Write a ``planned`` effect BEFORE tool execution.

#### `mark_effect_applied`

```python
def mark_effect_applied(self, effect_id: str, *, finalized_at: Optional[datetime] = None, after_hash: Optional[str] = None, rollback_available: Optional[bool] = None) -> DispatchEffect
```

- **Returns:** `DispatchEffect`
- **Description:** Transition a planned effect to ``applied`` AFTER success.

#### `mark_effect_failed`

```python
def mark_effect_failed(self, effect_id: str, *, finalized_at: Optional[datetime] = None) -> DispatchEffect
```

- **Returns:** `DispatchEffect`
- **Description:** Transition a planned effect to ``failed`` on error.

#### `try_claim_run_lease`

```python
def try_claim_run_lease(self, run_id: str, owner: str, *, now: Optional[datetime] = None, stale_after_seconds: int = DEFAULT_LEASE_STALE_SECONDS) -> bool
```

- **Returns:** `bool`
- **Description:** Atomically claim the run lease for *owner*; return True on success.

#### `release_run_lease`

```python
def release_run_lease(self, run_id: str, owner: str) -> bool
```

- **Returns:** `bool`
- **Description:** Release the run lease if *owner* holds it; return True when released.

#### `read_run_lease`

```python
def read_run_lease(self, run_id: str) -> Optional[dict[str, str]]
```

- **Returns:** `Optional[dict[str, str]]`
- **Description:** Return the current lease row for *run_id* (or ``None`` if unclaimed).

#### `select_dangling_planned_effects`

```python
def select_dangling_planned_effects(self, run_id: str) -> list[DispatchEffect]
```

- **Returns:** `list[DispatchEffect]`
- **Description:** Return ``planned`` effects with no ``finalized_at`` for *run_id*.

### `tokenpak.orchestration.dispatch.loop_policy.LoopOutcome`

**Bases:** object

The resolved stop condition for a loop, or ``None`` to keep looping.

``stop_condition`` is ``None`` while the loop should continue, otherwise the
exact :class:`LoopStopCondition` that fired. ``exhausted`` is True only for
the ``loop_budget_exhausted`` condition (it drives the ``on_exhausted``
actions in the station runner). ``produced_valid_output`` mirrors the loop
state's ``output_schema_valid`` for the runner's convenience.

#### `should_stop`

```python
def should_stop(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.dispatch.registry.capabilities.UnknownCapabilityError`

**Bases:** ValueError

Raised when a capability string is not in :data:`DISPATCH_CAPABILITIES`.

Subclasses :class:`ValueError` so Pydantic field validators surface it as a
standard validation error while still being catchable by exact type.

#### `__init__`

```python
def __init__(self, unknown: Iterable[str]) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.registry.route_registry.DispatchRouteRegistry`

**Bases:** object

Loads and indexes route profiles from one or more directories.

Routes are discovered by glob (``route.*.yaml``), so dropping a new profile
file into the routes directory registers it with no code change. Each profile
is parsed into a :class:`DispatchRoute`; the model's station field validator
rejects unknown capability strings at load time (fail-loud), and the
registry re-raises that as :class:`RouteProfileError` with the offending file
path attached.

#### `__init__`

```python
def __init__(self, routes: dict[str, DispatchRoute]) -> None
```

- **Returns:** `None`

#### `from_dir`

```python
def from_dir(cls, directory: Path | None = None) -> 'DispatchRouteRegistry'
```

- **Returns:** `'DispatchRouteRegistry'`
- **Description:** Build a registry from every ``route.*.yaml`` in ``directory``.

#### `ids`

```python
def ids(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return the registered route ids (sorted).

#### `all`

```python
def all(self) -> list[DispatchRoute]
```

- **Returns:** `list[DispatchRoute]`
- **Description:** Return all registered routes (ordered by id).

#### `get`

```python
def get(self, route_id: str) -> DispatchRoute
```

- **Returns:** `DispatchRoute`
- **Description:** Return the route with ``route_id``; raise ``KeyError`` if absent.

#### `has`

```python
def has(self, route_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Return ``True`` iff ``route_id`` is registered.

#### `for_intent`

```python
def for_intent(self, intent: str) -> list[DispatchRoute]
```

- **Returns:** `list[DispatchRoute]`
- **Description:** Return every route declaring ``intent`` in its triggers (sorted by id).

### `tokenpak.orchestration.dispatch.registry.route_registry.RouteResolutionError`

**Bases:** ValueError

Raised when a route's worker station cannot bind to any worker.

Carries the offending route + station ids and the reason (no worker with the
required role, or none with the full required-capability set) so the
dispatch runtime can report exactly why the route did not resolve.

#### `__init__`

```python
def __init__(self, route_id: str, station_id: str, reason: str) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.registry.workers.DispatchWorkerRegistry`

**Bases:** object

Loads and indexes worker profiles from one or more directories.

Workers are discovered by glob (``worker.*.yaml``), so dropping a new
profile file into the registry directory registers it with no code change.
Each profile is parsed into a :class:`DispatchWorker`; the model's field
validator rejects unknown capability strings at load time (fail-loud), and
the registry re-raises that as :class:`WorkerProfileError` with the
offending file path attached.

#### `__init__`

```python
def __init__(self, workers: dict[str, DispatchWorker]) -> None
```

- **Returns:** `None`

#### `from_dir`

```python
def from_dir(cls, directory: Path | None = None) -> 'DispatchWorkerRegistry'
```

- **Returns:** `'DispatchWorkerRegistry'`
- **Description:** Build a registry from every ``worker.*.yaml`` in ``directory``.

#### `ids`

```python
def ids(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return the registered worker ids (sorted).

#### `all`

```python
def all(self) -> list[DispatchWorker]
```

- **Returns:** `list[DispatchWorker]`
- **Description:** Return all registered workers (ordered by id).

#### `get`

```python
def get(self, worker_id: str) -> DispatchWorker
```

- **Returns:** `DispatchWorker`
- **Description:** Return the worker with ``worker_id``; raise ``KeyError`` if absent.

#### `for_role`

```python
def for_role(self, role: str) -> list[DispatchWorker]
```

- **Returns:** `list[DispatchWorker]`
- **Description:** Return every worker declaring ``role`` (dynamic role→worker lookup).

### `tokenpak.orchestration.dispatch.registry.workers.OverlayLoader`

**Bases:** object

Loads prompt overlays from the user dir with packaged-default fallback.

Resolution order per overlay id: a file in the **user** overlay directory
(``~/.tpk/dispatch/overlays/``) shadows the **packaged** default of the same
id. Both directories are discovered by glob (``overlay.*.yaml``); there is
no hardcoded overlay enumeration.

#### `__init__`

```python
def __init__(self, user_dir: Path | None = None, packaged_dir: Path | None = None) -> None
```

- **Returns:** `None`

#### `ids`

```python
def ids(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return the discoverable overlay ids (user + packaged, sorted).

#### `load`

```python
def load(self, overlay_id: str) -> PromptOverlay
```

- **Returns:** `PromptOverlay`
- **Description:** Load a single overlay by id (user dir shadows packaged default).

#### `load_all`

```python
def load_all(self) -> dict[str, PromptOverlay]
```

- **Returns:** `dict[str, PromptOverlay]`
- **Description:** Load every discoverable overlay into ``{id: PromptOverlay}``.

### `tokenpak.orchestration.dispatch.registry.workers.RouteBindError`

**Bases:** ValueError

Raised when an overlay/station cannot bind to a worker.

Carries the missing capabilities so the dispatcher can report exactly which
required capability the worker lacks.

#### `__init__`

```python
def __init__(self, worker_id: str, overlay_id: str | None, missing: Iterable[str]) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.runner.FulfillmentLine`

**Bases:** object

Sequential station-execution engine.

Construct with the foundation seams — a :class:`WorkerLLM` (the TIP worker
boundary), a context provider, a :class:`RunLedger`, a worker registry, and
optional Spend Guard / cancel token / reviewer client / overlay loader. Call
:meth:`run` with a *selected, bound* route, the manifest, and the autonomy
mode.

**Sequential, no parallel, no branches.** :meth:`_walk_stations` iterates
``route.stations`` in order. There is no fan-out, no concurrent station, and
no conditional branch primitive — that is the deliberate v0.1-alpha omission.
A later version may add a branch model; this runner does not.

#### `__init__`

```python
def __init__(self, *, worker_llm: WorkerLLM, context_provider: Any, ledger: RunLedger, worker_registry: DispatchWorkerRegistry, reviewer_llm: Optional[ReviewerLLM] = None, overlay_loader: Optional[OverlayLoader] = None, gatehouse: Optional[Gatehouse] = None, spend_guard: Optional[SpendGuard] = None, cancel_token: Optional[CancelToken] = None, tool_runner: Optional[Callable[[Any], Any]] = None, clock: Optional[Callable[[], datetime]] = None) -> None
```

- **Returns:** `None`

#### `run`

```python
def run(self, *, route: DispatchRoute, manifest: DispatchManifest, autonomy_mode: AutonomyMode | str, route_intent: Optional[str] = None, run_id: Optional[str] = None, approval_granted: bool = False) -> FulfillmentResult
```

- **Returns:** `FulfillmentResult`
- **Description:** Run a route's stations sequentially and return the line result.

#### `resume`

```python
def resume(self, *, run_id: str, route: DispatchRoute, manifest: DispatchManifest, autonomy_mode: AutonomyMode | str, workspace_root: str, route_intent: Optional[str] = None, approval_granted: bool = False) -> FulfillmentResult
```

- **Returns:** `FulfillmentResult`
- **Description:** Resume an interrupted run.

### `tokenpak.orchestration.dispatch.runner.RunAlreadyTerminalError`

**Bases:** RuntimeError

Raised when run()/resume() targets a run already in a terminal status.

#### `__init__`

```python
def __init__(self, run_id: str, status: str) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.runner.RunLeaseHeldError`

**Bases:** RuntimeError

Raised when another caller holds the run lease (concurrent run/resume).

#### `__init__`

```python
def __init__(self, run_id: str, holder: Optional[str]) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.station_runner.CancelToken`

**Bases:** Protocol

Cancellation signal checked before each loop iteration.

#### `is_cancelled`

```python
def is_cancelled(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True once cancellation has been requested for this job.

### `tokenpak.orchestration.dispatch.station_runner.FlagCancelToken`

**Bases:** object

A trivial in-memory cancel token (deterministic in tests).

The FulfillmentLine runner / CLI set :attr:`cancelled` when
``tokenpak dispatch cancel`` runs; the station runner checks it before each
iteration.

#### `__init__`

```python
def __init__(self, cancelled: bool = False) -> None
```

- **Returns:** `None`

#### `is_cancelled`

```python
def is_cancelled(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.dispatch.station_runner.StationRunOutcome`

**Bases:** object

The result of running one station (returned by :meth:`StationRunner.run`).

Carries the committed :class:`DispatchStationRun`, the stop condition
that ended the loop, any :class:`LateResult` captured on a post-cancel TIP
result, the ids of effects recorded during the run, and a failure reason
(set only on a failed run — e.g. :data:`SPEND_GUARD_EXCEEDED_REASON`).

#### `completed`

```python
def completed(self) -> bool
```

- **Returns:** `bool`

#### `cancelled`

```python
def cancelled(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.dispatch.station_runner.StationRunner`

**Bases:** object

Runs one route station's bounded loop.

Construct with the injected :class:`WorkerLLM` (the TIP boundary), a
:class:`ContextProvider`, a :class:`RunLedger`, and optional Spend Guard /
cancel token. Call :meth:`run` with the run id, the manifest, the route
station, the bound worker (+ overlay), and the autonomy mode.

Ledger protocol: a **RUNNING intent row** (same id as the terminal record,
attempt number set) is persisted before any station work begins, so an
interruption at any point leaves a durable marker for resume reconciliation.
The *terminal* station-run record is then committed **only after** its
schema-valid output is written (acceptance criterion 4): a successful run
rewrites the row to ``completed`` exactly once, atomically, after the loop
produced a valid payload; a failed / cancelled run rewrites it to its
terminal state once the loop ends. Effects recorded mid-loop (via tool
callables) are written through the ledger's effect lifecycle as they happen,
independent of the station-run commits.

#### `__init__`

```python
def __init__(self, *, worker_llm: WorkerLLM, context_provider: ContextProvider, ledger: RunLedger, spend_guard: Optional[SpendGuard] = None, cancel_token: Optional[CancelToken] = None, tool_runner: Optional[Callable[[WorkerToolRequest], Any]] = None, clock: Optional[Callable[[], datetime]] = None) -> None
```

- **Returns:** `None`

#### `run`

```python
def run(self, *, run_id: str, manifest: DispatchManifest, station: RouteStation, worker: DispatchWorker, autonomy_mode: AutonomyMode | str, overlay: Optional[PromptOverlay] = None, route_intent: Optional[str] = None, station_run_id: Optional[str] = None, attempt_number: int = 1, approval_granted: bool = False) -> StationRunOutcome
```

- **Returns:** `StationRunOutcome`
- **Description:** Execute one station's bounded loop and commit its terminal record.

### `tokenpak.orchestration.dispatch.station_runner.WorkerLLM`

**Bases:** Protocol

Injected TIP worker boundary — routes through TIP at runtime.

Mirrors the FrontDock ``TipClient`` / Reviewer ``ReviewerLLM`` contracts: in
production this is the TIP worker invocation (Spend Guard enforced); in tests
it is a deterministic mock. **No provider SDK is imported or called by this
module.** :meth:`run_turn` is called once per loop iteration with the
composed prompt, the assembled context bundle, and the tool outputs from the
previous iteration; it returns a :class:`WorkerTurn`.

#### `run_turn`

```python
def run_turn(self, *, prompt: list[str], context: ContextBundle, prior_tool_outputs: list[Any], iteration: int) -> WorkerTurn
```

- **Returns:** `WorkerTurn`
- **Description:** Return the worker's turn for this loop iteration.

### `tokenpak.orchestration.dispatch.stations.reviewer.ReviewerLLM`

**Bases:** Protocol

Injected single-call review client (routes through TIP at runtime).

The dispatch runtime (TIP worker invocation) is a later packet; the Reviewer
Station depends only on this thin contract so it can be exercised with a fake
client in tests and bound to the real TIP path once the runner lands. The
callable takes the rendered review prompt and returns the model's raw output
— either a JSON string or an already-parsed mapping. Exactly one call is made
per review.

#### `__call__`

```python
def __call__(self, prompt: str) -> Union[str, dict[str, Any]]
```

- **Returns:** `Union[str, dict[str, Any]]`

### `tokenpak.orchestration.dispatch.stations.reviewer.ReviewerStation`

**Bases:** object

Semantic-review station: one TIP LLM call per review.

Construct with an injected :class:`ReviewerLLM`; call :meth:`review` with a
:class:`ReviewerStationInput`. The station builds the review prompt, makes
**exactly one** client call, schema-validates the response into a
:class:`ReviewerStationResult` (fail-loud on malformed output), and returns
it with ``delivery_recommendation`` derived from ``status``. No repair loop.

#### `__init__`

```python
def __init__(self, client: ReviewerLLM) -> None
```

- **Returns:** `None`

#### `build_prompt`

```python
def build_prompt(self, payload: ReviewerStationInput) -> str
```

- **Returns:** `str`
- **Description:** Render the review prompt from the input (deterministic; no I/O).

#### `review`

```python
def review(self, payload: ReviewerStationInput) -> ReviewerStationResult
```

- **Returns:** `ReviewerStationResult`
- **Description:** Run one review: build prompt → one LLM call → parse/validate → derive.

### `tokenpak.orchestration.dispatch.stations.reviewer.ReviewerStationResult`

**Bases:** DispatchBaseModel

Output of the Reviewer Station.

``delivery_recommendation.status`` is **derived** from ``status`` and the
model enforces that invariant at validation time: a result whose
recommendation status does not match :data:`STATUS_TO_DELIVERY` is rejected
fail-loud. Callers should let :meth:`for_status` build the recommendation
rather than hand-author it.

#### `for_status`

```python
def for_status(cls, status: ReviewerStatus | str, *, criteria_results: list[CriterionResult] | None = None, required_fixes: list[RequiredFix] | None = None, risk_flags: list[ReviewerRiskFlag] | None = None, reason: str = '') -> 'ReviewerStationResult'
```

- **Returns:** `'ReviewerStationResult'`
- **Description:** Construct a result with the delivery recommendation derived from ``status``.

### `tokenpak.orchestration.dispatch.tools._matrix.ApprovalRequiredError`

**Bases:** RuntimeError

Raised when an APPROVAL-graded cell is invoked without granted approval.

#### `__init__`

```python
def __init__(self, tool: ToolName, mode: AutonomyMode) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.tools._matrix.ToolPolicyViolation`

**Bases:** RuntimeError

Raised when a tool is invoked in an autonomy mode that DENIES it.

#### `__init__`

```python
def __init__(self, tool: ToolName, mode: AutonomyMode) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.tools.apply_patch.PathPolicyViolation`

**Bases:** RuntimeError

Raised when an ``apply_patch`` target is rejected by the path policy.

#### `__init__`

```python
def __init__(self, target: str, reason: str) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.dispatch.tools.run_command.CommandCategoryError`

**Bases:** ValueError

Raised when a ``run_command`` category is forbidden or not on the allowlist.

#### `__init__`

```python
def __init__(self, category: CommandCategory, reason: str) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.episode_distiller.EpisodeRecord`

**Bases:** object

Structured record of a single agent work episode.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return a JSON-serializable dict.

#### `to_json`

```python
def to_json(self) -> str
```

- **Returns:** `str`
- **Description:** Return the record as a JSON string.

#### `to_sqlite_row`

```python
def to_sqlite_row(self) -> tuple[str, str, str, Optional[str], str, float, int, str]
```

- **Returns:** `tuple[str, str, str, Optional[str], str, float, int, str]`
- **Description:** Return a tuple suitable for SQLite INSERT (matches SQLITE_SCHEMA).

#### `from_dict`

```python
def from_dict(cls, d: dict[str, Any]) -> 'EpisodeRecord'
```

- **Returns:** `'EpisodeRecord'`
- **Description:** Reconstruct from a dict (e.g. JSON-parsed).

#### `outcome_score`

```python
def outcome_score(self) -> float
```

- **Returns:** `float`
- **Description:** Return numeric outcome score for QPT calculations.

#### `has_errors`

```python
def has_errors(self) -> bool
```

- **Returns:** `bool`

#### `tool_count`

```python
def tool_count(self) -> int
```

- **Returns:** `int`

### `tokenpak.orchestration.error_normalizer.ErrorNormalizer`

**Bases:** object

Normalizes semantically-equivalent error strings to stable signatures.

#### `__init__`

```python
def __init__(self, extra_pattern_path: Optional[Path] = None) -> None
```

- **Returns:** `None`

#### `normalize`

```python
def normalize(self, raw: str) -> str
```

- **Returns:** `str`

#### `suggest_merges_from_recipes`

```python
def suggest_merges_from_recipes(self, recipe_to_signatures: Mapping[str, Iterable[str]]) -> List[MergeSuggestion]
```

- **Returns:** `List[MergeSuggestion]`

### `tokenpak.orchestration.error_normalizer.FailureSignatureDB`

**Bases:** object

In-memory signature DB with normalization-aware lookup/merge accounting.

#### `__init__`

```python
def __init__(self, normalizer: Optional[ErrorNormalizer] = None) -> None
```

- **Returns:** `None`

#### `lookup`

```python
def lookup(self, raw_signature: str) -> Optional[FailureRecord]
```

- **Returns:** `Optional[FailureRecord]`

#### `record_failure`

```python
def record_failure(self, raw_signature: str, repair_recipe: Optional[str] = None) -> FailureRecord
```

- **Returns:** `FailureRecord`

#### `merge_synonym_stats`

```python
def merge_synonym_stats(self, signatures: Iterable[str]) -> Optional[FailureRecord]
```

- **Returns:** `Optional[FailureRecord]`

#### `auto_learn_merge_suggestions`

```python
def auto_learn_merge_suggestions(self) -> List[MergeSuggestion]
```

- **Returns:** `List[MergeSuggestion]`

### `tokenpak.orchestration.failure_memory.FailureMemoryDB`

**Bases:** object

Persistent failure signature store with CRUD, matching, and learning.

Args:
    storage_path: Path to the JSON file.  Defaults to
                  ``~/.tokenpak/failure_signatures.json``.

#### `__init__`

```python
def __init__(self, storage_path: Optional[Path | str] = None) -> None
```

- **Returns:** `None`

#### `add`

```python
def add(self, sig: FailureSignature) -> FailureSignature
```

- **Returns:** `FailureSignature`
- **Description:** Add or overwrite a failure signature.

#### `get`

```python
def get(self, signature_id: str) -> Optional[FailureSignature]
```

- **Returns:** `Optional[FailureSignature]`
- **Description:** Return a signature by ID, or None if not found.

#### `update`

```python
def update(self, sig: FailureSignature) -> FailureSignature
```

- **Returns:** `FailureSignature`
- **Description:** Update an existing signature (upsert).

#### `delete`

```python
def delete(self, signature_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove a signature.  Returns True if it existed.

#### `list_all`

```python
def list_all(self) -> list[FailureSignature]
```

- **Returns:** `list[FailureSignature]`
- **Description:** Return all signatures, sorted by confidence descending.

#### `count`

```python
def count(self) -> int
```

- **Returns:** `int`

#### `match`

```python
def match(self, error_text: str) -> Optional[FailureSignature]
```

- **Returns:** `Optional[FailureSignature]`
- **Description:** Find the best-matching signature for an error message.

#### `match_all`

```python
def match_all(self, error_text: str) -> list[FailureSignature]
```

- **Returns:** `list[FailureSignature]`
- **Description:** Return all matching signatures, sorted by confidence descending.

#### `record_repair_outcome`

```python
def record_repair_outcome(self, signature_id: str, *, success: bool) -> Optional[FailureSignature]
```

- **Returns:** `Optional[FailureSignature]`
- **Description:** Update a signature after a repair attempt.

#### `reload`

```python
def reload(self) -> None
```

- **Returns:** `None`
- **Description:** Re-read the on-disk store (useful after external edits).

### `tokenpak.orchestration.handoff.ContextRef`

**Bases:** object

A single context reference passed in a handoff.

#### `to_dict`

```python
def to_dict(self) -> JsonObject
```

- **Returns:** `JsonObject`

#### `from_dict`

```python
def from_dict(cls, d: Mapping[str, JsonValue]) -> 'ContextRef'
```

- **Returns:** `'ContextRef'`

### `tokenpak.orchestration.handoff.Handoff`

**Bases:** object

A context handoff record.

#### `to_dict`

```python
def to_dict(self) -> JsonObject
```

- **Returns:** `JsonObject`

#### `from_dict`

```python
def from_dict(cls, d: Mapping[str, JsonValue]) -> 'Handoff'
```

- **Returns:** `'Handoff'`

#### `is_expired`

```python
def is_expired(self) -> bool
```

- **Returns:** `bool`

#### `ttl_remaining_s`

```python
def ttl_remaining_s(self) -> float
```

- **Returns:** `float`

### `tokenpak.orchestration.handoff.HandoffBlock`

**Bases:** object

A single content block inside a TokenPak.

Attributes:
    type:     Semantic type label, e.g. "memory", "evidence", "task_state".
    id:       Unique identifier within the pack.
    content:  Text content.
    metadata: Optional key/value metadata.

#### `to_dict`

```python
def to_dict(self) -> JsonObject
```

- **Returns:** `JsonObject`

#### `from_dict`

```python
def from_dict(cls, d: Mapping[str, JsonValue]) -> 'HandoffBlock'
```

- **Returns:** `'HandoffBlock'`

### `tokenpak.orchestration.handoff.HandoffManager`

**Bases:** object

Manage context handoffs between agents.

#### `__init__`

```python
def __init__(self, handoff_dir: Optional[Path] = None) -> Any
```

- **Returns:** `Any`

#### `create_handoff`

```python
def create_handoff(self, from_agent: str, to_agent: str, context_refs: Optional[List[ContextRef]] = None, what_was_done: str = '', whats_next: str = '', relevant_files: Optional[List[str]] = None, ttl_hours: float = DEFAULT_TTL_HOURS, metadata: Optional[Mapping[str, JsonValue]] = None) -> Handoff
```

- **Returns:** `Handoff`
- **Raises:** `ValueError`
- **Description:** Create a new handoff and persist it to disk.

#### `receive_handoff`

```python
def receive_handoff(self, handoff_id: str) -> Handoff
```

- **Returns:** `Handoff`
- **Raises:** `FileNotFoundError`, `ValueError`
- **Description:** Validate context refs and mark handoff as received.

#### `apply_handoff`

```python
def apply_handoff(self, handoff_id: str) -> Handoff
```

- **Returns:** `Handoff`
- **Raises:** `FileNotFoundError`, `ValueError`
- **Description:** Mark handoff as applied and return loaded context.

#### `expire_stale`

```python
def expire_stale(self) -> int
```

- **Returns:** `int`
- **Description:** Expire all handoffs that have passed their TTL. Returns count expired.

#### `list_handoffs`

```python
def list_handoffs(self, to_agent: Optional[str] = None, from_agent: Optional[str] = None, status: Optional[HandoffStatus] = None) -> List[Handoff]
```

- **Returns:** `List[Handoff]`
- **Description:** List handoffs, optionally filtered by agent or status.

#### `get_handoff`

```python
def get_handoff(self, handoff_id: str) -> Optional[Handoff]
```

- **Returns:** `Optional[Handoff]`
- **Description:** Get a single handoff by ID.

### `tokenpak.orchestration.handoff.HandoffWire`

**Bases:** object

JSON-serialisable wire representation of a :class:`Handoff` + :class:`TokenPak`.

Usage::

    wire_obj = HandoffWire(pack=pack, from_agent="research", to_agent="writer")
    wire_str = wire_obj.to_wire()

    wire_obj2 = HandoffWire.from_wire(wire_str)
    context   = wire_obj2.pack.to_prompt()

This is intentionally separate from :class:`HandoffManager` (file-based
persistence) — the wire format is for direct in-process or network passing.

#### `__init__`

```python
def __init__(self, pack: TokenPak, from_agent: str, to_agent: str, summary: str = '', metadata: Optional[Mapping[str, JsonValue]] = None, handoff_id: Optional[str] = None) -> Any
```

- **Returns:** `Any`

#### `to_wire`

```python
def to_wire(self) -> str
```

- **Returns:** `str`
- **Description:** Serialise to a JSON string (the "wire" format).

#### `from_wire`

```python
def from_wire(cls, wire: str) -> 'HandoffWire'
```

- **Returns:** `'HandoffWire'`
- **Raises:** `ValueError`
- **Description:** Deserialise from JSON wire string.

#### `to_dict`

```python
def to_dict(self) -> JsonObject
```

- **Returns:** `JsonObject`
- **Description:** Return the wire payload as a JSON-compatible mapping.

### `tokenpak.orchestration.handoff.TokenPak`

**Bases:** object

A lightweight container of :class:`HandoffBlock` objects.

Designed for passing structured context between agents.

Example::

    pack = TokenPak()
    pack.add(HandoffBlock(type="memory", id="task_state", content=state))
    pack.add(HandoffBlock(type="evidence", id="findings", content=research))
    prompt = pack.to_prompt()

#### `__init__`

```python
def __init__(self, blocks: Optional[List[HandoffBlock]] = None) -> Any
```

- **Returns:** `Any`

#### `add`

```python
def add(self, block: HandoffBlock) -> 'TokenPak'
```

- **Returns:** `'TokenPak'`
- **Description:** Append a block to the pack. Returns self for chaining.

#### `remove`

```python
def remove(self, block_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove a block by id. Returns True if found and removed.

#### `get`

```python
def get(self, block_id: str) -> Optional[HandoffBlock]
```

- **Returns:** `Optional[HandoffBlock]`
- **Description:** Return the first block with the given id, or None.

#### `blocks_by_type`

```python
def blocks_by_type(self, block_type: str) -> List[HandoffBlock]
```

- **Returns:** `List[HandoffBlock]`
- **Description:** Return all blocks with the given type.

#### `blocks`

```python
def blocks(self) -> List[HandoffBlock]
```

- **Returns:** `List[HandoffBlock]`

#### `to_dict`

```python
def to_dict(self) -> JsonObject
```

- **Returns:** `JsonObject`

#### `from_dict`

```python
def from_dict(cls, d: Mapping[str, JsonValue]) -> 'TokenPak'
```

- **Returns:** `'TokenPak'`

#### `to_prompt`

```python
def to_prompt(self) -> str
```

- **Returns:** `str`
- **Description:** Render all blocks as a structured prompt string.

### `tokenpak.orchestration.locks.FileLockManager`

**Bases:** object

File lock registry for multi-agent coordination.

Parameters
----------
agent_id : str
    Identifier for the agent claiming locks (default: $TOKENPAK_AGENT or 'cali').
lock_dir : Path | str | None
    Directory where lock files are stored.
timeout_s : int
    Default lock timeout in seconds.

#### `__init__`

```python
def __init__(self, agent_id: Optional[str] = None, lock_dir: Optional[Path | str] = None, timeout_s: int = DEFAULT_TIMEOUT_S) -> Any
```

- **Returns:** `Any`

#### `claim`

```python
def claim(self, path: str | Path, timeout_s: Optional[int] = None) -> LockRecord
```

- **Returns:** `LockRecord`
- **Description:** Claim a lock on *path*.

#### `release`

```python
def release(self, path: str | Path) -> bool
```

- **Returns:** `bool`
- **Description:** Release the lock on *path*.

#### `query`

```python
def query(self, path: str | Path) -> Optional[LockRecord]
```

- **Returns:** `Optional[LockRecord]`
- **Description:** Return lock info for *path*, or None if unlocked / expired.

#### `locks`

```python
def locks(self) -> list[LockRecord]
```

- **Returns:** `list[LockRecord]`
- **Description:** Return all live (non-expired) lock records.

#### `prune_expired`

```python
def prune_expired(self) -> int
```

- **Returns:** `int`
- **Description:** Remove expired lock files. Returns count removed.

#### `suggest_alternatives`

```python
def suggest_alternatives(self, blocked_path: str | Path, candidates: list[str | Path]) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Given a list of candidate file paths, return those that are NOT

#### `renew`

```python
def renew(self, path: str | Path, timeout_s: Optional[int] = None) -> LockRecord
```

- **Returns:** `LockRecord`
- **Description:** Renew (extend) an existing lock held by this agent.

### `tokenpak.orchestration.locks.LockConflictError`

**Bases:** Exception

Raised when a file is already locked by another agent/process.

#### `__init__`

```python
def __init__(self, path: str, lock_info: LockRecord) -> Any
```

- **Returns:** `Any`

### `tokenpak.orchestration.macros.engine.MacroDefinition`

**Bases:** object

A user-defined macro loaded from YAML.

#### `__init__`

```python
def __init__(self, name: str, steps: List[MacroStep], description: str = '', variables: Optional[Dict[str, Any]] = None, continue_on_error: bool = False) -> Any
```

- **Returns:** `Any`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'MacroDefinition'
```

- **Returns:** `'MacroDefinition'`

#### `to_yaml`

```python
def to_yaml(self) -> str
```

- **Returns:** `str`

#### `from_yaml`

```python
def from_yaml(cls, text: str) -> 'MacroDefinition'
```

- **Returns:** `'MacroDefinition'`

### `tokenpak.orchestration.macros.engine.MacroEngine`

**Bases:** object

Core YAML macro engine.

Manages user-defined macros stored as YAML files in ~/.tokenpak/macros/.

#### `__init__`

```python
def __init__(self, macros_dir: Optional[Path] = None) -> Any
```

- **Returns:** `Any`

#### `create`

```python
def create(self, name: str, steps: List[Dict[str, Any]], description: str = '', variables: Optional[Dict[str, Any]] = None, continue_on_error: bool = False, overwrite: bool = False) -> Path
```

- **Returns:** `Path`
- **Raises:** `ValueError`
- **Description:** Create a new macro YAML file.

#### `create_from_yaml`

```python
def create_from_yaml(self, yaml_text: str, overwrite: bool = False) -> Path
```

- **Returns:** `Path`
- **Description:** Create a macro from raw YAML string.

#### `show`

```python
def show(self, name: str) -> MacroDefinition
```

- **Returns:** `MacroDefinition`
- **Raises:** `FileNotFoundError`
- **Description:** Load and return a macro definition.

#### `list`

```python
def list(self) -> List[MacroDefinition]
```

- **Returns:** `List[MacroDefinition]`
- **Description:** Return all user-defined macros, sorted by name.

#### `delete`

```python
def delete(self, name: str) -> bool
```

- **Returns:** `bool`
- **Description:** Delete a macro by name.

#### `exists`

```python
def exists(self, name: str) -> bool
```

- **Returns:** `bool`

#### `run`

```python
def run(self, name: str, variables: Optional[Dict[str, Any]] = None, dry_run: bool = False, continue_on_error: Optional[bool] = None) -> MacroResult
```

- **Returns:** `MacroResult`
- **Description:** Execute a macro by name.

#### `run_definition`

```python
def run_definition(self, macro: MacroDefinition, variables: Optional[Dict[str, Any]] = None, dry_run: bool = False, continue_on_error: Optional[bool] = None) -> MacroResult
```

- **Returns:** `MacroResult`
- **Description:** Execute a MacroDefinition object.

### `tokenpak.orchestration.macros.engine.MacroResult`

**Bases:** object

#### `__init__`

```python
def __init__(self, macro_name: str, steps: List[StepResult], started_at: str, finished_at: str, success: bool, dry_run: bool = False) -> Any
```

- **Returns:** `Any`

#### `duration_seconds`

```python
def duration_seconds(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `format`

```python
def format(self) -> str
```

- **Returns:** `str`
- **Description:** Return human-readable output.

### `tokenpak.orchestration.macros.engine.MacroStep`

**Bases:** object

A single step within a macro.

#### `__init__`

```python
def __init__(self, name: str, cmd: str, label: str = '', timeout: int = 60) -> Any
```

- **Returns:** `Any`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'MacroStep'
```

- **Returns:** `'MacroStep'`

### `tokenpak.orchestration.macros.engine.StepResult`

**Bases:** object

#### `__init__`

```python
def __init__(self, name: str, label: str, cmd: str, output: str, error: str, success: bool, returncode: int, dry_run: bool = False) -> Any
```

- **Returns:** `Any`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.orchestration.macros.hooks.EventType`

**Bases:** str, Enum

Supported event types.

#### `from_string`

```python
def from_string(cls, value: str) -> 'EventType'
```

- **Returns:** `'EventType'`
- **Description:** Parse event type from string.

### `tokenpak.orchestration.macros.hooks.Trigger`

**Bases:** object

A trigger that maps an event pattern to an action.

#### `matches`

```python
def matches(self, event_type: str, event_data: str) -> bool
```

- **Returns:** `bool`
- **Description:** Check if this trigger matches the given event.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'Trigger'
```

- **Returns:** `'Trigger'`

### `tokenpak.orchestration.macros.hooks.TriggerLogEntry`

**Bases:** object

Log entry for a trigger activation.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.orchestration.macros.hooks.TriggerRegistry`

**Bases:** object

Registry for event triggers.

Stores triggers in JSON format for persistence.
Provides methods to add, remove, list, test, and fire triggers.

#### `__init__`

```python
def __init__(self, triggers_path: Optional[Path] = None, log_path: Optional[Path] = None) -> Any
```

- **Returns:** `Any`

#### `add`

```python
def add(self, event_type: str, pattern: str, action: str, description: str = '') -> Trigger
```

- **Returns:** `Trigger`
- **Description:** Register a new trigger.

#### `remove`

```python
def remove(self, trigger_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove a trigger by ID.

#### `list`

```python
def list(self, event_type: Optional[str] = None) -> List[Trigger]
```

- **Returns:** `List[Trigger]`
- **Description:** List all triggers, optionally filtered by event type.

#### `get`

```python
def get(self, trigger_id: str) -> Optional[Trigger]
```

- **Returns:** `Optional[Trigger]`
- **Description:** Get a trigger by ID.

#### `test`

```python
def test(self, event_type: str, event_data: str = '*') -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Dry-run: show what triggers would fire for an event.

#### `fire`

```python
def fire(self, event_type: str, event_data: str, dry_run: bool = False, env: Optional[Dict[str, str]] = None) -> List[TriggerLogEntry]
```

- **Returns:** `List[TriggerLogEntry]`
- **Description:** Fire all triggers matching an event.

#### `get_log`

```python
def get_log(self, limit: int = 50, trigger_id: Optional[str] = None) -> List[TriggerLogEntry]
```

- **Returns:** `List[TriggerLogEntry]`
- **Description:** Get recent trigger activations.

#### `clear_log`

```python
def clear_log(self) -> int
```

- **Returns:** `int`
- **Description:** Clear the trigger log. Returns number of entries cleared.

### `tokenpak.orchestration.macros.premade_macros.PremadeMacroRunner`

**Bases:** object

Runs premade macros and formats their output.

#### `install`

```python
def install(self, name: str) -> Path
```

- **Returns:** `Path`
- **Raises:** `ValueError`
- **Description:** Install a premade macro as a JSON descriptor in ~/.tokenpak/macros/.

#### `run`

```python
def run(self, name: str, json_output: bool = False) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Run a premade macro and return structured results.

#### `format_output`

```python
def format_output(self, result: Dict[str, Any]) -> str
```

- **Returns:** `str`
- **Description:** Format macro results for human-readable display.

#### `list_available`

```python
def list_available(self) -> List[Dict[str, str]]
```

- **Returns:** `List[Dict[str, str]]`
- **Description:** List all premade macros.

### `tokenpak.orchestration.macros.scheduler.MacroScheduler`

**Bases:** object

Scheduler for macros using system cron and at-style one-shots.

Persists schedule info in ~/.tokenpak/scheduled.json.

#### `__init__`

```python
def __init__(self, schedule_path: Optional[Path] = None) -> Any
```

- **Returns:** `Any`

#### `schedule_cron`

```python
def schedule_cron(self, name: str, cron_expr: str, command: Optional[str] = None, description: str = '') -> ScheduledMacro
```

- **Returns:** `ScheduledMacro`
- **Description:** Schedule a macro on a cron expression.

#### `schedule_at`

```python
def schedule_at(self, name: str, run_at: str, command: Optional[str] = None, description: str = '') -> ScheduledMacro
```

- **Returns:** `ScheduledMacro`
- **Description:** Schedule a one-shot macro run at a specific time.

#### `list_scheduled`

```python
def list_scheduled(self) -> List[ScheduledMacro]
```

- **Returns:** `List[ScheduledMacro]`
- **Description:** List all scheduled macros.

#### `cancel`

```python
def cancel(self, schedule_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Cancel a scheduled run by ID.

#### `get`

```python
def get(self, schedule_id: str) -> Optional[ScheduledMacro]
```

- **Returns:** `Optional[ScheduledMacro]`
- **Description:** Get a scheduled macro by ID.

### `tokenpak.orchestration.macros.scheduler.ScheduledMacro`

**Bases:** object

A scheduled macro run.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'ScheduledMacro'
```

- **Returns:** `'ScheduledMacro'`

### `tokenpak.orchestration.memory_promoter.Lesson`

**Bases:** object

A learned lesson with promotion tracking.

#### `success_rate`

```python
def success_rate(self) -> float
```

- **Returns:** `float`
- **Description:** Return success rate (0-1).

#### `days_since_contradicted`

```python
def days_since_contradicted(self) -> float
```

- **Returns:** `float`
- **Description:** Return days since last contradiction, or infinity if never contradicted.

#### `is_expired`

```python
def is_expired(self) -> bool
```

- **Returns:** `bool`
- **Description:** Check if this lesson has exceeded its tier's TTL.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.orchestration.memory_promoter.MemoryPromoter`

**Bases:** object

Manages promotion and demotion of learned lessons.

#### `__init__`

```python
def __init__(self, path: str | Path = DEFAULT_PROMOTER_PATH) -> Any
```

- **Returns:** `Any`

#### `add_lesson`

```python
def add_lesson(self, lesson_id: str, content: str, specificity_score: float = 0.5, savings_pct: float = 0.0, metadata: Optional[Mapping[str, object]] = None) -> Lesson
```

- **Returns:** `Lesson`
- **Description:** Create a new lesson starting at Tier 1.

#### `record_success`

```python
def record_success(self, lesson_id: str, dry_run: bool = False) -> Optional[Lesson]
```

- **Returns:** `Optional[Lesson]`
- **Description:** Record a successful application of this lesson.

#### `record_failure`

```python
def record_failure(self, lesson_id: str) -> Optional[Lesson]
```

- **Returns:** `Optional[Lesson]`
- **Description:** Record a failed application of this lesson.

#### `record_contradiction`

```python
def record_contradiction(self, lesson_id: str) -> Optional[Lesson]
```

- **Returns:** `Optional[Lesson]`
- **Description:** Record that this lesson was contradicted by new evidence.

#### `cleanup_expired`

```python
def cleanup_expired(self, dry_run: bool = False) -> int
```

- **Returns:** `int`
- **Description:** Remove or demote expired lessons. Returns count of lessons affected.

#### `get_tier_lessons`

```python
def get_tier_lessons(self, tier: int) -> list[Lesson]
```

- **Returns:** `list[Lesson]`
- **Description:** Get all lessons at a specific tier.

#### `get_lesson`

```python
def get_lesson(self, lesson_id: str) -> Optional[Lesson]
```

- **Returns:** `Optional[Lesson]`
- **Description:** Get a specific lesson.

#### `get_all_lessons`

```python
def get_all_lessons(self) -> list[Lesson]
```

- **Returns:** `list[Lesson]`
- **Description:** Get all lessons.

#### `stats`

```python
def stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return statistics about the memory store.

### `tokenpak.orchestration.precondition_gates.Gate`

**Bases:** object

A single precondition gate for a workflow step.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, d: dict[str, Any]) -> 'Gate'
```

- **Returns:** `'Gate'`

### `tokenpak.orchestration.precondition_gates.PreconditionGates`

**Bases:** object

Manage precondition gates for workflow steps.

Args:
    gates_path:    Path to ``preconditions.json`` (gates store).
    failures_path: Path to ``precondition_failures.jsonl`` (failure log).
    threshold:     How many failures trigger auto-promotion of a gate.

#### `__init__`

```python
def __init__(self, gates_path: Path = DEFAULT_GATES_PATH, failures_path: Path = DEFAULT_FAILURES_PATH, threshold: int = AUTO_PROMOTE_THRESHOLD) -> None
```

- **Returns:** `None`

#### `add_gate`

```python
def add_gate(self, gate: Gate) -> None
```

- **Returns:** `None`
- **Description:** Manually register a gate for a step.

#### `remove_gate`

```python
def remove_gate(self, step: str, gate_type: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove a gate by step + type. Returns True if removed.

#### `list_gates`

```python
def list_gates(self, step: Optional[str] = None) -> List[Gate]
```

- **Returns:** `List[Gate]`
- **Description:** List all gates, optionally filtered by step.

#### `check`

```python
def check(self, step: str) -> Tuple[bool, str]
```

- **Returns:** `Tuple[bool, str]`
- **Description:** Evaluate all gates for *step*.

#### `record_failure`

```python
def record_failure(self, step: str, gate_type: str, params: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None) -> None
```

- **Returns:** `None`
- **Description:** Record a step failure where a precondition of *gate_type* was missing.

#### `promote_patterns`

```python
def promote_patterns(self) -> List[Gate]
```

- **Returns:** `List[Gate]`
- **Description:** Scan the failure log for patterns that exceed the threshold and

#### `gate_summary`

```python
def gate_summary(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return a summary dict suitable for logging/display.

### `tokenpak.orchestration.prefetcher.PredictivePrefetcher`

**Bases:** object

Learns likely next artifacts and preloads them on trigger events.

#### `__init__`

```python
def __init__(self, store_path: str | None = None) -> None
```

- **Returns:** `None`

#### `save`

```python
def save(self) -> None
```

- **Returns:** `None`

#### `record_transition`

```python
def record_transition(self, completed_step: str, next_step: str, artifacts: Iterable[str]) -> None
```

- **Returns:** `None`
- **Description:** Learn artifact demand for transition: completed_step -> next_step.

#### `learn_workflow_path`

```python
def learn_workflow_path(self, steps: Sequence[str], artifacts_by_step: Dict[str, Iterable[str]]) -> None
```

- **Returns:** `None`
- **Description:** Learn transition patterns from a historical workflow path.

#### `register_task_type_artifacts`

```python
def register_task_type_artifacts(self, task_type: str, artifacts: Iterable[str]) -> None
```

- **Returns:** `None`
- **Description:** Learn common files used by a task type.

#### `register_error_artifacts`

```python
def register_error_artifacts(self, error_kind: str, artifacts: Iterable[str]) -> None
```

- **Returns:** `None`
- **Description:** Register diagnostic artifacts to load when an error kind is detected.

#### `recommend_for_completed_step`

```python
def recommend_for_completed_step(self, completed_step: str, limit: int = 5) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Predict likely artifacts needed after this completed step.

#### `recommend_for_task_type`

```python
def recommend_for_task_type(self, task_type: str, limit: int = 5) -> List[str]
```

- **Returns:** `List[str]`

#### `recommend_for_error`

```python
def recommend_for_error(self, error_kind: str, extra_artifacts: Iterable[str] | None = None) -> List[str]
```

- **Returns:** `List[str]`

#### `on_workflow_step_completed`

```python
def on_workflow_step_completed(self, completed_step: str, preload: Callable[[str], None], limit: int = 5) -> List[str]
```

- **Returns:** `List[str]`

#### `on_task_type_recognized`

```python
def on_task_type_recognized(self, task_type: str, preload: Callable[[str], None], limit: int = 5) -> List[str]
```

- **Returns:** `List[str]`

#### `on_error_detected`

```python
def on_error_detected(self, error_kind: str, preload: Callable[[str], None], extra_artifacts: Iterable[str] | None = None) -> List[str]
```

- **Returns:** `List[str]`

### `tokenpak.orchestration.registry.AgentInfo`

**Bases:** object

Information about a registered agent.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'AgentInfo'
```

- **Returns:** `'AgentInfo'`

#### `is_stale`

```python
def is_stale(self, expire_seconds: int = DEFAULT_EXPIRE_SECONDS) -> bool
```

- **Returns:** `bool`
- **Description:** Check if agent hasn't sent heartbeat within expire window.

#### `heartbeat_age_seconds`

```python
def heartbeat_age_seconds(self) -> float
```

- **Returns:** `float`
- **Description:** Seconds since last heartbeat.

### `tokenpak.orchestration.registry.AgentRegistry`

**Bases:** object

Persistent agent registry with heartbeat tracking.

Usage:
    registry = AgentRegistry()
    agent_id = registry.register("trix", "agent-2", {"gpu": False, "memory_gb": 4})
    registry.heartbeat(agent_id)
    agents = registry.list_active()
    registry.deregister(agent_id)

#### `__init__`

```python
def __init__(self, path: Optional[Path] = None, expire_seconds: int = DEFAULT_EXPIRE_SECONDS) -> Any
```

- **Returns:** `Any`

#### `register`

```python
def register(self, name: str, hostname: str, capabilities: Optional[Dict[str, Any]] = None, agent_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str
```

- **Returns:** `str`
- **Description:** Register a new agent or update existing one.

#### `deregister`

```python
def deregister(self, agent_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove an agent from registry. Returns True if found and removed.

#### `get`

```python
def get(self, agent_id: str) -> Optional[AgentInfo]
```

- **Returns:** `Optional[AgentInfo]`
- **Description:** Get agent by ID.

#### `heartbeat`

```python
def heartbeat(self, agent_id: str, status: Optional[str] = None, current_task: Optional[str] = None) -> bool
```

- **Returns:** `bool`
- **Description:** Update agent heartbeat timestamp.

#### `list_all`

```python
def list_all(self) -> List[AgentInfo]
```

- **Returns:** `List[AgentInfo]`
- **Description:** List all registered agents (including stale).

#### `list_active`

```python
def list_active(self) -> List[AgentInfo]
```

- **Returns:** `List[AgentInfo]`
- **Description:** List only active (non-stale) agents.

#### `prune_stale`

```python
def prune_stale(self) -> int
```

- **Returns:** `int`
- **Description:** Remove stale agents. Returns count removed.

#### `find_by_name`

```python
def find_by_name(self, name: str) -> List[AgentInfo]
```

- **Returns:** `List[AgentInfo]`
- **Description:** Find agents by name.

#### `find_by_hostname`

```python
def find_by_hostname(self, hostname: str) -> List[AgentInfo]
```

- **Returns:** `List[AgentInfo]`
- **Description:** Find agents by hostname.

#### `clear`

```python
def clear(self) -> int
```

- **Returns:** `int`
- **Description:** Remove all agents. Returns count removed.

### `tokenpak.orchestration.retry.ImmediateAlertError`

**Bases:** Exception

Raised by per-error routing when an auth/fatal error demands immediate alert.

#### `__init__`

```python
def __init__(self, status_code: str, original: Exception) -> Any
```

- **Returns:** `Any`

### `tokenpak.orchestration.retry.RetryAttempt`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> RetryAttemptData
```

- **Returns:** `RetryAttemptData`

### `tokenpak.orchestration.retry.RetryEngine`

**Bases:** Generic[TaskResult]

5-level retry engine with escalation, per-error routing, and partial-state preservation.

Parameters
----------
fn : callable
    The task function. Signature: fn(context, partial_state) -> result.
    Should update partial_state in-place as it makes progress.
context : dict
    Task metadata (task name, args, agent_id, etc.).
partial_state : dict | None
    Mutable state tracking progress. Created fresh if None.
state_dir : Path | None
    Where to persist partial state on failure.
agent_id : str | None
    Current agent identifier.
wait_seconds : list[float]
    Wait times between Level-0 retries. Defaults to config or [1, 2, 4].
per_error : dict[str, str] | None
    Map of HTTP status code str → behavior ("wait", "retry", "alert").
    Merged over DEFAULT_PER_ERROR; config file takes next priority.
on_model_downgrade : callable | None
    Hook: (current_model) -> next_model string.
on_provider_switch : callable | None
    Hook: (current_provider) -> next_provider string.
on_handoff : callable | None
    Hook: (context, partial_state) -> bool (True = accepted).
on_human_alert : callable | None
    Hook: (alert_dict) -> None. Default: logs at CRITICAL level.

#### `__init__`

```python
def __init__(self, fn: Callable[[RetryData, RetryData], TaskResult], context: RetryData, partial_state: Optional[RetryData] = None, state_dir: Optional[Path | str] = None, agent_id: Optional[str] = None, wait_seconds: Optional[list[float]] = None, per_error: Optional[dict[str, str]] = None, on_model_downgrade: Optional[Callable[[str], str]] = None, on_provider_switch: Optional[Callable[[str], str]] = None, on_handoff: Optional[Callable[[RetryData, RetryData], bool]] = None, on_human_alert: Optional[Callable[[RetryData], None]] = None) -> None
```

- **Returns:** `None`

#### `load_state`

```python
def load_state(cls, state_file: Path) -> RetryData
```

- **Returns:** `RetryData`
- **Description:** Reload persisted state for inspection or resume.

#### `run`

```python
def run(self) -> TaskResult | HandoffResult
```

- **Returns:** `TaskResult | HandoffResult`
- **Description:** Execute the task with full escalation.

### `tokenpak.orchestration.retry.RetryExhaustedError`

**Bases:** Exception

Raised when all 5 escalation levels have failed.

#### `__init__`

```python
def __init__(self, context: RetryData, partial_state: RetryData, attempts: list['RetryAttempt']) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.runbook_generator.RunbookDB`

**Bases:** object

Storage and retrieval for self-generated runbooks.

#### `__init__`

```python
def __init__(self, runbooks_dir: Optional[Path | str] = None, index_path: Optional[Path | str] = None) -> None
```

- **Returns:** `None`

#### `add`

```python
def add(self, rb: RunbookEntry) -> RunbookEntry
```

- **Returns:** `RunbookEntry`
- **Description:** Persist a new runbook. Raises ValueError if id already exists.

#### `get`

```python
def get(self, runbook_id: str) -> Optional[RunbookEntry]
```

- **Returns:** `Optional[RunbookEntry]`

#### `update`

```python
def update(self, rb: RunbookEntry) -> RunbookEntry
```

- **Returns:** `RunbookEntry`

#### `delete`

```python
def delete(self, runbook_id: str) -> bool
```

- **Returns:** `bool`

#### `list_all`

```python
def list_all(self) -> List[RunbookEntry]
```

- **Returns:** `List[RunbookEntry]`

#### `count`

```python
def count(self) -> int
```

- **Returns:** `int`

#### `find_duplicate`

```python
def find_duplicate(self, error_class: str, task_type: str, title: str = '') -> Optional[RunbookEntry]
```

- **Returns:** `Optional[RunbookEntry]`
- **Description:** Return an existing runbook that covers the same error_class + task_type pair.

#### `find_by_error_class`

```python
def find_by_error_class(self, error_class: str) -> Optional[RunbookEntry]
```

- **Returns:** `Optional[RunbookEntry]`
- **Description:** Return best (most successes) runbook for the given error class.

#### `find_by_task_type`

```python
def find_by_task_type(self, task_type: str) -> List[RunbookEntry]
```

- **Returns:** `List[RunbookEntry]`

#### `search`

```python
def search(self, query: str) -> List[RunbookEntry]
```

- **Returns:** `List[RunbookEntry]`
- **Description:** Full-text search across title, keywords, trigger_symptoms, steps.

#### `record_outcome`

```python
def record_outcome(self, runbook_id: str, *, success: bool, tokens_used: float = 0.0) -> Optional[RunbookEntry]
```

- **Returns:** `Optional[RunbookEntry]`
- **Description:** Update success/failure counts and rolling avg cost.

#### `reload`

```python
def reload(self) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.runbook_generator.RunbookEntry`

**Bases:** object

A reusable runbook artifact derived from a successful task episode.

#### `success_rate_str`

```python
def success_rate_str(self) -> str
```

- **Returns:** `str`

#### `slug`

```python
def slug(self) -> str
```

- **Returns:** `str`

### `tokenpak.orchestration.skill_compiler.ExtractedSkill`

**Bases:** object

A promoted skill derived from repeated successful episodes.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'ExtractedSkill'
```

- **Returns:** `'ExtractedSkill'`

### `tokenpak.orchestration.skill_compiler.PatternStats`

**Bases:** object

#### `successful_episodes`

```python
def successful_episodes(self) -> List[SkillEpisode]
```

- **Returns:** `List[SkillEpisode]`

#### `success_rate`

```python
def success_rate(self) -> float
```

- **Returns:** `float`

#### `avg_tokens_original`

```python
def avg_tokens_original(self) -> float
```

- **Returns:** `float`

#### `avg_tokens_skill`

```python
def avg_tokens_skill(self) -> float
```

- **Returns:** `float`

#### `avg_token_savings`

```python
def avg_token_savings(self) -> float
```

- **Returns:** `float`

#### `recent_failures`

```python
def recent_failures(self) -> List[SkillEpisode]
```

- **Returns:** `List[SkillEpisode]`

#### `contradicted_by_recent_failures`

```python
def contradicted_by_recent_failures(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.skill_compiler.SkillCompiler`

**Bases:** object

Detect repeated successful episodes and promote them into reusable skills.

#### `__init__`

```python
def __init__(self, store: Optional[SkillStore] = None, recent_failure_window: int = RECENT_FAILURE_WINDOW) -> None
```

- **Returns:** `None`

#### `record_episode`

```python
def record_episode(self, episode: SkillEpisode) -> Optional[ExtractedSkill]
```

- **Returns:** `Optional[ExtractedSkill]`

#### `pattern_stats`

```python
def pattern_stats(self, pattern_key: Optional[str] = None) -> Dict[str, PatternStats]
```

- **Returns:** `Dict[str, PatternStats]`

#### `detect_repeated_patterns`

```python
def detect_repeated_patterns(self) -> List[PatternStats]
```

- **Returns:** `List[PatternStats]`

#### `should_promote`

```python
def should_promote(self, stats: PatternStats) -> bool
```

- **Returns:** `bool`

#### `maybe_promote`

```python
def maybe_promote(self, pattern_key: str) -> Optional[ExtractedSkill]
```

- **Returns:** `Optional[ExtractedSkill]`

#### `compile_skill`

```python
def compile_skill(self, stats: PatternStats) -> ExtractedSkill
```

- **Returns:** `ExtractedSkill`

### `tokenpak.orchestration.skill_compiler.SkillEpisode`

**Bases:** object

A completed task execution that may contribute to skill extraction.

#### `normalized_pattern`

```python
def normalized_pattern(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `pattern_key`

```python
def pattern_key(self) -> str
```

- **Returns:** `str`

#### `counted_success`

```python
def counted_success(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.skill_compiler.SkillStore`

**Bases:** object

Persistent storage for extracted skills and their macro registrations.

#### `__init__`

```python
def __init__(self, skills_dir: Optional[Path | str] = None, macro_engine: Optional[MacroEngine] = None, index_path: Optional[Path | str] = None) -> None
```

- **Returns:** `None`

#### `register_with_macro_engine`

```python
def register_with_macro_engine(self, skill: ExtractedSkill, overwrite: bool = True) -> Path
```

- **Returns:** `Path`
- **Description:** Register extracted skill with macro engine, converting step format as needed.

#### `save`

```python
def save(self, skill: ExtractedSkill, overwrite: bool = True) -> Path
```

- **Returns:** `Path`

#### `get`

```python
def get(self, skill_id: str) -> Optional[ExtractedSkill]
```

- **Returns:** `Optional[ExtractedSkill]`

#### `list_all`

```python
def list_all(self) -> List[ExtractedSkill]
```

- **Returns:** `List[ExtractedSkill]`

#### `execute`

```python
def execute(self, skill_id: str, variables: Optional[Dict[str, Any]] = None, dry_run: bool = False) -> MacroResult
```

- **Returns:** `MacroResult`

### `tokenpak.orchestration.state_collector.StateCollector`

**Bases:** object

Collects structured environment facts before LLM reasoning.

Usage::

    collector = StateCollector(cwd="/path/to/project")
    state = collector.collect_all()
    print(state.to_json())

#### `__init__`

```python
def __init__(self, cwd: Optional[str] = None, known_good_env: Optional[Dict[str, str]] = None, pytest_results_path: Optional[str] = None) -> Any
```

- **Returns:** `Any`

#### `collect_git_state`

```python
def collect_git_state(self) -> GitState
```

- **Returns:** `GitState`
- **Description:** Collect git branch, uncommitted files, and remote diff.

#### `collect_service_state`

```python
def collect_service_state(self) -> ServiceState
```

- **Returns:** `ServiceState`
- **Description:** Collect running processes and open ports of interest.

#### `collect_env_state`

```python
def collect_env_state(self) -> EnvState
```

- **Returns:** `EnvState`
- **Description:** Collect relevant env vars and detect drift from known-good baseline.

#### `collect_file_state`

```python
def collect_file_state(self) -> FileState
```

- **Returns:** `FileState`
- **Description:** Collect recently changed files (last 10 min) in cwd.

#### `collect_test_state`

```python
def collect_test_state(self) -> TestState
```

- **Returns:** `TestState`
- **Description:** Collect last pytest results from .pytest_cache or results file.

#### `collect_all`

```python
def collect_all(self) -> StructuredState
```

- **Returns:** `StructuredState`
- **Description:** Collect all subsystem states into a single compact StructuredState.

### `tokenpak.orchestration.state_collector.StructuredState`

**Bases:** object

#### `is_stale`

```python
def is_stale(self, threshold: float = STALE_THRESHOLD_SECONDS) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if state is older than threshold seconds.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `to_json`

```python
def to_json(self, compact: bool = True) -> str
```

- **Returns:** `str`

#### `token_estimate`

```python
def token_estimate(self) -> int
```

- **Returns:** `int`
- **Description:** Rough token estimate (~4 chars per token).

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'StructuredState'
```

- **Returns:** `'StructuredState'`

### `tokenpak.orchestration.team.agent_registry.AgentRecord`

**Bases:** object

A registered team agent.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'AgentRecord'
```

- **Returns:** `'AgentRecord'`

### `tokenpak.orchestration.team.agent_registry.AgentRegistry`

**Bases:** object

Thread-safe registry for team agents.

Persists to a JSON file. A background thread marks stale agents.

Usage::

    registry = AgentRegistry("~/.tokenpak/team/agents.json")
    registry.register("cali", capabilities=["compression", "tools"])
    registry.heartbeat("cali")
    agents = registry.list_agents()
    registry.start_health_checker()

#### `__init__`

```python
def __init__(self, store_path: str = ':memory:', stale_timeout: float = STALE_TIMEOUT_SECONDS) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, name: str, capabilities: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None) -> AgentRecord
```

- **Returns:** `AgentRecord`
- **Description:** Register or re-register an agent.

#### `heartbeat`

```python
def heartbeat(self, name: str) -> bool
```

- **Returns:** `bool`
- **Description:** Update last_heartbeat for an agent; marks online if was stale.

#### `deregister`

```python
def deregister(self, name: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove an agent from the registry.

#### `get`

```python
def get(self, name: str) -> Optional[AgentRecord]
```

- **Returns:** `Optional[AgentRecord]`

#### `list_agents`

```python
def list_agents(self) -> List[AgentRecord]
```

- **Returns:** `List[AgentRecord]`
- **Description:** Return all agents (with current status).

#### `list_agents_dict`

```python
def list_agents_dict(self) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Return agents as serialisable dicts (for API responses).

#### `mark_stale`

```python
def mark_stale(self) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Check all agents; mark stale if heartbeat has timed out.

#### `start_health_checker`

```python
def start_health_checker(self, interval: float = 15.0) -> None
```

- **Returns:** `None`
- **Description:** Start background thread that periodically marks stale agents.

#### `stop_health_checker`

```python
def stop_health_checker(self) -> None
```

- **Returns:** `None`

#### `stats`

```python
def stats(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.orchestration.team.shared_vault.SharedVault`

**Bases:** object

JSON-backed shared vault for team context blocks.

Merge strategy (team blocks lower priority than local)::

    merged = merge_with_local(local_blocks)
    # local_blocks override team blocks at the same path

Usage::

    vault = SharedVault("~/.tokenpak/team/shared_vault.json")
    vault.push_block(block)
    blocks = vault.pull_blocks()
    merged = vault.merge_with_local(local_blocks)

#### `__init__`

```python
def __init__(self, store_path: str = ':memory:') -> None
```

- **Returns:** `None`

#### `push_block`

```python
def push_block(self, block: SharedVaultBlock) -> None
```

- **Returns:** `None`
- **Description:** Add or update a block in the shared vault.

#### `push_blocks`

```python
def push_blocks(self, blocks: List[SharedVaultBlock]) -> int
```

- **Returns:** `int`
- **Description:** Bulk push; returns count of blocks stored.

#### `pull_blocks`

```python
def pull_blocks(self, contributor: Optional[str] = None) -> List[SharedVaultBlock]
```

- **Returns:** `List[SharedVaultBlock]`
- **Description:** Return all blocks (or only from a specific contributor).

#### `get_block`

```python
def get_block(self, block_id: str) -> Optional[SharedVaultBlock]
```

- **Returns:** `Optional[SharedVaultBlock]`

#### `delete_block`

```python
def delete_block(self, block_id: str) -> bool
```

- **Returns:** `bool`

#### `merge_with_local`

```python
def merge_with_local(self, local_blocks: List[Any]) -> List[Any]
```

- **Returns:** `List[Any]`
- **Description:** Merge team blocks with local blocks.

#### `search`

```python
def search(self, query: str, top_k: int = 10) -> List[SharedVaultBlock]
```

- **Returns:** `List[SharedVaultBlock]`
- **Description:** Naive keyword search over compressed content.

#### `stats`

```python
def stats(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.orchestration.team.shared_vault.SharedVaultBlock`

**Bases:** object

A block contributed to the shared team vault.

#### `compression_ratio`

```python
def compression_ratio(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'SharedVaultBlock'
```

- **Returns:** `'SharedVaultBlock'`

### `tokenpak.orchestration.team.templates.Template`

**Bases:** object

A shared team prompt template.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'Template'
```

- **Returns:** `'Template'`

#### `render`

```python
def render(self, variables: Optional[Dict[str, str]] = None) -> str
```

- **Returns:** `str`
- **Description:** Render template with optional variable substitution ({{var}} syntax).

### `tokenpak.orchestration.team.templates.TemplateStore`

**Bases:** object

JSON-backed store for team templates with RBAC.

Usage::

    store = TemplateStore("~/.tokenpak/team/templates.json")
    store.create("summarise", "Summarise this: {{content}}", created_by="admin", actor_role="admin")
    templates = store.list_templates()
    template = store.get("summarise")
    rendered = template.render({"content": "..."})

#### `__init__`

```python
def __init__(self, store_path: str = ':memory:') -> None
```

- **Returns:** `None`

#### `create`

```python
def create(self, name: str, content: str, created_by: str, actor_role: str = ROLE_ADMIN, description: str = '', tags: Optional[List[str]] = None, role_required: str = ROLE_MEMBER, metadata: Optional[Dict[str, Any]] = None) -> Template
```

- **Returns:** `Template`
- **Description:** Create a new template (admin only).

#### `update`

```python
def update(self, name: str, content: Optional[str] = None, description: Optional[str] = None, tags: Optional[List[str]] = None, actor_role: str = ROLE_ADMIN) -> Template
```

- **Returns:** `Template`
- **Description:** Update an existing template (admin only).

#### `delete`

```python
def delete(self, name: str, actor_role: str = ROLE_ADMIN) -> bool
```

- **Returns:** `bool`
- **Description:** Delete a template (admin only).

#### `get`

```python
def get(self, name: str, actor_role: str = ROLE_MEMBER) -> Optional[Template]
```

- **Returns:** `Optional[Template]`
- **Description:** Retrieve a template by name (any team member).

#### `list_templates`

```python
def list_templates(self, actor_role: str = ROLE_MEMBER, tag: Optional[str] = None) -> List[Template]
```

- **Returns:** `List[Template]`
- **Description:** List templates visible to actor (respects role_required).

#### `use`

```python
def use(self, name: str, variables: Optional[Dict[str, str]] = None, actor_role: str = ROLE_MEMBER) -> str
```

- **Returns:** `str`
- **Description:** Fetch a template and render it with optional variables.

#### `stats`

```python
def stats(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.orchestration.triggers.daemon.TriggerDaemon`

**Bases:** object

Watches file system, timers, and cost thresholds; fires matching triggers.

#### `__init__`

```python
def __init__(self, store: Optional[TriggerStore] = None) -> Any
```

- **Returns:** `Any`

#### `run`

```python
def run(self) -> None
```

- **Returns:** `None`
- **Description:** Block and run daemon until stop() is called.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`

### `tokenpak.orchestration.triggers.store.TriggerStore`

**Bases:** object

Load/save triggers from YAML config.

#### `__init__`

```python
def __init__(self, config_path: Path = DEFAULT_CONFIG) -> Any
```

- **Returns:** `Any`

#### `list`

```python
def list(self) -> List[Trigger]
```

- **Returns:** `List[Trigger]`

#### `add`

```python
def add(self, event: str, action: str) -> Trigger
```

- **Returns:** `Trigger`

#### `remove`

```python
def remove(self, trigger_id: str) -> bool
```

- **Returns:** `bool`

#### `get`

```python
def get(self, trigger_id: str) -> Optional[Trigger]
```

- **Returns:** `Optional[Trigger]`

#### `log_fire`

```python
def log_fire(self, trigger: Trigger, exit_code: int, output: str) -> None
```

- **Returns:** `None`

#### `list_logs`

```python
def list_logs(self, limit: int = 20) -> List[TriggerLog]
```

- **Returns:** `List[TriggerLog]`

### `tokenpak.orchestration.validation_framework.FileStateValidator`

**Bases:** PostActionValidator

Validate that expected files exist, were modified, or match content patterns.

Args:
    must_exist:   List of paths that must exist after the action.
    must_not_exist: List of paths that must NOT exist.
    must_be_newer_than: Dict[path, timestamp] — file mtime must be >= timestamp.
    content_patterns: Dict[path, regex] — file must contain a match for regex.

#### `__init__`

```python
def __init__(self, must_exist: Optional[List[str]] = None, must_not_exist: Optional[List[str]] = None, must_be_newer_than: Optional[Dict[str, float]] = None, content_patterns: Optional[Dict[str, str]] = None) -> Any
```

- **Returns:** `Any`

#### `validate`

```python
def validate(self, action_result: dict, expected: dict) -> ValidationResult
```

- **Returns:** `ValidationResult`

### `tokenpak.orchestration.validation_framework.PostActionValidator`

**Bases:** ABC

Base class for all post-action validators.

#### `name`

```python
def name(self) -> str
```

- **Returns:** `str`

#### `validate`

```python
def validate(self, action_result: dict, expected: dict) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Run validation checks and return a ValidationResult.

### `tokenpak.orchestration.validation_framework.SchemaValidator`

**Bases:** PostActionValidator

Validate that action_result matches an expected JSON schema (structural subset).

This is a lightweight structural validator — it does NOT require jsonschema.
It checks required keys, type hints, and optional allowed values.

Args:
    schema: Dict describing expected structure::

        {
            "required_keys": ["status", "id"],
            "types": {"status": str, "id": int},
            "allowed_values": {"status": ["ok", "pending"]},
            "disallowed_keys": ["error"],
        }

#### `__init__`

```python
def __init__(self, schema: Dict[str, Any]) -> Any
```

- **Returns:** `Any`

#### `validate`

```python
def validate(self, action_result: dict, expected: dict) -> ValidationResult
```

- **Returns:** `ValidationResult`

### `tokenpak.orchestration.validation_framework.ServiceHealthValidator`

**Bases:** PostActionValidator

Validate that a service is reachable and healthy.

Checks HTTP health endpoint and/or process presence.

Args:
    url:         HTTP(S) URL to poll (e.g. ``http://localhost:8080/health``).
                 If *None*, the HTTP check is skipped.
    process_name: Substring to match in running process list via ``pgrep``.
                  If *None*, the process check is skipped.
    timeout:     HTTP request timeout in seconds (default 5).
    expected_status: Expected HTTP status code (default 200).

#### `__init__`

```python
def __init__(self, url: Optional[str] = None, process_name: Optional[str] = None, timeout: int = 5, expected_status: int = 200) -> Any
```

- **Returns:** `Any`

#### `validate`

```python
def validate(self, action_result: dict, expected: dict) -> ValidationResult
```

- **Returns:** `ValidationResult`

### `tokenpak.orchestration.validation_framework.TestSuiteValidator`

**Bases:** PostActionValidator

Validate by running a test suite and comparing pass/fail counts.

Args:
    command:      Shell command to run tests (e.g. ``["pytest", "tests/"]``).
    min_pass_pct: Minimum percentage of tests that must pass (0–100, default 100).
    cwd:          Working directory for the test command.
    timeout:      Max seconds to wait for tests (default 120).

#### `__init__`

```python
def __init__(self, command: List[str], min_pass_pct: float = 100.0, cwd: Optional[str] = None, timeout: int = 120) -> Any
```

- **Returns:** `Any`

#### `validate`

```python
def validate(self, action_result: dict, expected: dict) -> ValidationResult
```

- **Returns:** `ValidationResult`

### `tokenpak.orchestration.validation_framework.ValidationOrchestrator`

**Bases:** object

Register validators per workflow step and run them after each step completes.

Example::

    orch = ValidationOrchestrator()
    orch.register_step_validator("build", FileStateValidator(must_exist=["dist/app.whl"]))
    orch.register_step_validator("deploy", ServiceHealthValidator(url="http://localhost/health"))

    result = orch.validate_step("deploy", action_result={...}, expected={})
    if not result.passed:
        orch.handle_failure("deploy", result)  # auto-retry or raise

Args:
    retry_policy: Controls how many retries occur on failure.
    on_escalate:  Optional callback called when retries are exhausted.
                  Signature: ``(step_name: str, result: ValidationResult) -> None``

#### `__init__`

```python
def __init__(self, retry_policy: Optional[RetryPolicy] = None, on_escalate: Optional[Callable[[str, ValidationResult], None]] = None) -> Any
```

- **Returns:** `Any`

#### `register_step_validator`

```python
def register_step_validator(self, step_name: str, validator: PostActionValidator) -> None
```

- **Returns:** `None`
- **Description:** Attach a validator to a workflow step (multiple validators per step allowed).

#### `validate_step`

```python
def validate_step(self, step_name: str, action_result: dict, expected: dict) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Run all validators registered for *step_name* and merge results.

#### `handle_failure`

```python
def handle_failure(self, step_name: str, result: ValidationResult, retry_fn: Optional[Callable[[], dict]] = None, expected: Optional[dict] = None) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Raises:** `ValidationError`
- **Description:** Handle a failed validation with automatic retry and optional escalation.

#### `validation_history`

```python
def validation_history(self) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Return list of past validation events (step, passed, timestamp).

### `tokenpak.orchestration.validation_framework.ValidationResult`

**Bases:** object

Aggregate result from a PostActionValidator.validate() call.

#### `summary`

```python
def summary(self) -> str
```

- **Returns:** `str`

#### `failed_checks`

```python
def failed_checks(self) -> List[ValidationCheck]
```

- **Returns:** `List[ValidationCheck]`

### `tokenpak.orchestration.workflow.WorkflowManager`

**Bases:** object

#### `__init__`

```python
def __init__(self, workflow_dir = DEFAULT_WORKFLOW_DIR) -> Any
```

- **Returns:** `Any`

#### `load`

```python
def load(self, wf_id) -> Any
```

- **Returns:** `Any`

#### `list_workflows`

```python
def list_workflows(self, status = None, tags = None, limit = None) -> Any
```

- **Returns:** `Any`

#### `incomplete_workflows`

```python
def incomplete_workflows(self) -> Any
```

- **Returns:** `Any`

#### `create`

```python
def create(self, name, steps = None, template = None, metadata = None, tags = None, wf_id = None) -> Any
```

- **Returns:** `Any`

#### `start`

```python
def start(self, wf_id) -> Any
```

- **Returns:** `Any`

#### `begin_step`

```python
def begin_step(self, wf_id, step_name) -> Any
```

- **Returns:** `Any`

#### `complete_step`

```python
def complete_step(self, wf_id, step_name, output = None) -> Any
```

- **Returns:** `Any`

#### `fail_step`

```python
def fail_step(self, wf_id, step_name, error, skip_dependents = True) -> Any
```

- **Returns:** `Any`

#### `skip_step`

```python
def skip_step(self, wf_id, step_name, reason = '') -> Any
```

- **Returns:** `Any`

#### `cancel`

```python
def cancel(self, wf_id) -> Any
```

- **Returns:** `Any`

#### `pause`

```python
def pause(self, wf_id) -> Any
```

- **Returns:** `Any`

#### `resume`

```python
def resume(self, wf_id) -> Any
```

- **Returns:** `Any`

#### `delete`

```python
def delete(self, wf_id) -> Any
```

- **Returns:** `Any`

#### `run`

```python
def run(self, wf_id, handlers, on_step_start = None, on_step_done = None) -> Any
```

- **Returns:** `Any`

#### `history`

```python
def history(self, limit = 20, name_filter = None) -> Any
```

- **Returns:** `Any`

### `tokenpak.orchestration.workflow.WorkflowRecord`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Any
```

- **Returns:** `Any`

#### `from_dict`

```python
def from_dict(cls, d) -> Any
```

- **Returns:** `Any`

#### `completion_pct`

```python
def completion_pct(self) -> Any
```

- **Returns:** `Any`

#### `current_step`

```python
def current_step(self) -> Any
```

- **Returns:** `Any`

#### `next_pending_step`

```python
def next_pending_step(self) -> Any
```

- **Returns:** `Any`

#### `duration_seconds`

```python
def duration_seconds(self) -> Any
```

- **Returns:** `Any`

### `tokenpak.orchestration.workflow.WorkflowStep`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Any
```

- **Returns:** `Any`

#### `from_dict`

```python
def from_dict(cls, d) -> Any
```

- **Returns:** `Any`

#### `duration_seconds`

```python
def duration_seconds(self) -> Any
```

- **Returns:** `Any`

#### `is_done`

```python
def is_done(self) -> Any
```

- **Returns:** `Any`

#### `is_terminal`

```python
def is_terminal(self) -> Any
```

- **Returns:** `Any`

### `tokenpak.orchestration.workflow_budget.BudgetEvent`

**Bases:** object

#### `is_warning`

```python
def is_warning(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.orchestration.workflow_budget.WorkflowBudget`

**Bases:** object

Dynamic token-budget manager for a sequence of workflow steps.

Args:
    total:      Total token budget for the entire workflow.
    steps:      Ordered list of step names (execution order).
    min_floor:  Minimum tokens guaranteed per pending step (default 100).
    warn_pct:   Overspend fraction that triggers a warning (default 1.20 = 120%).
    critical_pct: Remaining-budget fraction that triggers a critical alert
                  (default 0.20 = 20% of total remaining is critical).

#### `__init__`

```python
def __init__(self, total: int, steps: Sequence[str], min_floor: int = MIN_FLOOR_TOKENS, warn_pct: float = WARN_OVERSPEND_PCT, critical_pct: float = CRITICAL_REMAINING_PCT) -> None
```

- **Returns:** `None`

#### `total`

```python
def total(self) -> int
```

- **Returns:** `int`

#### `remaining`

```python
def remaining(self) -> int
```

- **Returns:** `int`

#### `pending_steps`

```python
def pending_steps(self) -> List[str]
```

- **Returns:** `List[str]`

#### `completed_steps`

```python
def completed_steps(self) -> List[str]
```

- **Returns:** `List[str]`

#### `step_allocation`

```python
def step_allocation(self, step: str) -> int
```

- **Returns:** `int`
- **Description:** Return current token allocation for *step*.

#### `step_usage`

```python
def step_usage(self, step: str) -> Optional[int]
```

- **Returns:** `Optional[int]`
- **Description:** Return recorded usage for *step*, or None if not yet recorded.

#### `record_usage`

```python
def record_usage(self, step: str, tokens_used: int) -> List[BudgetEvent]
```

- **Returns:** `List[BudgetEvent]`
- **Raises:** `KeyError`, `ValueError`, `ValueError`
- **Description:** Record actual token usage for a completed step and rebalance.

#### `snapshot`

```python
def snapshot(self) -> Dict
```

- **Returns:** `Dict`
- **Description:** Return a summary dict of current budget state.

### `tokenpak.orchestration.workflow_performance.WorkflowPerformanceTracker`

**Bases:** object

Persist and query per-template workflow performance statistics.

#### `__init__`

```python
def __init__(self, stats_path: Path = STATS_PATH) -> None
```

- **Returns:** `None`

#### `record`

```python
def record(self, template: str, success: bool, duration_seconds: float, tokens_used: int = 0, regression: bool = False) -> WorkflowStats
```

- **Returns:** `WorkflowStats`
- **Description:** Record the outcome of a single workflow execution.

#### `score_template`

```python
def score_template(self, template: str, *, max_duration: float = 300.0, max_tokens: int = 50000) -> float
```

- **Returns:** `float`
- **Description:** Compute a scalar score in [0.0, 1.0] for *template*.

#### `rank_templates`

```python
def rank_templates(self, task_type: str, *, candidates: Optional[Sequence[str]] = None, max_duration: float = 300.0, max_tokens: int = 50000) -> List[Tuple[str, float]]
```

- **Returns:** `List[Tuple[str, float]]`
- **Description:** Return templates sorted by score, highest first.

#### `get_stats`

```python
def get_stats(self, template: str) -> Optional[WorkflowStats]
```

- **Returns:** `Optional[WorkflowStats]`
- **Description:** Return :class:`WorkflowStats` for *template*, or ``None``.

#### `all_stats`

```python
def all_stats(self) -> Dict[str, WorkflowStats]
```

- **Returns:** `Dict[str, WorkflowStats]`
- **Description:** Return a copy of all tracked stats.

### `tokenpak.orchestration.workflow_performance.WorkflowStats`

**Bases:** object

Aggregate counters for a single workflow template.

#### `total_runs`

```python
def total_runs(self) -> int
```

- **Returns:** `int`

#### `success_rate`

```python
def success_rate(self) -> float
```

- **Returns:** `float`
- **Description:** Fraction of runs that succeeded (0.0–1.0).  0.0 when no data.

#### `avg_duration`

```python
def avg_duration(self) -> float
```

- **Returns:** `float`
- **Description:** Mean execution time in seconds across all runs.

#### `avg_tokens`

```python
def avg_tokens(self) -> float
```

- **Returns:** `float`
- **Description:** Mean token count across all runs.

#### `regression_rate`

```python
def regression_rate(self) -> float
```

- **Returns:** `float`
- **Description:** Fraction of successful runs that had a regression (0.0–1.0).

#### `to_dict`

```python
def to_dict(self) -> Dict
```

- **Returns:** `Dict`

#### `from_dict`

```python
def from_dict(cls, d: Dict) -> 'WorkflowStats'
```

- **Returns:** `'WorkflowStats'`

### `tokenpak.plugins.base.CompressorPlugin`

**Bases:** ABC

Abstract base class for custom compressor plugins.

Subclass this and implement ``compress()`` to create a plugin.
Register via ``TOKENPAK_PLUGINS`` env var or ``config.yaml`` ``plugins.enabled`` key.

#### `compress`

```python
def compress(self, text: str, context: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Compress *text* and return a result dict.

#### `priority`

```python
def priority(self) -> int
```

- **Returns:** `int`
- **Description:** Execution priority.  Higher number runs first.  Default: 50.

### `tokenpak.plugins.registry.PluginRegistry`

**Bases:** object

Registry for CompressorPlugin subclasses.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, plugin_cls: Type[CompressorPlugin]) -> None
```

- **Returns:** `None`
- **Raises:** `ValueError`
- **Description:** Register a plugin class (instantiates it immediately).

#### `discover`

```python
def discover(self) -> None
```

- **Returns:** `None`
- **Description:** Load plugins from env var and config file.

#### `get_plugins`

```python
def get_plugins(self) -> List[CompressorPlugin]
```

- **Returns:** `List[CompressorPlugin]`
- **Description:** Return plugins sorted by priority, highest first.

### `tokenpak.prove.adapter.ArmConfig`

**Bases:** object

Configuration for one arm of a prove run.

#### `resolve`

```python
def resolve(self) -> 'ArmConfig'
```

- **Returns:** `'ArmConfig'`
- **Description:** Fill in blanks from the provider registry.

### `tokenpak.prove.adapter.ArmResult`

**Bases:** object

Aggregate results from one arm.

#### `finalize`

```python
def finalize(self) -> None
```

- **Returns:** `None`

### `tokenpak.prove.display.LiveDisplay`

**Bases:** object

Manage live display of two arm log files.

Automatically picks the best available method with zero user interaction.

#### `__init__`

```python
def __init__(self, arm_a_log: Path, arm_b_log: Path) -> None
```

- **Returns:** `None`

#### `start`

```python
def start(self) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Start the live display. Returns a description, or None if skipped.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Clean up — close panes we opened, terminate subprocesses.

### `tokenpak.prove.scenario.Scenario`

**Bases:** object

A parsed prove scenario.

The ``matrix`` field defines which (platform, provider, model)
combinations to test.  When empty, the runner falls back to the
legacy two-arm (direct vs proxy) behaviour using ``model`` and
``provider`` from the top-level frontmatter.

Matrix format in frontmatter::

    matrix:
      - name: Sonnet Direct
        platform: api
        provider: anthropic
        model: claude-sonnet-4-6
      - name: Sonnet + TokenPak
        platform: proxy
        provider: anthropic
        model: claude-sonnet-4-6
      - name: GPT-4o Direct
        platform: api
        provider: openai
        model: gpt-4o

#### `from_file`

```python
def from_file(cls, path: Path) -> 'Scenario'
```

- **Returns:** `'Scenario'`
- **Description:** Parse a scenario .md file.

### `tokenpak.proxy.__init__.ProxyStats`

**Bases:** object

Stats/metrics container — resets on each new instance (restart).

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

### `tokenpak.proxy.__init__.TokenPakProxy`

**Bases:** object

TokenPak proxy entry point (stub for test surface).

#### `__init__`

```python
def __init__(self, config: Any = None) -> None
```

- **Returns:** `None`

### `tokenpak.proxy.adapters.anthropic_adapter.AnthropicAdapter`

**Bases:** FormatAdapter

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`

#### `normalize`

```python
def normalize(self, body: bytes) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`

#### `denormalize`

```python
def denormalize(self, canonical: CanonicalRequest) -> bytes
```

- **Returns:** `bytes`

#### `inject_system_context`

```python
def inject_system_context(self, body: bytes, injection_text: str) -> bytes
```

- **Returns:** `bytes`
- **Description:** Inject volatile content into the system prompt with correct cache boundary.

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `get_sse_format`

```python
def get_sse_format(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.base.FormatAdapter`

**Bases:** ABC

Abstract format adapter for provider-specific payloads.

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`

#### `normalize`

```python
def normalize(self, body: bytes) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`

#### `denormalize`

```python
def denormalize(self, canonical: CanonicalRequest) -> bytes
```

- **Returns:** `bytes`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `get_sse_format`

```python
def get_sse_format(self) -> str
```

- **Returns:** `str`

#### `extract_request_tokens`

```python
def extract_request_tokens(self, body: bytes, token_counter: Optional[TokenCounter] = None) -> Tuple[str, int]
```

- **Returns:** `Tuple[str, int]`

#### `extract_response_tokens`

```python
def extract_response_tokens(self, body: bytes, is_sse: bool = False) -> int
```

- **Returns:** `int`

#### `extract_query_signal`

```python
def extract_query_signal(self, body: bytes) -> str
```

- **Returns:** `str`

#### `inject_system_context`

```python
def inject_system_context(self, body: bytes, injection_text: str) -> bytes
```

- **Returns:** `bytes`

### `tokenpak.proxy.adapters.embedding_base.EmbeddingAdapter`

**Bases:** ABC

Abstract embedding adapter for provider-specific embedding payloads.

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if this adapter should handle the given request.

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert a canonical embedding request to provider-specific form.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Convert a provider-specific response body to the canonical OpenAI embedding format.

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`
- **Description:** Return the default upstream base URL for this provider.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`
- **Description:** Return the environment variable name that holds this provider's API key.

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`
- **Description:** Return the default embedding model identifier for this provider.

#### `is_available`

```python
def is_available(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the provider's API key is present in the environment.

### `tokenpak.proxy.adapters.embedding_router.EmbeddingRouter`

**Bases:** object

Routes embedding requests to the best available provider.

Priority order (highest first): Voyage > OpenAI > Gemini > Jina > Ollama.
Provider availability is determined at startup by checking env vars via
each adapter's is_available() method.  Re-discovery is triggered on SIGHUP.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `discover_providers`

```python
def discover_providers(self) -> List[EmbeddingAdapter]
```

- **Returns:** `List[EmbeddingAdapter]`
- **Description:** Check each adapter's is_available() and rebuild the priority list.

#### `resolve_model`

```python
def resolve_model(self, requested_model: str, input_texts: List[str] | None = None) -> Tuple[str, EmbeddingAdapter]
```

- **Returns:** `Tuple[str, EmbeddingAdapter]`
- **Description:** Resolve a model name to a (resolved_model_string, adapter) pair.

#### `get_providers_status`

```python
def get_providers_status(self) -> List[Dict[str, object]]
```

- **Returns:** `List[Dict[str, object]]`
- **Description:** Return status of all embedding providers.

#### `handle_request`

```python
def handle_request(self, path: str, headers: Dict[str, str], body: bytes) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Raises:** `RuntimeError`, `ValueError`, `ValueError`
- **Description:** Parse, resolve, and normalise an incoming embedding request.

### `tokenpak.proxy.adapters.embedding_voyage.EmbeddingVoyageAdapter`

**Bases:** EmbeddingAdapter

Embedding adapter for Voyage AI.

Detection strategy: matches requests whose path is ``/v1/embeddings`` when
``VOYAGE_API_KEY`` is present in the environment.

Field mapping (canonical → Voyage):
    dimensions      → output_dimension
    input_type      → input_type  (passed through verbatim)
    truncate        → truncation
    encoding_format → encoding_format  (passed through verbatim)

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True when path is /v1/embeddings and VOYAGE_API_KEY is set.

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert canonical embedding request to Voyage AI wire format.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Convert Voyage response body to OpenAI-compatible embedding format.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.gemini_embedding_adapter.GeminiEmbeddingAdapter`

**Bases:** EmbeddingAdapter

Embedding adapter for Google Gemini.

Single input:  POST /v1beta/models/{model}:embedContent
               Body: {content: {parts: [{text: ...}]}}
               Response: {embedding: {values: [...]}}

Batch input:   POST /v1beta/models/{model}:batchEmbedContents
               Body: {requests: [{model: "models/{model}", content: {parts: [{text: ...}]}}, ...]}
               Response: {embeddings: [{values: [...]}, ...]}

Auth: query param ?key=<GEMINI_API_KEY>  (NOT Authorization header)

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True when the request targets a Gemini embedding model.

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert a canonical embedding request to Gemini wire format.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Convert a Gemini embedContent or batchEmbedContents response to OpenAI format.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.google_adapter.GoogleGenerativeAIAdapter`

**Bases:** FormatAdapter

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`

#### `normalize`

```python
def normalize(self, body: bytes) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`

#### `denormalize`

```python
def denormalize(self, canonical: CanonicalRequest) -> bytes
```

- **Returns:** `bytes`

#### `extract_response_tokens`

```python
def extract_response_tokens(self, body: bytes, is_sse: bool = False) -> int
```

- **Returns:** `int`

#### `extract_input_tokens`

```python
def extract_input_tokens(self, body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract prompt token count from Google usageMetadata.

#### `extract_total_tokens`

```python
def extract_total_tokens(self, body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract total token count from Google usageMetadata.

#### `detect_streaming`

```python
def detect_streaming(self, path: str) -> bool
```

- **Returns:** `bool`
- **Description:** Detect streaming from URL path or query parameters.

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `get_sse_format`

```python
def get_sse_format(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.grok_adapter.GrokAdapter`

**Bases:** FormatAdapter

Adapter for xAI Grok models via api.x.ai (OpenAI-compatible format).

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Detect Grok requests.

#### `normalize`

```python
def normalize(self, body: bytes) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`
- **Description:** Parse xAI/OpenAI chat completions payload into CanonicalRequest.

#### `denormalize`

```python
def denormalize(self, canonical: CanonicalRequest) -> bytes
```

- **Returns:** `bytes`
- **Description:** Serialise CanonicalRequest back to OpenAI-compatible JSON for xAI.

#### `extract_response_tokens`

```python
def extract_response_tokens(self, body: bytes, is_sse: bool = False) -> int
```

- **Returns:** `int`
- **Description:** Extract completion token count from xAI response (OpenAI schema).

#### `estimate_cost`

```python
def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> Dict[str, float]
```

- **Returns:** `Dict[str, float]`
- **Description:** Estimate cost in USD for a Grok API call.

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `get_sse_format`

```python
def get_sse_format(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.jina_embedding.JinaEmbeddingAdapter`

**Bases:** EmbeddingAdapter

Embedding adapter for Jina AI.

Detection: requests whose body model field starts with ``jina-embeddings-``.

Field mapping (canonical → Jina):
    input_type      → task  (via _INPUT_TYPE_TO_TASK; unknown values passed through)
    encoding_format → embedding_type
    truncate        → truncate  (passed through verbatim)
    normalized      → normalized  (passed through verbatim)

Auth: ``Authorization: Bearer $JINA_API_KEY``

Response normalisation:
    - data[].object = 'embedding' injected if missing
    - usage.total_tokens mirrors prompt_tokens (Jina omits total_tokens)

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the request targets a Jina embedding model.

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert canonical embedding request to Jina AI wire format.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Convert Jina response body to OpenAI-compatible embedding format.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.jina_embedding_adapter.JinaEmbeddingAdapter`

**Bases:** EmbeddingAdapter

Embedding adapter for Jina AI.

Field mapping (canonical → Jina):
    input_type      → task  (via _INPUT_TYPE_TO_TASK; unknown values passed through)
    encoding_format → embedding_type
    truncate        → truncate  (passed through verbatim)
    normalized      → normalized  (passed through verbatim)

Response normalisation:
    - data[].object = 'embedding'  injected if missing
    - usage.total_tokens mirrors prompt_tokens (Jina omits total_tokens)

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the request targets a Jina model (starts with 'jina-').

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert canonical embedding request to Jina AI wire format.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Convert Jina response body to OpenAI-compatible embedding format.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.ollama_embedding_adapter.OllamaEmbeddingAdapter`

**Bases:** EmbeddingAdapter

Embedding adapter for Ollama local inference.

Upstream: $TOKENPAK_OLLAMA_URL/v1/embeddings (default http://localhost:11434)
Auth:     None — Ollama requires no authentication
Schema:   OpenAI-compatible on both request and response sides

is_available() returns True when:
  - TOKENPAK_OLLAMA_URL is set in the environment, OR
  - the default Ollama URL responds to a HEAD/GET request

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the request targets a known Ollama embedding model.

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert a canonical embedding request to Ollama /v1/embeddings wire format.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Convert Ollama response to canonical OpenAI-compatible embedding format.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `is_available`

```python
def is_available(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if Ollama is reachable.

### `tokenpak.proxy.adapters.openai_chat_adapter.OpenAIChatAdapter`

**Bases:** FormatAdapter

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`

#### `normalize`

```python
def normalize(self, body: bytes) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`

#### `denormalize`

```python
def denormalize(self, canonical: CanonicalRequest) -> bytes
```

- **Returns:** `bytes`

#### `extract_input_tokens`

```python
def extract_input_tokens(self, body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract prompt token count from OpenAI usage field.

#### `extract_total_tokens`

```python
def extract_total_tokens(self, body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract total token count from OpenAI usage field.

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `get_sse_format`

```python
def get_sse_format(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.openai_codex_responses_adapter.OpenAICodexResponsesAdapter`

**Bases:** OpenAIResponsesAdapter

Codex Responses adapter — same format, different upstream + detection.

Key differences from standard OpenAI Responses:
- Upstream: chatgpt.com/backend-api (not api.openai.com)
- Path: /codex/responses (not /v1/responses)
- Requires: stream=true, store=false, no max_output_tokens
- Uses curl_cffi for Cloudflare bypass

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Match Codex requests by path or by JWT auth.

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `get_sse_format`

```python
def get_sse_format(self) -> str
```

- **Returns:** `str`

#### `get_upstream_path`

```python
def get_upstream_path(self) -> str
```

- **Returns:** `str`
- **Description:** Return the correct path for the ChatGPT Codex backend.

#### `denormalize`

```python
def denormalize(self, canonical: CanonicalRequest) -> bytes
```

- **Returns:** `bytes`
- **Description:** Denormalize with ChatGPT Codex constraints applied.

### `tokenpak.proxy.adapters.openai_embedding.OpenAIEmbeddingAdapter`

**Bases:** EmbeddingAdapter

Embedding adapter for OpenAI.

OpenAI's /v1/embeddings is the canonical format, so normalize_request is a
near-identity transform and normalize_response is a passthrough.

Upstream: https://api.openai.com/v1/embeddings
Auth:     Authorization: Bearer $OPENAI_API_KEY

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the request targets a known OpenAI embedding model.

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert a canonical embedding request to OpenAI /v1/embeddings wire format.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Return the response body unchanged — OpenAI response is already canonical format.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.openai_embedding_adapter.OpenAIEmbeddingAdapter`

**Bases:** EmbeddingAdapter

Embedding adapter for OpenAI.

OpenAI IS the canonical format, so normalize_request is near-identity
and normalize_response is a passthrough.

Upstream: https://api.openai.com/v1/embeddings
Auth:     Authorization: Bearer $OPENAI_API_KEY

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the request targets an OpenAI embedding model (starts with 'text-embedding-').

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert a canonical embedding request to OpenAI wire format.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Return the response body unchanged — OpenAI response is already canonical format.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.openai_responses_adapter.OpenAIResponsesAdapter`

**Bases:** FormatAdapter

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`

#### `normalize`

```python
def normalize(self, body: bytes) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`

#### `denormalize`

```python
def denormalize(self, canonical: CanonicalRequest) -> bytes
```

- **Returns:** `bytes`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `get_sse_format`

```python
def get_sse_format(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.passthrough_adapter.PassthroughAdapter`

**Bases:** FormatAdapter

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`

#### `normalize`

```python
def normalize(self, body: bytes) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`

#### `denormalize`

```python
def denormalize(self, canonical: CanonicalRequest) -> bytes
```

- **Returns:** `bytes`

#### `inject_system_context`

```python
def inject_system_context(self, body: bytes, injection_text: str) -> bytes
```

- **Returns:** `bytes`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

#### `get_sse_format`

```python
def get_sse_format(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.adapters.registry.AdapterRegistry`

**Bases:** object

Registry for provider format adapters.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, adapter: FormatAdapter, priority: int = 100) -> None
```

- **Returns:** `None`

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes] = None) -> FormatAdapter
```

- **Returns:** `FormatAdapter`

#### `list_formats`

```python
def list_formats(self) -> List[str]
```

- **Returns:** `List[str]`

#### `adapters`

```python
def adapters(self) -> List[FormatAdapter]
```

- **Returns:** `List[FormatAdapter]`

### `tokenpak.proxy.adapters.voyage_embedding_adapter.VoyageEmbeddingAdapter`

**Bases:** EmbeddingAdapter

Embedding adapter for Voyage AI.

Field mapping (canonical → Voyage):
    dimensions      → output_dimension
    input_type      → input_type  (passed through verbatim)
    truncate        → truncation
    encoding_format → encoding_format  (passed through verbatim)

#### `detect`

```python
def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the request targets a Voyage model (starts with 'voyage-').

#### `normalize_request`

```python
def normalize_request(self, canonical: CanonicalEmbeddingRequest) -> Tuple[str, Dict[str, str], bytes]
```

- **Returns:** `Tuple[str, Dict[str, str], bytes]`
- **Description:** Convert canonical embedding request to Voyage AI wire format.

#### `normalize_response`

```python
def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Convert Voyage response body to OpenAI-compatible embedding format.

#### `get_env_key_name`

```python
def get_env_key_name(self) -> str
```

- **Returns:** `str`

#### `get_default_model`

```python
def get_default_model(self) -> str
```

- **Returns:** `str`

#### `get_default_upstream`

```python
def get_default_upstream(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.admission.AgentConcurrencyGate`

**Bases:** object

FIFO concurrency gate with a dynamic cap and bounded queue.

``acquire`` admits up to ``effective_cap()`` requests concurrently;
later arrivals wait FIFO (bounded queue depth, bounded wait) and are
admitted strictly in arrival order as slots release. The cap is dynamic:
when ``degraded_probe`` reports a degraded local proxy the effective cap
drops to 1, serializing managed traffic during recovery without any
config change.

#### `__init__`

```python
def __init__(self, max_parallel: int, max_queue: int, degraded_probe: Optional[Callable[[], bool]] = None, source: str = 'config') -> None
```

- **Returns:** `None`

#### `effective_cap`

```python
def effective_cap(self) -> int
```

- **Returns:** `int`
- **Description:** Current cap: 1 while the local proxy is degraded, else configured.

#### `acquire`

```python
def acquire(self, wait_timeout: float = DEFAULT_QUEUE_WAIT_S) -> str
```

- **Returns:** `str`
- **Description:** Admit, queue-then-admit, or reject the calling request.

#### `release`

```python
def release(self) -> None
```

- **Returns:** `None`
- **Description:** Release an admitted slot and wake the head of the queue.

#### `snapshot`

```python
def snapshot(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Point-in-time gate state for /health, status, and doctor surfaces.

### `tokenpak.proxy.api.routes.HealthRoute`

**Bases:** object

Handles GET /health requests.

Parameters
----------
start_time : float, optional
    Proxy start time (Unix epoch).  Defaults to module import time if not
    provided — useful for standalone/test usage.
version : str, optional
    Override proxy version string.

#### `__init__`

```python
def __init__(self, start_time: Optional[float] = None, version: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `handle`

```python
def handle(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Run all health checks and return the response dict.

#### `handle_bytes`

```python
def handle_bytes(self) -> Tuple[bytes, int, Dict[str, str]]
```

- **Returns:** `Tuple[bytes, int, Dict[str, str]]`
- **Description:** Return ``(body_bytes, http_status, headers)`` for direct HTTP handler use.

### `tokenpak.proxy.api.routes.MetricsRoute`

**Bases:** object

Handles GET /metrics requests — returns Prometheus text exposition format.

Parameters
----------
proxy_server : ProxyServer, optional
    Live proxy server instance for session + circuit-breaker data.
    If None, metrics are collected from available global registries only.
db_path : str or Path, optional
    Path to TelemetryDB for per-provider/model breakdowns.
    Defaults to the project-level ``telemetry.db`` when not set.

#### `__init__`

```python
def __init__(self, proxy_server: Optional[Any] = None, db_path: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `handle`

```python
def handle(self) -> str
```

- **Returns:** `str`
- **Description:** Collect and return Prometheus metrics as a text string.

#### `handle_bytes`

```python
def handle_bytes(self) -> Tuple[bytes, int, Dict[str, str]]
```

- **Returns:** `Tuple[bytes, int, Dict[str, str]]`
- **Description:** Return ``(body_bytes, http_status, headers)`` for direct HTTP handler use.

### `tokenpak.proxy.api.routes.RouteRegistry`

**Bases:** object

Minimal route registry for management API endpoints.

Supports exact-path matching only (no regex/params).

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, path: str, handler: Any) -> None
```

- **Returns:** `None`
- **Description:** Register *handler* for *path*.

#### `match`

```python
def match(self, path: str) -> Optional[Any]
```

- **Returns:** `Optional[Any]`
- **Description:** Return the handler for *path*, or None if not registered.

#### `paths`

```python
def paths(self) -> list[str]
```

- **Returns:** `list[str]`

### `tokenpak.proxy.cache.CacheEntry`

**Bases:** object

Single cache entry with metadata.

#### `is_expired`

```python
def is_expired(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.proxy.cache.CacheMetrics`

**Bases:** object

Cache performance metrics.

#### `hit_rate`

```python
def hit_rate(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.proxy.cache.LRUCache`

**Bases:** object

Thread-safe LRU cache with TTL and size-based eviction.

Configuration (via proxy.yaml):
    cache:
        max_size_mb: 256
        ttl_seconds: 3600
        eviction_policy: lru

Usage:
    cache = LRUCache(max_size_mb=256, ttl_seconds=3600)
    cache.set("key", value)
    result = cache.get("key")  # None if missing or expired

#### `__init__`

```python
def __init__(self, max_size_mb: float = 256.0, ttl_seconds: Optional[float] = 3600.0, eviction_policy: str = 'lru') -> Any
```

- **Returns:** `Any`

#### `get`

```python
def get(self, key: str) -> Optional[Any]
```

- **Returns:** `Optional[Any]`
- **Description:** Retrieve value. Returns None if missing or expired.

#### `set`

```python
def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None
```

- **Returns:** `None`
- **Description:** Store a value. Evicts LRU entries if size limit reached.

#### `delete`

```python
def delete(self, key: str) -> bool
```

- **Returns:** `bool`
- **Description:** Delete a key. Returns True if it existed.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Clear all entries.

#### `evict_expired`

```python
def evict_expired(self) -> int
```

- **Returns:** `int`
- **Description:** Scan and evict all expired entries. Returns count evicted.

#### `metrics`

```python
def metrics(self) -> CacheMetrics
```

- **Returns:** `CacheMetrics`

#### `metrics_dict`

```python
def metrics_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Prometheus-compatible metrics dict.

### `tokenpak.proxy.cache_poison.CacheMissDiagnosis`

**Bases:** object

Result of a prefix-aware cache-miss diagnosis.

All fields are derived metadata safe to log — no raw prompt content.

#### `debug_line`

```python
def debug_line(self) -> str
```

- **Returns:** `str`
- **Description:** Redacted one-line summary for opt-in forensic logging.

### `tokenpak.proxy.capsule_integration.RequestHook`

**Bases:** Protocol

Compression hook shape accepted by the proxy request pipeline.

#### `__call__`

```python
def __call__(self, body: bytes, model: str, trace: Optional['PipelineTrace'] = None) -> tuple[bytes, int, int, int]
```

- **Returns:** `tuple[bytes, int, int, int]`

### `tokenpak.proxy.circuit_breaker.CircuitBreaker`

**Bases:** object

Thread-safe circuit breaker for a single provider.

State machine::

    CLOSED  ──(threshold failures in window)──▶  OPEN
    OPEN    ──(recovery_timeout elapsed)──────▶  HALF_OPEN
    HALF_OPEN ─(success)─▶  CLOSED
    HALF_OPEN ─(failure)─▶  OPEN  (timer reset)

#### `__init__`

```python
def __init__(self, provider: str, config: CircuitBreakerConfig) -> None
```

- **Returns:** `None`

#### `allow_request`

```python
def allow_request(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the request should proceed, False to fast-fail.

#### `record_success`

```python
def record_success(self) -> None
```

- **Returns:** `None`
- **Description:** Record a successful response. Resets circuit if in HALF_OPEN.

#### `record_failure`

```python
def record_failure(self) -> None
```

- **Returns:** `None`
- **Description:** Record a failed response. May trip the circuit.

#### `state`

```python
def state(self) -> CircuitState
```

- **Returns:** `CircuitState`

#### `status`

```python
def status(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return a status dict for the /health endpoint.

#### `reset`

```python
def reset(self) -> None
```

- **Returns:** `None`
- **Description:** Manually reset the circuit to CLOSED.

### `tokenpak.proxy.circuit_breaker.CircuitBreakerConfig`

**Bases:** object

Configuration for all circuit breakers.

#### `from_env`

```python
def from_env(cls) -> 'CircuitBreakerConfig'
```

- **Returns:** `'CircuitBreakerConfig'`

### `tokenpak.proxy.circuit_breaker.CircuitBreakerRegistry`

**Bases:** object

Thread-safe registry of per-provider circuit breakers.

#### `__init__`

```python
def __init__(self, config: Optional[CircuitBreakerConfig] = None) -> None
```

- **Returns:** `None`

#### `allow_request`

```python
def allow_request(self, provider: str) -> bool
```

- **Returns:** `bool`

#### `record_success`

```python
def record_success(self, provider: str) -> None
```

- **Returns:** `None`

#### `record_failure`

```python
def record_failure(self, provider: str) -> None
```

- **Returns:** `None`

#### `get_state`

```python
def get_state(self, provider: str) -> CircuitState
```

- **Returns:** `CircuitState`

#### `reload_config`

```python
def reload_config(self) -> None
```

- **Returns:** `None`
- **Description:** Thread-safe config reload — re-reads env vars and propagates to all breakers.

#### `all_statuses`

```python
def all_statuses(self) -> dict[str, dict[str, object]]
```

- **Returns:** `dict[str, dict[str, object]]`

#### `reset`

```python
def reset(self, provider: str) -> None
```

- **Returns:** `None`

#### `reset_all`

```python
def reset_all(self) -> None
```

- **Returns:** `None`

#### `enabled`

```python
def enabled(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.proxy.circuit_breaker.RateLimitCircuitBreaker`

**Bases:** object

Per-provider 429 circuit breaker.

Records 429 responses in a rolling window.  When the count reaches
*threshold* within *window_sec* seconds the circuit opens.  While open
``is_open()`` returns True — callers should return HTTP 503 without
forwarding upstream.  After *cooldown_sec* seconds the circuit closes
automatically and normal forwarding resumes.

Configuration (env vars at module level, overrideable per-instance):
  TOKENPAK_RATE_LIMIT_WINDOW_SEC   (default 60)
  TOKENPAK_RATE_LIMIT_THRESHOLD    (default 5)
  TOKENPAK_RATE_LIMIT_COOLDOWN_SEC (default 30)

#### `__init__`

```python
def __init__(self, window_sec: Optional[float] = None, threshold: Optional[int] = None, cooldown_sec: Optional[float] = None) -> None
```

- **Returns:** `None`

#### `record_429`

```python
def record_429(self) -> None
```

- **Returns:** `None`
- **Description:** Record one 429 response.  Opens circuit when threshold is reached.

#### `is_open`

```python
def is_open(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the circuit is open (caller should return 503).

#### `reset`

```python
def reset(self) -> None
```

- **Returns:** `None`
- **Description:** Manually reset to closed state.  Use in tests only.

#### `status`

```python
def status(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return a status dict suitable for health-check endpoints.

### `tokenpak.proxy.circuit_breaker.RateLimitCircuitBreakerRegistry`

**Bases:** object

Thread-safe per-provider registry of :class:`RateLimitCircuitBreaker` instances.

#### `__init__`

```python
def __init__(self, window_sec: Optional[float] = None, threshold: Optional[int] = None, cooldown_sec: Optional[float] = None) -> None
```

- **Returns:** `None`

#### `record_429`

```python
def record_429(self, provider: str) -> None
```

- **Returns:** `None`
- **Description:** Record a 429 for *provider*.  May open the circuit.

#### `is_open`

```python
def is_open(self, provider: str) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the rate-limit circuit is open for *provider*.

#### `reset`

```python
def reset(self, provider: str) -> None
```

- **Returns:** `None`

#### `reset_all`

```python
def reset_all(self) -> None
```

- **Returns:** `None`

#### `all_statuses`

```python
def all_statuses(self) -> dict[str, dict[str, object]]
```

- **Returns:** `dict[str, dict[str, object]]`

### `tokenpak.proxy.config.ProxyConfig`

**Bases:** object

Read-only configuration object for the TokenPak proxy.

Wraps the module-level constants so callers can access them as attributes
on a single config instance::

    cfg = ProxyConfig()
    print(cfg.port, cfg.compilation_mode)

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

### `tokenpak.proxy.connection_pool.ConnectionPool`

**Bases:** object

Thread-safe, per-provider ``httpx.Client`` pool.

Usage
-----
::

    pool = ConnectionPool()

    # Non-streaming request
    with pool.request("POST", "https://api.anthropic.com/v1/messages",
                      content=body, headers=headers) as response:
        data = response.read()

    # Streaming request (SSE)
    with pool.stream("POST", "https://api.anthropic.com/v1/messages",
                     content=body, headers=headers) as response:
        for chunk in response.iter_bytes(chunk_size=4096):
            ...

Lifecycle
---------
Call ``pool.close()`` to release all connections (e.g. on proxy shutdown).

#### `__init__`

```python
def __init__(self, config: Optional[PoolConfig] = None) -> None
```

- **Returns:** `None`

#### `request`

```python
def request(self, method: str, url: str, *, content: Optional[bytes] = None, headers: Optional[Mapping[str, str]] = None, session_key: Optional[str] = None) -> httpx.Response
```

- **Returns:** `httpx.Response`
- **Description:** Send a non-streaming HTTP request via the pool.

#### `stream`

```python
def stream(self, method: str, url: str, *, content: Optional[bytes] = None, headers: Optional[Mapping[str, str]] = None, session_key: Optional[str] = None) -> _StreamingContext
```

- **Returns:** `_StreamingContext`
- **Description:** Send a streaming HTTP request via the pool.

#### `http2_enabled`

```python
def http2_enabled(self) -> bool
```

- **Returns:** `bool`
- **Description:** True if HTTP/2 will be used (config says yes AND h2 is installed).

#### `active_providers`

```python
def active_providers(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** List of netloc strings for which a client has been created.

#### `metrics`

```python
def metrics(self) -> dict[str, int | float | bool]
```

- **Returns:** `dict[str, int | float | bool]`
- **Description:** Return a copy of the current pool metrics.

#### `reset_metrics`

```python
def reset_metrics(self) -> None
```

- **Returns:** `None`
- **Description:** Reset all pool counters to zero.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`
- **Description:** Close all pooled clients within the configured shutdown bound.

#### `session_client_snapshot`

```python
def session_client_snapshot(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return a diagnostic snapshot of the per-session client pool.

### `tokenpak.proxy.connection_pool.PoolConfig`

**Bases:** object

Connection pool configuration.

Attributes
----------
max_connections : int
    Maximum total connections per provider (default: 20).
max_keepalive_connections : int
    Maximum keep-alive connections per provider (default: 10).
keepalive_expiry : float
    Seconds before an idle keep-alive connection is evicted (default: 30).
connect_timeout : float
    Seconds to wait for a new TCP connection (default: 10).
read_timeout : float
    Seconds to wait for a response (default: 300 — LLM responses can be slow).
http2 : bool
    Enable HTTP/2 when ``h2`` is installed (default: True).
evict_on_transport_error : bool
    Evict a client from the pool when a request on it raises a transport
    error, so retries get a fresh client/connection instead of the same
    dead one (default: True).
retire_close_grace_seconds : float
    How long an evicted client is retained (unclosed) so requests still
    in flight on it can finish before ``close()`` (default: 900).
close_timeout_seconds : float
    Maximum time pool shutdown waits for client close calls (default: 1).

#### `from_env`

```python
def from_env(cls) -> 'PoolConfig'
```

- **Returns:** `'PoolConfig'`
- **Description:** Build a PoolConfig from environment variables.

### `tokenpak.proxy.connection_pool.PoolMetrics`

**Bases:** object

Rolling counters for connection pool health checks.

#### `reuse_rate`

```python
def reuse_rate(self) -> float
```

- **Returns:** `float`
- **Description:** Fraction of requests that reused an existing connection (0–1).

#### `to_dict`

```python
def to_dict(self) -> dict[str, int | float]
```

- **Returns:** `dict[str, int | float]`

### `tokenpak.proxy.credential_passthrough.CredentialPassthrough`

**Bases:** object

Stateless credential-forwarding utility.

All methods are pure functions that operate on a headers dict;
no instance state ever holds credential values.

Parameters
----------
require_auth : bool
    When *True* (default) ``validate_auth`` rejects requests that
    carry no recognisable auth header. Set to *False* for open endpoints.

#### `__init__`

```python
def __init__(self, *, require_auth: bool = True) -> None
```

- **Returns:** `None`

#### `validate_auth`

```python
def validate_auth(self, request_headers: Dict[str, str]) -> Tuple[bool, Optional[str]]
```

- **Returns:** `Tuple[bool, Optional[str]]`
- **Description:** Check that *request_headers* contains a well-formed auth credential.

#### `build_forward_headers`

```python
def build_forward_headers(self, request_headers: Dict[str, str], provider: str) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`
- **Description:** Construct the headers dict to forward to an upstream *provider*.

#### `mask_for_logging`

```python
def mask_for_logging(self, headers: Dict[str, str]) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`
- **Description:** Return a copy of *headers* safe for debug logging.

### `tokenpak.proxy.custom_providers.CustomProvider`

**Bases:** object

Parsed representation of a single custom provider entry.

#### `api_key`

```python
def api_key(self) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Resolve the API key from the environment (never stored in memory).

#### `has_api_key`

```python
def has_api_key(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.proxy.degradation.DegradationEvent`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.proxy.degradation.DegradationTracker`

**Bases:** object

Thread-safe, bounded in-memory log of degradation events.

Usage::

    from tokenpak.proxy.degradation import get_degradation_tracker
    tracker = get_degradation_tracker()
    tracker.record("compression_failure", "CompressionError: …", recovered=True)

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `record`

```python
def record(self, event_type: str, detail: str, recovered: bool = True) -> None
```

- **Returns:** `None`
- **Description:** Record a degradation event.

#### `record_compression_failure`

```python
def record_compression_failure(self, exc: Exception) -> None
```

- **Returns:** `None`
- **Description:** Shortcut: record a compression/hook failure.

#### `record_provider_failover`

```python
def record_provider_failover(self, from_provider: str, to_provider: str, reason: str) -> None
```

- **Returns:** `None`
- **Description:** Shortcut: record a provider failover.

#### `record_config_fallback`

```python
def record_config_fallback(self, detail: str) -> None
```

- **Returns:** `None`
- **Description:** Shortcut: record a config fallback.

#### `is_degraded`

```python
def is_degraded(self) -> bool
```

- **Returns:** `bool`
- **Description:** True if there was a runtime degradation event in the last 10 minutes.

#### `get_recent`

```python
def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Return the most recent events (newest first).

#### `summary`

```python
def summary(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return a summary dict for status display and the /degradation endpoint.

### `tokenpak.proxy.embedding_cache.EmbeddingCache`

**Bases:** object

SQLite-backed cache for embedding responses.

Each public method opens and closes its own connection (connection-per-call
pattern) so concurrent readers don't block each other under WAL mode.

#### `__init__`

```python
def __init__(self, db_path: str, ttl_days: int = _DEFAULT_TTL_DAYS, max_mb: int = _DEFAULT_MAX_MB) -> None
```

- **Returns:** `None`

#### `get`

```python
def get(self, model: str, dims: int, text: str, *, no_cache: bool = False) -> Optional[bytes]
```

- **Returns:** `Optional[bytes]`
- **Description:** Return cached embedding bytes, or None on miss / bypass / expiry.

#### `put`

```python
def put(self, model: str, dims: int, text: str, response_json: bytes, tokens: int) -> None
```

- **Returns:** `None`
- **Description:** Store an embedding response, then enforce TTL and size limits.

### `tokenpak.proxy.embedding_router.ContentAwareRouter`

**Bases:** object

Route embedding requests to the best available provider.

Parameters
----------
available_providers:
    Ordered list of provider name strings that are currently reachable.
    Defaults to the module-level ``_DEFAULT_PROVIDERS`` list (all known
    providers, useful for unit tests that do not need real API keys).
strategy:
    Overrides ``TOKENPAK_EMBEDDING_ROUTING_STRATEGY`` env var when
    supplied.  One of ``"auto"``, ``"fast"``, ``"quality"``,
    ``"passthrough"``.

#### `__init__`

```python
def __init__(self, available_providers: Optional[List[str]] = None, strategy: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `strategy`

```python
def strategy(self) -> str
```

- **Returns:** `str`

#### `route`

```python
def route(self, input_text: str, model_hint: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Return the name of the best provider for this request.

### `tokenpak.proxy.example_selector.IntentExampleSelector`

**Bases:** object

#### `__init__`

```python
def __init__(self, *, examples_root: str | Path = '~/.tokenpak/examples', config_path: str | Path = '~/.tokenpak/examples/config.yaml') -> None
```

- **Returns:** `None`

#### `select`

```python
def select(self, *, intent: str, token_budget: int, reserved_tokens: int = 0) -> ExampleSelection
```

- **Returns:** `ExampleSelection`

### `tokenpak.proxy.failover.FailoverConfig`

**Bases:** object

Parsed failover configuration block.

#### `available_chain`

```python
def available_chain(self) -> List[ProviderEntry]
```

- **Returns:** `List[ProviderEntry]`
- **Description:** Return only providers whose credentials are present in the environment.

### `tokenpak.proxy.failover.FailoverManager`

**Bases:** object

Orchestrates provider failover.

Usage::

    mgr = FailoverManager()
    for attempt in mgr.iter_providers("claude-sonnet-4-5", preferred="anthropic"):
        try:
            result = call_provider(attempt.provider, attempt.model, ...)
            break
        except ProviderError:
            continue

#### `__init__`

```python
def __init__(self, config: Optional[FailoverConfig] = None) -> Any
```

- **Returns:** `Any`

#### `reload_config`

```python
def reload_config(self, path: Optional[Path] = None) -> None
```

- **Returns:** `None`
- **Description:** Thread-safe config reload. Safe to call while iter_providers() is running.

#### `enabled`

```python
def enabled(self) -> bool
```

- **Returns:** `bool`

#### `map_model`

```python
def map_model(self, original_model: str, provider: str) -> str
```

- **Returns:** `str`
- **Description:** Map an original model name to the equivalent for *provider*.

#### `iter_providers`

```python
def iter_providers(self, model: str, preferred: Optional[str] = None) -> Iterator[FailoverResult]
```

- **Returns:** `Iterator[FailoverResult]`
- **Description:** Yield FailoverResult objects in failover priority order.

#### `get_provider_for`

```python
def get_provider_for(self, model: str, preferred: Optional[str] = None) -> Optional[FailoverResult]
```

- **Returns:** `Optional[FailoverResult]`
- **Description:** Return the first available provider for the given model.

### `tokenpak.proxy.failover.ProviderEntry`

**Bases:** object

Single provider entry in the failover chain.

#### `credential_available`

```python
def credential_available(self) -> bool
```

- **Returns:** `bool`
- **Description:** True if the required env var is set and non-empty.

#### `get_credential`

```python
def get_credential(self) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Return the credential value from the environment.

### `tokenpak.proxy.failover_engine.CircuitBreaker`

**Bases:** object

Per-provider circuit breaker.

States:
    closed   → normal operation
    open     → skip provider (too many failures)
    half-open → one probe attempt after cool-down

#### `__init__`

```python
def __init__(self, failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD, cool_down_seconds: float = CIRCUIT_COOL_DOWN_SECONDS) -> None
```

- **Returns:** `None`

#### `is_available`

```python
def is_available(self, provider: str) -> bool
```

- **Returns:** `bool`
- **Description:** True if the circuit allows requests to this provider.

#### `record_failure`

```python
def record_failure(self, provider: str) -> bool
```

- **Returns:** `bool`
- **Description:** Record a failure for a provider.

#### `record_success`

```python
def record_success(self, provider: str) -> None
```

- **Returns:** `None`
- **Description:** Record a success — resets failure count and closes circuit.

#### `get_state`

```python
def get_state(self, provider: str) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return current circuit state dict for status display.

#### `reset`

```python
def reset(self, provider: str) -> None
```

- **Returns:** `None`
- **Description:** Force-reset circuit to closed (for testing / manual override).

### `tokenpak.proxy.failover_engine.ClassifiedError`

**Bases:** object

#### `should_switch`

```python
def should_switch(self) -> bool
```

- **Returns:** `bool`
- **Description:** True if the error warrants switching providers (not auth — alert instead).

#### `is_auth_error`

```python
def is_auth_error(self) -> bool
```

- **Returns:** `bool`

#### `is_rate_limit`

```python
def is_rate_limit(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.proxy.failover_engine.FailoverEngine`

**Bases:** object

Orchestrates multi-provider failover for LLM proxy requests.

Usage::

    engine = FailoverEngine()
    for attempt in engine.iter_attempts(original_model="claude-sonnet-4-5",
                                         original_provider="anthropic"):
        try:
            response = call_provider(attempt.provider, attempt.model, ...)
            engine.record_success(attempt.provider)
            break
        except ProviderError as exc:
            error = classify_error(http_status=exc.status)
            if not engine.handle_error(attempt, error):
                raise  # all providers exhausted

#### `__init__`

```python
def __init__(self, config: Optional[FailoverConfig] = None, circuit_breaker: Optional[CircuitBreaker] = None, event_log: Optional[FailoverEventLog] = None) -> None
```

- **Returns:** `None`

#### `enabled`

```python
def enabled(self) -> bool
```

- **Returns:** `bool`

#### `iter_attempts`

```python
def iter_attempts(self, original_model: str, original_provider: str) -> Iterator[ProviderAttempt]
```

- **Returns:** `Iterator[ProviderAttempt]`
- **Description:** Yield ProviderAttempt objects in failover order, respecting circuit breakers.

#### `handle_error`

```python
def handle_error(self, attempt: ProviderAttempt, error: ClassifiedError, original_provider: str, original_model: str) -> Tuple[bool, float]
```

- **Returns:** `Tuple[bool, float]`
- **Description:** Process an error from a provider attempt.

#### `record_success`

```python
def record_success(self, provider: str, original_provider: str, original_model: str, was_failover: bool = False) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Record a successful provider call.

#### `get_circuit_states`

```python
def get_circuit_states(self) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Return all known circuit breaker states (for status display).

### `tokenpak.proxy.failover_engine.FailoverEvent`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.proxy.failover_engine.FailoverEventLog`

**Bases:** object

Thread-safe in-memory log of failover events (max 100).

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `record`

```python
def record(self, event: FailoverEvent) -> None
```

- **Returns:** `None`

#### `get_recent`

```python
def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`

#### `get_footer_indicator`

```python
def get_footer_indicator(self) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Return footer string for the most recent failover event, or None.

### `tokenpak.proxy.fallback.FallbackChain`

**Bases:** object

Convenience class that encapsulates the key-pool failover logic.

Wraps the module-level ``_build_key_pool``, ``_get_next_key``,
``_cool_down_key``, and upstream-resolution helpers into a single
object for consumers that prefer an OOP interface.

Example::

    chain = FallbackChain()
    idx, key = chain.next_key()
    if not chain.send(idx, ...):
        chain.cool_down(idx, 120, "429 rate-limited")
        idx2, key2 = chain.next_key(exclude=idx)

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `pool_size`

```python
def pool_size(self) -> int
```

- **Returns:** `int`

#### `next_key`

```python
def next_key(self, exclude: Optional[int] = None) -> tuple[str | None, int]
```

- **Returns:** `tuple[str | None, int]`
- **Description:** Return ``(index, key)`` for the next available API key.

#### `cool_down`

```python
def cool_down(self, idx: int, duration: float, reason: str) -> None
```

- **Returns:** `None`
- **Description:** Mark key *idx* as temporarily unavailable.

#### `is_available`

```python
def is_available(self, idx: int) -> bool
```

- **Returns:** `bool`

#### `resolve_upstream`

```python
def resolve_upstream(self, adapter: FormatAdapter) -> str
```

- **Returns:** `str`

#### `reload_config`

```python
def reload_config(self) -> str
```

- **Returns:** `str`

### `tokenpak.proxy.handlers.rate_limit.RateLimitBackoff`

**Bases:** object

Exponential backoff calculator for rate-limited API requests.

Parameters
----------
base_wait : float
    Base wait time in seconds for attempt 0. Default: 1.0.
max_wait : float
    Maximum wait time ceiling in seconds. Default: 60.0.
jitter_factor : float
    Fraction of computed wait to add as random jitter (0 = deterministic).
    Jitter is drawn from [0, jitter_factor * computed_wait). Default: 0.1.

#### `__init__`

```python
def __init__(self, base_wait: float = 1.0, max_wait: float = 60.0, jitter_factor: float = 0.1) -> None
```

- **Returns:** `None`

#### `wait_time`

```python
def wait_time(self, attempt: int, retry_after: float | None = None) -> float
```

- **Returns:** `float`
- **Description:** Calculate the wait time for a given attempt number.

### `tokenpak.proxy.intelligence.auth.APIKeyValidator`

**Bases:** object

Maps API keys to tiers.

Override ``lookup`` to integrate with a real database.
For local dev/tests, populate ``TOKENPAK_ALLOWED_KEYS``.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, key: str, tier: LicenseTier) -> None
```

- **Returns:** `None`
- **Description:** Register a key programmatically (useful in tests).

#### `lookup`

```python
def lookup(self, key: str) -> Optional[LicenseTier]
```

- **Returns:** `Optional[LicenseTier]`
- **Description:** Return the tier for *key*, or ``None`` if unknown.

#### `validate`

```python
def validate(self, key: Optional[str]) -> Tuple[bool, Optional[LicenseTier], str]
```

- **Returns:** `Tuple[bool, Optional[LicenseTier], str]`
- **Description:** Returns ``(ok, tier, reason)``.

### `tokenpak.proxy.intelligence.auth.PIIScrubFilter`

**Bases:** logging.Filter

Remove API keys and bearer tokens from log records.

#### `filter`

```python
def filter(self, record: logging.LogRecord) -> bool
```

- **Returns:** `bool`

### `tokenpak.proxy.intelligence.auth.RateLimiter`

**Bases:** object

Fixed-window (per-minute) rate limiter.

Thread-safe; resets at the start of each UTC minute.
Stores ``(count, window_start)`` per hashed key.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `check`

```python
def check(self, key: str, tier: LicenseTier) -> Tuple[bool, int, int]
```

- **Returns:** `Tuple[bool, int, int]`
- **Description:** Returns ``(allowed, remaining, reset_ts)``.

### `tokenpak.proxy.intelligence.auth.TokenPakAuthMiddleware`

**Bases:** BaseHTTPMiddleware

Middleware that:
1. Injects a unique ``X-Request-ID`` into every request.
2. Validates ``X-TokenPak-Key``.
3. Enforces per-tier rate limits.
4. Attaches ``request.state.tier`` and ``request.state.request_id``.
5. Sets rate-limit response headers on every reply.

#### `__init__`

```python
def __init__(self, app: Any, validator: Optional[APIKeyValidator] = None, limiter: Optional[RateLimiter] = None) -> None
```

- **Returns:** `None`

#### `dispatch`

```python
async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response
```

- **Returns:** `Response`

### `tokenpak.proxy.intelligence.deep_health.CheckResult`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.proxy.intelligence.deep_health.DeepHealthChecker`

**Bases:** object

Runs all deep health checks, optionally in parallel.

Parameters
----------
db_path:
    Override database path for testing.
index_path:
    Override index file path for testing.
provider_timeout:
    HTTP timeout for provider probe requests (seconds).

#### `__init__`

```python
def __init__(self, db_path: Optional[str] = None, index_path: Optional[str] = None, provider_timeout: float = 5.0, _check_anthropic: Any = None, _check_openai: Any = None, _check_database: Any = None, _check_index: Any = None, _check_memory: Any = None, _check_disk: Any = None) -> None
```

- **Returns:** `None`

#### `run`

```python
def run(self) -> DeepHealthResult
```

- **Returns:** `DeepHealthResult`
- **Description:** Run all checks synchronously (safe for sync and async contexts).

### `tokenpak.proxy.intelligence.deep_health.DeepHealthResult`

**Bases:** object

#### `http_status`

```python
def http_status(self) -> int
```

- **Returns:** `int`
- **Description:** 200 for ok/degraded, 503 for error.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.proxy.memory_guard.MemoryGuard`

**Bases:** object

Own one pressure-monitor thread with explicit thresholds and lifecycle.

#### `__init__`

```python
def __init__(self, *, target_mb: int, ceiling_mb: int, sys_low_mb: int = 0, check_interval_secs: float = 30, cooldown_secs: float = 300, action_mode: str = 'auto', configuration: Mapping[str, Any] | None = None, on_evict_compact_cache: Callable[[int], int] | None = None, on_evict_token_cache: Callable[[int], int] | None = None, on_evict_semantic_cache: Callable[[], int] | None = None) -> None
```

- **Returns:** `None`

#### `start`

```python
def start(self) -> bool
```

- **Returns:** `bool`
- **Description:** Start exactly one monitor thread; return false when already running.

#### `stop`

```python
def stop(self, timeout: float = 5.0) -> bool
```

- **Returns:** `bool`
- **Description:** Stop and join the monitor, retaining ownership if the join times out.

#### `stats`

```python
def stats(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.proxy.middleware.audit_trail.CompileAudit`

**Bases:** object

Audit trail for a /compile request.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert to dict.

#### `to_json`

```python
def to_json(self) -> str
```

- **Returns:** `str`
- **Description:** Convert to JSON.

### `tokenpak.proxy.middleware.logger.AsyncLogger`

**Bases:** object

Asynchronous logger with buffering.

#### `__init__`

```python
def __init__(self, config: LoggingConfig) -> None
```

- **Returns:** `None`

#### `log`

```python
def log(self, record: LogRecord) -> None
```

- **Returns:** `None`
- **Description:** Enqueue log record.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Stop async logging.

### `tokenpak.proxy.middleware.logger.LogRecord`

**Bases:** object

Structured log record.

#### `to_json`

```python
def to_json(self) -> str
```

- **Returns:** `str`
- **Description:** Convert to JSON.

#### `to_text`

```python
def to_text(self) -> str
```

- **Returns:** `str`
- **Description:** Convert to human-readable text.

### `tokenpak.proxy.middleware.logger.LoggingConfig`

**Bases:** object

Logging configuration.

#### `resolve_log_dir`

```python
def resolve_log_dir(self) -> str
```

- **Returns:** `str`
- **Description:** Resolve log directory path.

### `tokenpak.proxy.middleware.logger.RequestLogger`

**Bases:** object

Structured request logger.

#### `__init__`

```python
def __init__(self, config: LoggingConfig) -> None
```

- **Returns:** `None`

#### `log_request`

```python
def log_request(self, endpoint: str, method: str = 'POST', client_ip: Optional[str] = None, request_size: int = 0, response_size: int = 0, status_code: int = 200, latency_ms: float = 0.0, compression_ratio: Optional[float] = None, message: str = '', context: Optional[dict[str, object]] = None, level: LogLevel = 'info', request_id: Optional[str] = None) -> None
```

- **Returns:** `None`
- **Description:** Log a request.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Stop logging.

### `tokenpak.proxy.middleware.logging_middleware.LoggingMiddleware`

**Bases:** object

Request logging middleware for proxy.

#### `__init__`

```python
def __init__(self, logger: RequestLogger) -> None
```

- **Returns:** `None`

#### `wrap_request`

```python
def wrap_request(self, endpoint: str, method: str = 'POST') -> Callable[[Callable[P, R]], Callable[P, R]]
```

- **Returns:** `Callable[[Callable[P, R]], Callable[P, R]]`
- **Description:** Decorator to wrap a request handler with logging.

#### `log_compile_audit`

```python
def log_compile_audit(self, audit: CompileAudit) -> None
```

- **Returns:** `None`
- **Description:** Log compilation audit trail.

#### `log_cache_audit`

```python
def log_cache_audit(self, audit: CacheAudit) -> None
```

- **Returns:** `None`
- **Description:** Log cache audit trail.

#### `log_metrics_audit`

```python
def log_metrics_audit(self, audit: MetricsAudit) -> None
```

- **Returns:** `None`
- **Description:** Log metrics audit trail.

### `tokenpak.proxy.middleware.semantic_cache_middleware.SemanticCacheMiddleware`

**Bases:** object

Proxy-level middleware wrapping SemanticCache with scope management.

Maintains a per-scope cache dict so session-scoped and agent-scoped
isolation works out-of-the-box.

#### `__init__`

```python
def __init__(self, config: Optional[SemanticCacheConfig] = None) -> None
```

- **Returns:** `None`

#### `check`

```python
def check(self, query: str, scope_key: str = '') -> SemanticCacheLookup
```

- **Returns:** `SemanticCacheLookup`
- **Description:** Look up *query* in the appropriate scoped cache.

#### `record`

```python
def record(self, query: str, response: bytes, scope_key: str = '') -> None
```

- **Returns:** `None`
- **Description:** Store raw *response* bytes for *query* in the appropriate cache.

#### `build_trace`

```python
def build_trace(self, lookup: SemanticCacheLookup) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Build trace/span metadata from a cache lookup result.

#### `stats`

```python
def stats(self, scope_key: str = '') -> dict[str, int | float]
```

- **Returns:** `dict[str, int | float]`
- **Description:** Return hit/miss stats for a given scope.

#### `clear`

```python
def clear(self, scope_key: str = '') -> None
```

- **Returns:** `None`
- **Description:** Clear the cache for *scope_key* (or the global cache).

### `tokenpak.proxy.monitor.Monitor`

**Bases:** object

SQLite-backed request telemetry (requests + budget_alerts tables).

Writes are enqueued to a background writer thread (async, sub-millisecond
enqueue). The writer is a daemon thread, so queued rows are NOT
automatically durable across interpreter exit: embedders that own process
shutdown should call :meth:`stop` (poison pill + bounded drain + join) or
:meth:`flush` (bounded drain without stopping the writer) to guarantee
queued telemetry reaches the database on a clean exit. Rows that still
fail after bounded write retries are counted; diagnostic surfaces can
read the counter via :meth:`dropped_row_count`.

#### `__init__`

```python
def __init__(self, db_path: str | Path) -> None
```

- **Returns:** `None`

#### `log`

```python
def log(self, model: str, input_tokens: int, output_tokens: int, cost: float, latency_ms: int, status_code: int, endpoint: str, compilation_mode: str = '', protected_tokens: int = 0, compressed_tokens: int = 0, injected_tokens: int = 0, injected_sources: str = '', cache_read_tokens: int = 0, cache_creation_tokens: int = 0, would_have_saved: int = 0, cache_origin: str = 'unknown', user_id: str = '', cache_creation_ephemeral_1h_tokens: int = 0, cache_creation_ephemeral_5m_tokens: int = 0, ttl_attribution: str | None = None, session_id: str = '', agent_id: str = '', cycle_id: str = '', attribution_source: str = '', stop_reason: str = '') -> None
```

- **Returns:** `None`

#### `flush`

```python
def flush(self, timeout: float = 5.0) -> bool
```

- **Returns:** `bool`
- **Description:** Block until queued telemetry writes are committed (bounded).

#### `stop`

```python
def stop(self, timeout: float = 5.0) -> bool
```

- **Returns:** `bool`
- **Description:** Drain queued writes and stop the background writer thread.

#### `dropped_row_count`

```python
def dropped_row_count(self) -> int
```

- **Returns:** `int`
- **Description:** Telemetry rows dropped after write retries were exhausted.

#### `get_stats`

```python
def get_stats(self, hours: int = 24) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `get_by_model`

```python
def get_by_model(self) -> dict[str, dict[str, object]]
```

- **Returns:** `dict[str, dict[str, object]]`

#### `get_budget_alert_status`

```python
def get_budget_alert_status(self, _daily_limit: float | None = None, _threshold_pct: float | None = None) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `get_savings_report`

```python
def get_savings_report(self, since: str | None = None) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Savings summary computed from registry pricing rates.

#### `recent`

```python
def recent(self, limit: int = 20) -> list[dict[str, object]]
```

- **Returns:** `list[dict[str, object]]`

### `tokenpak.proxy.passthrough.CredentialPassthrough`

**Bases:** object

Stateless credential-forwarding utility.

All methods are pure functions operating on the request headers dict.
No instance state holds credential values between calls.

Usage
-----
::

    pt = CredentialPassthrough()
    ok, err = pt.validate_auth(request_headers)
    if not ok:
        return 401, err

    fwd_headers = pt.build_forward_headers(request_headers, config)

#### `__init__`

```python
def __init__(self, config: Optional[PassthroughConfig] = None) -> None
```

- **Returns:** `None`

#### `validate_auth`

```python
def validate_auth(self, headers: Dict[str, str]) -> Tuple[bool, Optional[str]]
```

- **Returns:** `Tuple[bool, Optional[str]]`
- **Description:** Check that the request carries a recognisable auth header.

#### `build_forward_headers`

```python
def build_forward_headers(self, incoming_headers: Dict[str, str], config: Optional[PassthroughConfig] = None) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`
- **Description:** Build the headers dict to forward to the upstream provider.

#### `mask_for_logging`

```python
def mask_for_logging(self, headers: Dict[str, str], config: Optional[PassthroughConfig] = None) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`
- **Description:** Return a copy of ``headers`` safe for debug logging.

### `tokenpak.proxy.prompt_builder.DeterministicPromptPack`

**Bases:** object

Enforces deterministic prompt assembly with fixed section ordering.

Canonical Section Order
-----------------------
1. SYSTEM PROMPT          — model behavior, instructions (STABLE)
2. TOOLS DEFINITIONS      — tool schemas in canonical order (STABLE)
3. POLICIES/CONSTRAINTS   — safety rules, guardrails (STABLE)
4. RETRIEVED CONTEXT      — search results, injected knowledge (VOLATILE)
5. USER INPUT             — user message / query (VOLATILE)

Key Properties
~~~~~~~~~~~~~~
- **Fixed ordering** ensures consistent section positioning
- **Deterministic separators** (no extra whitespace, canonical line breaks)
- **Byte-identical output** for equivalent inputs (proven by tests)
- **Stable vs volatile boundary** explicitly marked for cache_control
- **Optional integration** — does not break existing PromptBuilder

Why This Matters
~~~~~~~~~~~~~~~~
Prompt caching in Anthropic APIs requires:
  1. Consistent prefix structure (cache key must be stable)
  2. Volatile content segregated after the cache boundary
  3. Deterministic encoding to ensure byte-for-byte matches

Without fixed ordering:
  - Section position varies based on assembly order
  - Cache keys are unstable even for semantically identical prompts
  - Equivalent requests produce different byte sequences

With DeterministicPromptPack:
  - Section position is guaranteed
  - Cache keys are stable and reproducible
  - Byte-identical output for identical inputs

Usage Example (Before)
~~~~~~~~~~~~~~~~~~~~~~
```python
# Old way: sections assembled ad-hoc, order inconsistent
system_parts = []
system_parts.append(system_prompt)
if tools:
    system_parts.append(json.dumps(tools))
if policies:
    system_parts.append(policies)
if vault_context:
    system_parts.append(vault_context)

system_str = "\n\n".join(system_parts)  # order may vary, spacing inconsistent
```

Usage Example (After)
~~~~~~~~~~~~~~~~~~~~~
```python
pack = DeterministicPromptPack(
    system="You are a helpful AI.",
    tools=[{"name": "search", "description": "..."}],
    policies="Always be honest.",
    retrieved_context=["doc1", "doc2"],
    user_input="What is X?",
)

system_block = pack.to_system_block()
# Output: deterministic, section order fixed, byte-identical for same inputs
```

Attributes
~~~~~~~~~~
system : str
    System prompt / instructions for the model. (STABLE)
tools : list[dict]
    Tool schemas (typically frozen by tool_schema_registry). (STABLE)
policies : str
    Safety rules, constraints, guardrails. (STABLE)
retrieved_context : list[str | dict]
    Retrieved documents or search results. (VOLATILE — changes per request)
user_input : str
    User message or query. (VOLATILE — changes per request)
metadata : dict
    Optional metadata (not included in output, useful for debugging).

Cache Boundary Marking
~~~~~~~~~~~~~~~~~~~~~~
The last stable section (policies or tools, whichever is last) is marked
with cache_control: {type: "ephemeral"}. Volatile sections follow without
cache markers.

Before::

    system: [
        {type: text, text: "SYSTEM..."},
        {type: text, text: "TOOLS..."},
        {type: text, text: "POLICIES..."}  ← cache boundary
        {type: text, text: "RETRIEVED..."},  ← volatile, no marker
        {type: text, text: "USER..."}        ← volatile, no marker
    ]

After::

    system: [
        {type: text, text: "SYSTEM..."},
        {type: text, text: "TOOLS..."},
        {type: text, text: "POLICIES...", cache_control: {type: ephemeral}}
        {type: text, text: "RETRIEVED..."},
        {type: text, text: "USER..."}
    ]

Testing & Validation
~~~~~~~~~~~~~~~~~~~~
Byte-identity is proven via:

    pack1 = DeterministicPromptPack(...same inputs...)
    pack2 = DeterministicPromptPack(...same inputs...)
    assert pack1.to_request_body() == pack2.to_request_body()
    assert pack1.to_system_block() == pack2.to_system_block()
    # byte-for-byte identical JSON output

Integration Guidance
~~~~~~~~~~~~~~~~~~~~
1. **In proxy layer**: Replace ad-hoc system string assembly with:
   ```
   pack = DeterministicPromptPack(
       system=read_system_prompt(),
       tools=registry.get_tools(),
       policies=read_policies(),
       retrieved_context=vault_search(...),
       user_input=msg.content,
   )
   body["system"] = pack.to_system_block()
   ```

2. **With cache_control**: Automatically handled:
   ```
   blocks = pack.to_system_block()
   # Last stable block already has cache_control marker
   ```

3. **Feature-flagged**: Disable with env var or config:
   ```
   if config.USE_DETERMINISTIC_PACKING:
       pack = DeterministicPromptPack(...)
       body["system"] = pack.to_system_block()
   else:
       # Fallback to PromptBuilder or legacy assembly
   ```

#### `to_system_block`

```python
def to_system_block(self) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Assemble into a list of system content blocks (Anthropic format).

#### `to_request_body`

```python
def to_request_body(self, model: str = 'claude-3-5-sonnet-20241022') -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Assemble into a complete Anthropic messages API request body.

### `tokenpak.proxy.prompt_builder.PromptBuilder`

**Bases:** object

Stateless prompt builder that separates stable from volatile content.

Typical use in proxy::

    builder = PromptBuilder()
    parts = builder.decompose(body_bytes)

    # Add vault injection to volatile tail
    if vault_text:
        parts.volatile_blocks.append({"type": "text", "text": vault_text})

    # Get final body with cache_control correctly placed
    new_body = builder.build(parts)

The builder:
  - Classifies existing system blocks as stable vs volatile
  - Marks last stable block with cache_control: ephemeral
  - Does NOT cache_control volatile blocks
  - Preserves tool schemas (frozen externally by tool_schema_registry)

#### `decompose`

```python
def decompose(self, body_bytes: bytes) -> PromptParts | None
```

- **Returns:** `PromptParts | None`
- **Description:** Parse request body into structured PromptParts.

#### `build`

```python
def build(self, parts: PromptParts) -> bytes
```

- **Returns:** `bytes`
- **Description:** Assemble PromptParts into body bytes with correct cache_control placement.

### `tokenpak.proxy.prompt_builder.PromptCacheStats`

**Bases:** object

Thread-safe per-session cache placement statistics.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `record_applied`

```python
def record_applied(self, stable: int = 0, volatile: int = 0) -> None
```

- **Returns:** `None`

#### `record_skipped`

```python
def record_skipped(self, already_marked: bool = False) -> None
```

- **Returns:** `None`

#### `record_breakpoint`

```python
def record_breakpoint(self, name: str, applied: bool) -> None
```

- **Returns:** `None`

#### `summary`

```python
def summary(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.proxy.prompt_builder.PromptParts`

**Bases:** object

Decomposed prompt parts for inspection and reassembly.

#### `to_request_body`

```python
def to_request_body(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Reassemble into a complete Anthropic request body.

### `tokenpak.proxy.providers.anthropic.AnthropicFormat`

**Bases:** object

Handler for Anthropic Claude API format.

Anthropic uses:
- "system" field for system prompt (string or list of content blocks)
- "messages" array with role/content pairs
- Content can be string or list of content blocks (text, image, etc.)

#### `parse_request`

```python
def parse_request(body: bytes) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Parse an Anthropic API request body.

#### `extract_model`

```python
def extract_model(data: Dict[str, Any]) -> str
```

- **Returns:** `str`
- **Description:** Extract model name from request.

#### `extract_system`

```python
def extract_system(data: Dict[str, Any]) -> str
```

- **Returns:** `str`
- **Description:** Extract system prompt text.

#### `extract_messages`

```python
def extract_messages(data: Dict[str, Any]) -> List[AnthropicMessage]
```

- **Returns:** `List[AnthropicMessage]`
- **Description:** Extract messages from request.

#### `count_tokens_approx`

```python
def count_tokens_approx(data: Dict[str, Any]) -> int
```

- **Returns:** `int`
- **Description:** Approximate token count for request.

#### `is_streaming`

```python
def is_streaming(data: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Check if request is streaming.

#### `build_request`

```python
def build_request(model: str, messages: List[Dict[str, Any]], system: Optional[str] = None, max_tokens: int = 4096, stream: bool = True, **kwargs: object) -> bytes
```

- **Returns:** `bytes`
- **Description:** Build an Anthropic API request body.

#### `inject_system_content`

```python
def inject_system_content(data: Dict[str, Any], content: str, cache_control: bool = True) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Inject additional content into system prompt.

#### `extract_response_tokens`

```python
def extract_response_tokens(body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract output token count from response.

#### `extract_cache_tokens`

```python
def extract_cache_tokens(body: bytes) -> Dict[str, int]
```

- **Returns:** `Dict[str, int]`
- **Description:** Extract cache token counts from response.

### `tokenpak.proxy.providers.anthropic.AnthropicMessage`

**Bases:** object

Represents a message in Anthropic format.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `get_text`

```python
def get_text(self) -> str
```

- **Returns:** `str`
- **Description:** Extract text content from message.

### `tokenpak.proxy.providers.google.GoogleContent`

**Bases:** object

Represents content in Google format.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `get_text`

```python
def get_text(self) -> str
```

- **Returns:** `str`
- **Description:** Extract text content.

### `tokenpak.proxy.providers.google.GoogleFormat`

**Bases:** object

Handler for Google Gemini API format (stub).

Google uses:
- "contents" array instead of "messages"
- "parts" array within each content
- "systemInstruction" for system prompt
- Different role names ("model" instead of "assistant")

DEFERRED: Full implementation for multi-provider support.

#### `parse_request`

```python
def parse_request(body: bytes) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Parse a Google API request body.

#### `extract_model`

```python
def extract_model(data: Dict[str, Any]) -> str
```

- **Returns:** `str`
- **Description:** Extract model name.

#### `extract_system`

```python
def extract_system(data: Dict[str, Any]) -> str
```

- **Returns:** `str`
- **Description:** Extract system instruction.

#### `extract_contents`

```python
def extract_contents(data: Dict[str, Any]) -> List[GoogleContent]
```

- **Returns:** `List[GoogleContent]`
- **Description:** Extract contents from request.

#### `count_tokens_approx`

```python
def count_tokens_approx(data: Dict[str, Any]) -> int
```

- **Returns:** `int`
- **Description:** Approximate token count.

#### `is_streaming`

```python
def is_streaming(data: Dict[str, Any], url: str = '') -> bool
```

- **Returns:** `bool`
- **Description:** Check if request is streaming.

#### `build_request`

```python
def build_request(contents: List[Dict[str, Any]], system_instruction: Optional[str] = None, generation_config: Optional[Dict[str, Any]] = None, **kwargs: object) -> bytes
```

- **Returns:** `bytes`
- **Description:** Build a Google API request body.

#### `extract_response_tokens`

```python
def extract_response_tokens(body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract output token count from response.

### `tokenpak.proxy.providers.openai.OpenAIFormat`

**Bases:** object

Handler for OpenAI API format.

OpenAI uses:
- First message with role="system" for system prompt
- "messages" array with role/content pairs
- Content can be string or array of content parts
- Supports tool calls and function calling

#### `parse_request`

```python
def parse_request(body: bytes) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Parse an OpenAI API request body.

#### `extract_model`

```python
def extract_model(data: Dict[str, Any]) -> str
```

- **Returns:** `str`
- **Description:** Extract model name from request.

#### `extract_system`

```python
def extract_system(data: Dict[str, Any]) -> str
```

- **Returns:** `str`
- **Description:** Extract system prompt text (first system message).

#### `extract_messages`

```python
def extract_messages(data: Dict[str, Any]) -> List[OpenAIMessage]
```

- **Returns:** `List[OpenAIMessage]`
- **Description:** Extract messages from request.

#### `count_tokens_approx`

```python
def count_tokens_approx(data: Dict[str, Any]) -> int
```

- **Returns:** `int`
- **Description:** Approximate token count for request.

#### `is_streaming`

```python
def is_streaming(data: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Check if request is streaming.

#### `build_request`

```python
def build_request(model: str, messages: List[Dict[str, Any]], max_tokens: Optional[int] = None, stream: bool = True, **kwargs: object) -> bytes
```

- **Returns:** `bytes`
- **Description:** Build an OpenAI API request body.

#### `extract_response_tokens`

```python
def extract_response_tokens(body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract output token count from response.

### `tokenpak.proxy.providers.openai.OpenAIMessage`

**Bases:** object

Represents a message in OpenAI format.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `get_text`

```python
def get_text(self) -> str
```

- **Returns:** `str`
- **Description:** Extract text content from message.

### `tokenpak.proxy.providers.stream_translator.StreamingTranslator`

**Bases:** object

Stateful SSE stream translator between provider formats.

Usage::

    t = StreamingTranslator("anthropic", "openai")
    for raw_line in upstream_sse_lines:
        out = t.translate_chunk(raw_line)
        if out:
            for line in out:
                yield line + "\n\n"

Args:
    source_provider: "anthropic" | "openai" | "google"
    target_provider: "anthropic" | "openai" | "google"

#### `__init__`

```python
def __init__(self, source_provider: str, target_provider: str) -> None
```

- **Returns:** `None`

#### `translate_chunk`

```python
def translate_chunk(self, raw_line: str) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Translate one raw SSE line.

#### `translate_stream`

```python
def translate_stream(self, raw_lines: Iterator[str]) -> Iterator[str]
```

- **Returns:** `Iterator[str]`
- **Description:** Translate an iterator of raw SSE lines into translated SSE lines.

### `tokenpak.proxy.proxy_watchdog.CooldownManager`

**Bases:** object

Manage and auto-clear expired auth cooldowns.

Cooldown entries are stored in ~/.tokenpak/cooldowns.json:
{
    "anthropic:default": {"cooldownUntil": 1709000000, "errorCount": 3},
    ...
}
When cooldownUntil < now AND errorCount is low, the entry is cleared.

#### `__init__`

```python
def __init__(self, cooldowns_file: Path | None = None) -> None
```

- **Returns:** `None`

#### `clear_expired`

```python
def clear_expired(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Clear cooldowns where cooldownUntil < now. Returns list of cleared keys.

#### `check_auth_profiles`

```python
def check_auth_profiles(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Check auth-profiles.json for profiles with cooldownUntil set. Returns warnings.

### `tokenpak.proxy.proxy_watchdog.ProxyWatchdog`

**Bases:** object

Monitor and auto-heal proxy process.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `is_proxy_running`

```python
def is_proxy_running(self) -> bool
```

- **Returns:** `bool`
- **Description:** Check if proxy process is running and responding.

#### `is_port_listening`

```python
def is_port_listening(self) -> bool
```

- **Returns:** `bool`
- **Description:** Check if the proxy port is actually listening.

#### `restart_proxy`

```python
def restart_proxy(self) -> bool
```

- **Returns:** `bool`
- **Description:** Restart the proxy with exponential backoff.

#### `check_memory_usage`

```python
def check_memory_usage(self) -> None
```

- **Returns:** `None`
- **Description:** Warn if proxy memory exceeds 500MB.

#### `check_error_rate`

```python
def check_error_rate(self) -> None
```

- **Returns:** `None`
- **Description:** Warn if proxy error rate in session is high.

#### `clear_cooldowns`

```python
def clear_cooldowns(self) -> None
```

- **Returns:** `None`
- **Description:** Clear any expired cooldowns from state files.

#### `log_stats`

```python
def log_stats(self) -> None
```

- **Returns:** `None`
- **Description:** Log summary stats every hour.

#### `run`

```python
def run(self) -> None
```

- **Returns:** `None`
- **Description:** Main watchdog loop.

### `tokenpak.proxy.request.HTTPProxy`

**Bases:** object

Proxy dispatch interface for registry adapters.

Provides a clean API for adapters to forward requests through the
proxy pipeline. The actual pipeline logic lives in proxy.py (production)
or can be overridden for testing.

#### `__init__`

```python
def __init__(self, config: Optional[Dict[str, Any]] = None) -> Any
```

- **Returns:** `Any`

#### `handle_request`

```python
def handle_request(self, request: ProxyRequest, route: str = ROUTE_SDK, model: Optional[str] = None) -> ProxyResponse
```

- **Returns:** `ProxyResponse`
- **Description:** Forward a request through the proxy pipeline.

### `tokenpak.proxy.request.ProxyRequest`

**Bases:** object

Incoming proxy request — captures method, URL, headers, and body.

Used by registry adapters to pass requests through the proxy pipeline
without coupling to the HTTP server implementation.

#### `get_header`

```python
def get_header(self, name: str, default: str = '') -> str
```

- **Returns:** `str`
- **Description:** Case-insensitive header lookup.

### `tokenpak.proxy.request.ProxyResponse`

**Bases:** object

Upstream proxy response — captures status, headers, and body.

#### `get_header`

```python
def get_header(self, name: str, default: str = '') -> str
```

- **Returns:** `str`
- **Description:** Case-insensitive header lookup.

### `tokenpak.proxy.router.ProviderRouter`

**Bases:** object

Routes requests to appropriate LLM providers.

Detection priority:
1. Explicit path patterns (/v1/messages → Anthropic, /v1/chat/completions → OpenAI)
2. Header presence (x-api-key → Anthropic, Bearer → OpenAI)
3. Request body model field

#### `__init__`

```python
def __init__(self, custom_urls: Optional[Dict[str, str]] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize router with optional custom provider URLs.

#### `route`

```python
def route(self, path: str, headers: Dict[str, str], body: Optional[bytes] = None) -> RouteResult
```

- **Returns:** `RouteResult`
- **Raises:** `ValueError`
- **Description:** Route a request to the appropriate provider.

### `tokenpak.proxy.routes.ProxyRoutesMixin`

**Bases:** object

Mixin for ForwardProxyHandler providing GET routes, ingest, and static-file serving.

Mix in before BaseHTTPRequestHandler in the MRO:

    class ForwardProxyHandler(ProxyRoutesMixin, ProxyMiddlewareMixin, BaseHTTPRequestHandler): ...

#### `do_GET`

```python
def do_GET(self) -> None
```

- **Returns:** `None`

### `tokenpak.proxy.server.GracefulShutdown`

**Bases:** object

Coordinates graceful shutdown for the proxy.

Lifecycle
---------
1. ``begin()``          — signal that shutdown has started (new requests → 503)
2. ``track_request()``  — context manager: increment/decrement in-flight counter
3. ``wait_for_drain()`` — block until all in-flight requests finish or timeout

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `is_shutting_down`

```python
def is_shutting_down(self) -> bool
```

- **Returns:** `bool`

#### `begin`

```python
def begin(self) -> None
```

- **Returns:** `None`
- **Description:** Mark the start of shutdown. New requests will receive 503.

#### `track_request`

```python
def track_request(self) -> Iterator[None]
```

- **Returns:** `Iterator[None]`
- **Description:** Context manager that increments/decrements the in-flight counter.

#### `in_flight_count`

```python
def in_flight_count(self) -> int
```

- **Returns:** `int`

#### `wait_for_drain`

```python
def wait_for_drain(self, timeout: float = 30.0) -> bool
```

- **Returns:** `bool`
- **Description:** Block until all in-flight requests complete or *timeout* seconds elapse.

### `tokenpak.proxy.server.ProxyServer`

**Bases:** object

TokenPak HTTP proxy server.

Parameters
----------
host : str
    Bind host (default "0.0.0.0").
port : int
    Bind port (default from TOKENPAK_PORT env var or 8766).
compilation_mode : str
    "strict" | "hybrid" | "aggressive"
request_hook : callable, optional
    Called for each intercepted request before forwarding.
    Signature: (body: bytes, model: str, trace: PipelineTrace | None)
                -> (body, sent_tokens, raw_tokens, protected_tokens)

#### `__init__`

```python
def __init__(self, host: str = '127.0.0.1', port: int | None = None, compilation_mode: str | None = None, request_hook: RequestHook | None = None, shutdown_timeout: float | None = None) -> None
```

- **Returns:** `None`

#### `start`

```python
def start(self, blocking: bool = True) -> None
```

- **Returns:** `None`
- **Description:** Start the proxy server.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Serialize repeated/concurrent stop calls around the owned lifecycle.

#### `is_running`

```python
def is_running(self) -> bool
```

- **Returns:** `bool`

#### `health`

```python
def health(self, deep: bool = False) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `status`

```python
def status(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return a concise operational status snapshot for GET /status.

#### `stats`

```python
def stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `session_stats`

```python
def session_stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `last_request_stats`

```python
def last_request_stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `reset_session`

```python
def reset_session(self) -> None
```

- **Returns:** `None`

### `tokenpak.proxy.server_async.ConcurrencyLimiterMiddleware`

**Bases:** BaseHTTPMiddleware

Return HTTP 503 when MAX_CONCURRENCY in-flight requests are active.

#### `__init__`

```python
def __init__(self, app: ASGIApp, max_concurrency: int = MAX_CONCURRENCY) -> None
```

- **Returns:** `None`

#### `dispatch`

```python
async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response
```

- **Returns:** `Response`

### `tokenpak.proxy.server_extra.websocket_proxy.WebSocketConnectionManager`

**Bases:** object

Thread-safe (GIL-protected) manager for WebSocket connection lifecycle.

Tracks active and historical connections, enforces the max-connection
limit, and accumulates per-connection statistics.

Args:
    max_connections: Maximum number of simultaneous active connections.

#### `__init__`

```python
def __init__(self, max_connections: int = 100) -> None
```

- **Returns:** `None`

#### `can_accept`

```python
def can_accept(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the server can accept another connection.

#### `active_count`

```python
def active_count(self) -> int
```

- **Returns:** `int`
- **Description:** Return the number of currently active connections.

#### `register`

```python
def register(self, connection_id: str, client_address: str) -> bool
```

- **Returns:** `bool`
- **Description:** Register a new connection.

#### `unregister`

```python
def unregister(self, connection_id: str, close_code: Optional[int] = None) -> None
```

- **Returns:** `None`
- **Description:** Unregister an active connection.

#### `record_message`

```python
def record_message(self, connection_id: str) -> None
```

- **Returns:** `None`
- **Description:** Increment the messages-received counter for *connection_id*.

#### `record_chunk`

```python
def record_chunk(self, connection_id: str, compressed: int, uncompressed: int) -> None
```

- **Returns:** `None`
- **Description:** Record a compressed chunk sent to *connection_id*.

#### `record_upstream_error`

```python
def record_upstream_error(self, connection_id: str) -> None
```

- **Returns:** `None`
- **Description:** Increment the upstream-error counter for *connection_id*.

#### `get_stats`

```python
def get_stats(self, connection_id: str) -> Optional[WebSocketConnectionStats]
```

- **Returns:** `Optional[WebSocketConnectionStats]`
- **Description:** Return stats for *connection_id* (active or historical), or None.

#### `get_all_stats`

```python
def get_all_stats(self) -> List[dict[str, Any]]
```

- **Returns:** `List[dict[str, Any]]`
- **Description:** Return a list of serialised stats dicts for all tracked connections.

### `tokenpak.proxy.server_extra.websocket_proxy.WebSocketConnectionStats`

**Bases:** object

Per-connection statistics for a WebSocket client.

#### `compression_ratio`

```python
def compression_ratio(self) -> float
```

- **Returns:** `float`
- **Description:** Ratio of compressed to uncompressed bytes (lower = better compression).

#### `duration_seconds`

```python
def duration_seconds(self) -> float
```

- **Returns:** `float`
- **Description:** Total connection duration in seconds.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Serialise to a plain dict for JSON output / dashboard.

### `tokenpak.proxy.shadow_hook.ShadowHook`

**Bases:** object

Thin wrapper around RoutingLedger for proxy use.
Designed to be fail-silent — any error is caught and logged to stderr only.

#### `__init__`

```python
def __init__(self, ledger_path: str = DEFAULT_LEDGER_PATH, enabled: bool = True) -> Any
```

- **Returns:** `Any`

#### `record_request`

```python
def record_request(self, model: str, query: str, context_tokens: int = 0) -> Optional[int]
```

- **Returns:** `Optional[int]`
- **Description:** Called when a request is about to be forwarded to the LLM.

#### `record_response`

```python
def record_response(self, txn_key: Optional[int], response_text: str, response_tokens: int = 0, latency_ms: float = 0.0, context_blocks: Optional[list[str]] = None) -> Optional[int]
```

- **Returns:** `Optional[int]`
- **Description:** Called after the LLM response is received.

#### `record_feedback`

```python
def record_feedback(self, transaction_id: int, accepted: bool, reason: Optional[str] = None) -> bool
```

- **Returns:** `bool`
- **Description:** Record user feedback (retry = rejected, continued = accepted).

#### `get_stats`

```python
def get_stats(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return ledger stats, or empty dict on failure.

### `tokenpak.proxy.shadow_reader.ShadowReader`

**Bases:** object

Passive request observer for Phase 3 testing.

#### `__init__`

```python
def __init__(self, shadow_log_path: Path | None = None) -> None
```

- **Returns:** `None`

#### `observe_request`

```python
def observe_request(self, method: str, path: str, headers: dict[str, str], body_size: int, model: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Log incoming request observation. Returns observation_id.

#### `observe_response`

```python
def observe_response(self, obs_id: str, status: int, headers: dict[str, str], body_size: int, latency_ms: float) -> None
```

- **Returns:** `None`
- **Description:** Log outgoing response observation.

#### `observe_metric`

```python
def observe_metric(self, metric_name: str, metric_value: float, tags: Optional[dict[str, str]] = None) -> None
```

- **Returns:** `None`
- **Description:** Log a metric observation.

#### `mark_compression_analysis`

```python
def mark_compression_analysis(self, obs_id: str, applicable: bool, gain_tokens: Optional[int] = None, cost_change: Optional[float] = None, safety_concern: Optional[str] = None) -> None
```

- **Returns:** `None`
- **Description:** Annotate observation with post-processing analysis.

#### `flush`

```python
def flush(self) -> None
```

- **Returns:** `None`
- **Description:** Explicit flush (thread-safe).

#### `get_stats`

```python
def get_stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return shadow reader statistics.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Stop the flush thread cleanly.

### `tokenpak.proxy.spend_guard.contracts.GuardOutcome`

**Bases:** object

Tagged result returned by ``evaluate``.

The proxy hook reads ``kind`` and acts accordingly:
- ``forward`` / ``forward_modified`` / ``replay`` → write ``body`` upstream
- ``block`` / ``hard_block`` / ``estimate`` / ``cancel`` / ``reprompt`` →
  write ``response_body`` to the client with ``http_status``

#### `passthrough`

```python
def passthrough(cls, body: bytes) -> 'GuardOutcome'
```

- **Returns:** `'GuardOutcome'`
- **Description:** Default no-op outcome — guard disabled or estimator allowed.

### `tokenpak.proxy.spend_guard.pending.PendingStore`

**Bases:** object

SQLite-backed pending-request store.

Instances are cheap — they hold only a path. Each method opens a fresh
connection to keep the proxy thread-safe (BaseHTTPServer is per-request).

#### `__init__`

```python
def __init__(self, audit_db_path: str = '~/.tokenpak/spend_guard.db') -> Any
```

- **Returns:** `Any`

#### `store`

```python
def store(self, *, session_id: str, body: bytes, headers: dict[str, Any], target_url: str, provider: str, model: str, projected_tokens: int, projected_cost_usd: float, ttl_seconds: int = 600) -> PendingRequest
```

- **Returns:** `PendingRequest`
- **Description:** Insert a new pending request and return it.

#### `get_by_session`

```python
def get_by_session(self, session_id: str) -> Optional[PendingRequest]
```

- **Returns:** `Optional[PendingRequest]`
- **Description:** Most recent pending request for the given session, or None.

#### `get_by_id`

```python
def get_by_id(self, pending_id: str) -> Optional[PendingRequest]
```

- **Returns:** `Optional[PendingRequest]`

#### `recent_block_by_hash`

```python
def recent_block_by_hash(self, request_hash: str, within_seconds: float = 30.0) -> Optional[PendingRequest]
```

- **Returns:** `Optional[PendingRequest]`
- **Description:** Anti-loop: was this exact request_hash blocked recently?

#### `consume`

```python
def consume(self, pending_id: str) -> Optional[PendingRequest]
```

- **Returns:** `Optional[PendingRequest]`
- **Description:** Return the pending request and mark it consumed.

#### `discard`

```python
def discard(self, pending_id: str) -> bool
```

- **Returns:** `bool`

#### `expire_old`

```python
def expire_old(self) -> int
```

- **Returns:** `int`
- **Description:** Mark all pending rows past their expires_at as expired. Returns count.

### `tokenpak.proxy.spend_guard.receipt.ProofField`

**Bases:** object

A single proof datum: proven (a known value) or explicitly unavailable.

Honesty contract (AC-3): a field the runtime cannot observe is represented as
``available=False`` with a machine ``reason`` token — never a guessed value,
never a missing key.

#### `known`

```python
def known(cls, value: Any) -> 'ProofField'
```

- **Returns:** `'ProofField'`
- **Description:** A proven datum the runtime actually observed.

#### `unavailable`

```python
def unavailable(cls, reason: str) -> 'ProofField'
```

- **Returns:** `'ProofField'`
- **Description:** An explicitly-unavailable datum with a machine ``reason`` token.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.proxy.spend_guard.receipt.ReceiptDebugPointer`

**Bases:** object

Redaction-safe pointer to the request's debug capture (C18).

By construction this never embeds request/response plaintext — only the
``trace_id`` and the capture ``mode`` (``off`` / ``encrypted`` / ``hash_only``).
``path`` (the on-disk capture file, which reveals the OS user's home dir) is
dropped when the receipt is rendered with ``redact=True``.

#### `to_dict`

```python
def to_dict(self, *, redact: bool = True) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.proxy.spend_guard.receipt.RequestReceiptV1`

**Bases:** object

Canonical request-level proof object (schema ``receipt.v1``).

#### `to_dict`

```python
def to_dict(self, *, redact: bool = True) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Serialize to a plain dict. ``redact`` drops the debug capture path.

### `tokenpak.proxy.stats.CompressionStats`

**Bases:** object

Thread-safe compression telemetry collector.

#### `__init__`

```python
def __init__(self, log_path: 'str | _pathlib.Path | None' = None) -> None
```

- **Returns:** `None`

#### `record_compression`

```python
def record_compression(self, model: str, tokens_in: int, tokens_out: int, ratio: float, latency_ms: int, status: str = 'ok') -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `flush_shutdown_record`

```python
def flush_shutdown_record(self, record: Mapping[str, object]) -> bool
```

- **Returns:** `bool`
- **Description:** Append a shutdown summary record to the events JSONL file.

#### `get_stats`

```python
def get_stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.proxy.stats.StatsCollector`

**Bases:** object

Thread-safe metrics collector.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `record_request`

```python
def record_request(self, model: str = 'unknown', tokens_in: int = 0, tokens_out: int = 0, compressed: bool = False, tokens_saved: int = 0, latency_ms: float = 0.0) -> None
```

- **Returns:** `None`
- **Description:** Record a completed proxy request.

#### `record_error`

```python
def record_error(self, error_code: str) -> None
```

- **Returns:** `None`
- **Description:** Record a proxy error by code (e.g. 'AUTH_001').

#### `record_vault_search`

```python
def record_vault_search(self, hit: bool) -> None
```

- **Returns:** `None`
- **Description:** Record a vault search cache event.

#### `reset`

```python
def reset(self) -> None
```

- **Returns:** `None`
- **Description:** Reset all counters (start_time included).

#### `snapshot`

```python
def snapshot(self) -> StatsSnapshot
```

- **Returns:** `StatsSnapshot`
- **Description:** Return a JSON-serialisable metrics snapshot.

#### `to_dict`

```python
def to_dict(self) -> StatsSnapshot
```

- **Returns:** `StatsSnapshot`
- **Description:** Return the metrics snapshot through the legacy method name.

#### `to_text`

```python
def to_text(self) -> str
```

- **Returns:** `str`
- **Description:** Return plaintext representation suitable for shell tooling.

### `tokenpak.proxy.stats_api.StatsAPI`

**Bases:** object

Handles HTTP requests for stats endpoints.

#### `handle_stats_last`

```python
def handle_stats_last() -> tuple[str, dict[str, str]]
```

- **Returns:** `tuple[str, dict[str, str]]`
- **Description:** Handle GET /stats/last request.

#### `handle_stats_session`

```python
def handle_stats_session() -> tuple[str, dict[str, str]]
```

- **Returns:** `tuple[str, dict[str, str]]`
- **Description:** Handle GET /stats/session request.

#### `route`

```python
def route(path: str) -> tuple[str, dict[str, str]] | None
```

- **Returns:** `tuple[str, dict[str, str]] | None`
- **Description:** Route HTTP requests to appropriate handler.

### `tokenpak.proxy.streaming.StreamHandler`

**Bases:** object

Handles streaming responses with buffering and metrics extraction.

Supports gzip decompression and chunk-by-chunk forwarding.

#### `__init__`

```python
def __init__(self, content_encoding: str = '') -> Any
```

- **Returns:** `Any`

#### `process_chunk`

```python
def process_chunk(self, chunk: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Process a chunk: decompress if needed, buffer for later analysis.

#### `get_buffer`

```python
def get_buffer(self) -> bytes
```

- **Returns:** `bytes`
- **Description:** Get all buffered data, flushing any partial line held in the line buffer.

#### `extract_usage`

```python
def extract_usage(self) -> Dict[str, int]
```

- **Returns:** `Dict[str, int]`
- **Description:** Extract usage metrics from buffered stream.

#### `chunk_count`

```python
def chunk_count(self) -> int
```

- **Returns:** `int`
- **Description:** Number of chunks processed.

### `tokenpak.proxy.streaming.StreamUsage`

**Bases:** object

Usage metrics extracted from streaming response.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, int]
```

- **Returns:** `Dict[str, int]`

### `tokenpak.proxy.tool_schema_registry.ToolSchemaRegistry`

**Bases:** object

Singleton registry that freezes tool schemas for prompt-cache stability.

Thread-safe. The frozen text is updated only when tools actually change.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `normalize_request`

```python
def normalize_request(self, body_bytes: bytes) -> tuple[bytes, bool]
```

- **Returns:** `tuple[bytes, bool]`
- **Description:** Parse the request body, normalize its ``tools`` array (if present),

#### `get_frozen_text`

```python
def get_frozen_text(self) -> str | None
```

- **Returns:** `str | None`
- **Description:** Return the current frozen tools JSON text (for diagnostics).

#### `get_frozen_hash`

```python
def get_frozen_hash(self) -> str | None
```

- **Returns:** `str | None`
- **Description:** Return SHA-256 of frozen tools (first 16 hex chars).

#### `stats`

```python
def stats(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.proxy.tracing.PipelineTrace`

**Bases:** object

Complete trace for a request through the pipeline.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.proxy.tracing.StageTrace`

**Bases:** object

Trace for a single pipeline stage.

Proxy-local DTO, distinct from the canonical TIP ``StageTrace`` contract
despite the shared historical name. The module path and class name are
kept as-is: they are compatibility surface for reflection and default
pickle globals across releases.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.proxy.tracing.TraceStorage`

**Bases:** object

Thread-safe storage for recent pipeline traces.

#### `__init__`

```python
def __init__(self, max_traces: int = 10) -> None
```

- **Returns:** `None`

#### `store`

```python
def store(self, trace: PipelineTrace) -> None
```

- **Returns:** `None`
- **Description:** Store a completed trace.

#### `get_last`

```python
def get_last(self) -> Optional[PipelineTrace]
```

- **Returns:** `Optional[PipelineTrace]`
- **Description:** Get the most recent trace.

#### `get_by_id`

```python
def get_by_id(self, request_id: str) -> Optional[PipelineTrace]
```

- **Returns:** `Optional[PipelineTrace]`
- **Description:** Get a specific trace by ID.

#### `get_all`

```python
def get_all(self) -> List[PipelineTrace]
```

- **Returns:** `List[PipelineTrace]`
- **Description:** Get all stored traces.

### `tokenpak.proxy.upstream_retry.UpstreamRetryPolicy`

**Bases:** object

Shared bounded retry behavior for streaming and non-streaming sends.

#### `from_env`

```python
def from_env(cls, body: bytes | None = None, headers: Mapping[str, object] | None = None) -> 'UpstreamRetryPolicy'
```

- **Returns:** `'UpstreamRetryPolicy'`

#### `retryable_exceptions`

```python
def retryable_exceptions(self) -> tuple[type[Exception], ...]
```

- **Returns:** `tuple[type[Exception], ...]`

#### `is_retryable_exception`

```python
def is_retryable_exception(self, exc: Exception) -> bool
```

- **Returns:** `bool`

#### `retry_for_exception`

```python
def retry_for_exception(self, exc: Exception, attempt: int, *, stream_started: bool) -> RetryDecision
```

- **Returns:** `RetryDecision`

#### `retry_for_response`

```python
def retry_for_response(self, status_code: int, headers: Mapping[str, object] | None, attempt: int, *, stream_started: bool) -> RetryDecision
```

- **Returns:** `RetryDecision`

#### `retry_for_truncated_json`

```python
def retry_for_truncated_json(self, attempt: int, *, stream_started: bool) -> RetryDecision
```

- **Returns:** `RetryDecision`

### `tokenpak.proxy.vault_bridge.VaultIndex`

**Bases:** object

Read-only BM25-searchable index loaded from .tokenpak/index.json + blocks/.
Reloads periodically to pick up git-pulled changes.

#### `__init__`

```python
def __init__(self, tokenpak_dir: str) -> None
```

- **Returns:** `None`

#### `available`

```python
def available(self) -> bool
```

- **Returns:** `bool`

#### `blocks`

```python
def blocks(self) -> dict[str, Block]
```

- **Returns:** `dict[str, Block]`

#### `maybe_reload`

```python
def maybe_reload(self) -> None
```

- **Returns:** `None`
- **Description:** Reload if index file changed or enough time passed.

#### `cache_stats`

```python
def cache_stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return a thread-safe snapshot of bounded content-cache metrics.

#### `search`

```python
def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> list[SearchResult]
```

- **Returns:** `list[SearchResult]`
- **Description:** BM25 search across vault blocks. Returns [(block_dict, score), ...].

#### `compile_injection`

```python
def compile_injection(self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0) -> tuple[str, int, list[str]]
```

- **Returns:** `tuple[str, int, list[str]]`
- **Description:** Search vault and compile injection text within budget.

### `tokenpak.routing.broker.Broker`

**Bases:** object

Autonomous routing broker. Thread-safe.

Uses RoutingLedger for historical acceptance rates and EloRatings for
per-model performance tracking.

#### `__init__`

```python
def __init__(self, ledger_path: str = DEFAULT_LEDGER_PATH, elo_path: str = DEFAULT_ELO_PATH, tiers_path: str = DEFAULT_TIERS_PATH, min_samples: int = MIN_SAMPLES) -> Any
```

- **Returns:** `Any`

#### `route`

```python
def route(self, model: str, task_type: str, complexity_score: float, force_model: bool = False) -> RoutingDecision
```

- **Returns:** `RoutingDecision`
- **Description:** Decide whether to pass-through, downgrade, or upgrade a request.

#### `record_outcome`

```python
def record_outcome(self, transaction_id: int, accepted: bool, reason: Optional[str] = None) -> bool
```

- **Returns:** `bool`
- **Description:** Record outcome and update Elo. Trigger cooldown on rejected downgrade.

#### `is_confident`

```python
def is_confident(self, model: str, task_type: str) -> bool
```

- **Returns:** `bool`
- **Description:** Return True when sample count meets the minimum threshold.

### `tokenpak.routing.fallback.FallbackExhaustedError`

**Bases:** Exception

All fallback levels exhausted.

Attributes:
    context:  The task context dict passed to the router.
    cause:    The underlying :class:`RetryExhaustedError` (or
              :class:`ImmediateAlertError` for auth failures).

#### `__init__`

```python
def __init__(self, context: FallbackContext, cause: Exception) -> None
```

- **Returns:** `None`

### `tokenpak.routing.fallback.FallbackRouter`

**Bases:** object

High-level fallback router for the proxy layer.

Parameters
----------
state_dir:
    Directory for persisting partial state on failure.
    Defaults to ``~/.tokenpak/retry_state``.
failover_manager:
    Optional :class:`~tokenpak.proxy.failover.FailoverManager`.
    When attached and enabled, its ``iter_providers`` drives the
    provider-switch hook.
on_handoff:
    Called when Level 3 (handoff) is reached.
    Signature: ``(context, partial_state) -> bool``.
    Return ``True`` to accept the handoff (router returns
    ``{"_handoff": True}``).  Return ``False`` to escalate further.
on_human_alert:
    Called when Level 4 (human alert) is reached.
    Signature: ``(alert_dict) -> None``.

#### `__init__`

```python
def __init__(self, state_dir: Optional[Path | str] = None, failover_manager: Optional[_FailoverManager] = None, on_handoff: Optional[Callable[[FallbackContext, FallbackContext], bool]] = None, on_human_alert: Optional[Callable[[FallbackContext], None]] = None) -> None
```

- **Returns:** `None`

#### `call`

```python
def call(self, fn: Callable[[FallbackContext, FallbackContext], FallbackResult], context: FallbackContext, partial_state: Optional[FallbackContext] = None) -> FallbackResult | dict[str, bool]
```

- **Returns:** `FallbackResult | dict[str, bool]`
- **Description:** Execute *fn* with automatic fallback.

### `tokenpak.routing.intent_policy.PolicyResult`

**Bases:** object

Immutable result from intent policy resolution.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.routing.route_selector.Route`

**Bases:** object

A candidate route entry managed by :class:`RouteSelector`.

Attributes:
    name:           Unique route identifier (e.g. ``"primary"``).
    provider:       Target provider string (e.g. ``"anthropic"``).
    priority:       Lower number = higher priority in the ``"priority"``
                    strategy.  Defaults to ``100``.
    cost_per_token: Estimated cost per token in USD.  Used by the
                    ``"cost"`` strategy.  Defaults to ``0.0``.
    enabled:        Whether the route is eligible for selection.

#### `to_dict`

```python
def to_dict(self) -> Dict
```

- **Returns:** `Dict`
- **Description:** Serialise to a plain dictionary.

### `tokenpak.routing.route_selector.RouteSelector`

**Bases:** object

Thread-safe pool of candidate routes with pluggable selection strategies.

Parameters
----------
rng_seed:
    Optional seed for the random-number generator used by the ``"random"``
    strategy.  Useful for reproducible tests.

Examples
--------
>>> sel = RouteSelector()
>>> sel.add_route("a", "openai",    priority=2, cost_per_token=0.00001)
>>> sel.add_route("b", "anthropic", priority=1, cost_per_token=0.00005)
>>> sel.select("priority").name
'b'
>>> sel.select("cost").name
'a'

#### `__init__`

```python
def __init__(self, *, rng_seed: Optional[int] = None) -> None
```

- **Returns:** `None`

#### `add_route`

```python
def add_route(self, name: str, provider: str, *, priority: int = 100, cost_per_token: float = 0.0, enabled: bool = True) -> Route
```

- **Returns:** `Route`
- **Description:** Add (or replace) a route in the pool.

#### `disable`

```python
def disable(self, name: str) -> bool
```

- **Returns:** `bool`
- **Description:** Disable the named route so it is excluded from selection.

#### `enable`

```python
def enable(self, name: str) -> bool
```

- **Returns:** `bool`
- **Description:** Re-enable a previously disabled route.

#### `remove`

```python
def remove(self, name: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove a route from the pool entirely.

#### `list_available`

```python
def list_available(self) -> List[Route]
```

- **Returns:** `List[Route]`
- **Description:** Return all **enabled** routes, sorted by priority then name.

#### `list_all`

```python
def list_all(self) -> List[Route]
```

- **Returns:** `List[Route]`
- **Description:** Return all routes (enabled and disabled), sorted by priority.

#### `get`

```python
def get(self, name: str) -> Optional[Route]
```

- **Returns:** `Optional[Route]`
- **Description:** Return the named route, or *None* if not found.

#### `select`

```python
def select(self, strategy: str = 'priority') -> Optional[Route]
```

- **Returns:** `Optional[Route]`
- **Description:** Select the best route according to *strategy*.

### `tokenpak.routing.router.RouteEntry`

**Bases:** object

Internal record for a registered route.

Attributes:
    model_name: The model identifier string.
    provider:   Target provider string (e.g. ``"openai"``).
    enabled:    Whether this route is active.  Disabled routes are
                invisible to :meth:`Router.route` and
                :meth:`Router.get_provider`.

#### `__init__`

```python
def __init__(self, model_name: str, provider: str, *, enabled: bool = True) -> None
```

- **Returns:** `None`

### `tokenpak.routing.router.Router`

**Bases:** object

Thread-safe model-name → provider router.

Parameters
----------
default_provider:
    Provider to fall back to when a model is not found in the registry.
    If *None* and no matching route exists, :meth:`route` raises
    :class:`RoutingError`.
load_defaults:
    If *True* (default), pre-populate the registry with
    :data:`DEFAULT_ROUTES`.

Examples
--------
>>> r = Router(default_provider="anthropic")
>>> r.register("gpt-4o", "openai")
>>> r.route("gpt-4o")
'openai'
>>> r.route("unknown-model")
'anthropic'

#### `__init__`

```python
def __init__(self, default_provider: Optional[str] = None, *, load_defaults: bool = True) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, model_name: str, provider: str, *, enabled: bool = True) -> None
```

- **Returns:** `None`
- **Description:** Register (or update) a model → provider mapping.

#### `route`

```python
def route(self, model_name: str) -> str
```

- **Returns:** `str`
- **Description:** Return the provider for *model_name*.

#### `get_provider`

```python
def get_provider(self, model_name: str) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Return the provider for *model_name*, or *None* if not found/disabled.

#### `list_routes`

```python
def list_routes(self) -> Dict[str, Dict]
```

- **Returns:** `Dict[str, Dict]`
- **Description:** Return a snapshot of all registered routes.

#### `disable`

```python
def disable(self, model_name: str) -> bool
```

- **Returns:** `bool`
- **Description:** Disable the route for *model_name*.

#### `enable`

```python
def enable(self, model_name: str) -> bool
```

- **Returns:** `bool`
- **Description:** Re-enable a previously disabled route.

#### `set_default`

```python
def set_default(self, provider: Optional[str]) -> None
```

- **Returns:** `None`
- **Description:** Update the fallback default provider.

#### `default_provider`

```python
def default_provider(self) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** The configured fallback provider (read-only view).

### `tokenpak.routing.routing_ledger.RoutingLedger`

**Bases:** object

Thread-safe SQLite ledger for LLM transaction logging.
Uses WAL mode for concurrent readers + single writer.

Write operations never propagate ``sqlite3.OperationalError`` (e.g.
"database is locked") to callers: the error is counted and logged and a
sentinel value is returned instead, so a busy ledger cannot crash the
request path it is observing.

#### `__init__`

```python
def __init__(self, db_path: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `log_transaction`

```python
def log_transaction(self, model: str, query: str, context_blocks: list[str], response: str, accepted: Optional[bool] = None, rejection_reason: Optional[str] = None, latency_ms: float = 0.0, context_tokens: int = 0, response_tokens: int = 0, routing_action: str = 'passthrough') -> int
```

- **Returns:** `int`
- **Description:** Log a single LLM transaction.

#### `record_outcome`

```python
def record_outcome(self, transaction_id: int, accepted: bool, rejection_reason: Optional[str] = None) -> bool
```

- **Returns:** `bool`
- **Description:** Update the acceptance status of an existing transaction.

#### `get_transaction`

```python
def get_transaction(self, transaction_id: int) -> Optional[dict[str, object]]
```

- **Returns:** `Optional[dict[str, object]]`
- **Description:** Fetch a single transaction by ID.

#### `get_recent`

```python
def get_recent(self, limit: int = 100) -> list[dict[str, object]]
```

- **Returns:** `list[dict[str, object]]`
- **Description:** Return the most recent N transactions.

#### `get_stats`

```python
def get_stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return aggregate statistics from the ledger.

#### `sample_count`

```python
def sample_count(self, model: str, task_type: str) -> int
```

- **Returns:** `int`
- **Description:** Return number of transactions for (model, task_type) with known outcome.

#### `acceptance_rate`

```python
def acceptance_rate(self, model: str, task_type: str) -> float
```

- **Returns:** `float`
- **Description:** Return acceptance rate for (model, task_type). Returns 0.0 if no data.

#### `wal_mode_active`

```python
def wal_mode_active(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if WAL journal mode is active.

### `tokenpak.routing.rules.RouteEngine`

**Bases:** object

Evaluate routing rules against a request and return the first match.

#### `__init__`

```python
def __init__(self, store: Optional[RouteStore] = None) -> Any
```

- **Returns:** `Any`

#### `match`

```python
def match(self, *, model: str = '', prompt: str = '', token_count: Optional[int] = None, rules: Optional[List[RouteRule]] = None) -> Optional[RouteRule]
```

- **Returns:** `Optional[RouteRule]`
- **Description:** Return the first matching enabled rule (lowest priority wins).

#### `match_payload`

```python
def match_payload(self, payload: Dict[str, Any]) -> Optional[RouteRule]
```

- **Returns:** `Optional[RouteRule]`
- **Description:** Convenience wrapper that accepts a raw OpenAI-style request dict.

### `tokenpak.routing.rules.RoutePattern`

**Bases:** object

Pattern conditions for a routing rule.

At least one field must be set.  All set fields must match (AND logic).

#### `is_empty`

```python
def is_empty(self) -> bool
```

- **Returns:** `bool`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, d: Dict[str, Any]) -> 'RoutePattern'
```

- **Returns:** `'RoutePattern'`

### `tokenpak.routing.rules.RouteRule`

**Bases:** object

A single routing rule.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, d: Dict[str, Any]) -> 'RouteRule'
```

- **Returns:** `'RouteRule'`

### `tokenpak.routing.rules.RouteStore`

**Bases:** object

Persist routing rules to ~/.tokenpak/routes.yaml.

#### `__init__`

```python
def __init__(self, path: str = DEFAULT_ROUTES_PATH) -> Any
```

- **Returns:** `Any`

#### `list`

```python
def list(self) -> List[RouteRule]
```

- **Returns:** `List[RouteRule]`
- **Description:** Return all rules, sorted by priority then created_at.

#### `add`

```python
def add(self, pattern: RoutePattern, target: str, priority: int = 100, description: str = '') -> RouteRule
```

- **Returns:** `RouteRule`
- **Description:** Add a new rule and persist it. Returns the created rule.

#### `remove`

```python
def remove(self, rule_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Remove rule by id. Returns True if found and removed.

#### `get`

```python
def get(self, rule_id: str) -> Optional[RouteRule]
```

- **Returns:** `Optional[RouteRule]`
- **Description:** Return a single rule by id, or None.

#### `set_enabled`

```python
def set_enabled(self, rule_id: str, enabled: bool) -> bool
```

- **Returns:** `bool`
- **Description:** Enable or disable a rule by id. Returns True if found.

### `tokenpak.sdk.anthropic.AnthropicAdapter`

**Bases:** TokenPakAdapter

TokenPak adapter for the Anthropic Messages API.

Usage
-----
>>> adapter = AnthropicAdapter(
...     base_url="http://127.0.0.1:8767",
...     api_key="sk-ant-...",
... )
>>> response = adapter.call({
...     "model": "claude-3-5-sonnet-20241022",
...     "max_tokens": 1024,
...     "messages": [{"role": "user", "content": "Hello"}],
... })
>>> tokens = adapter.extract_tokens(response)

#### `prepare_request`

```python
def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Validate and normalise an Anthropic request.

#### `send`

```python
def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** POST to ``{base_url}/v1/messages`` through the proxy.

#### `parse_response`

```python
def parse_response(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Validate proxy response and surface provider errors.

#### `extract_tokens`

```python
def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Extract Anthropic usage block.

### `tokenpak.sdk.autogen.assistant.TokenPakAssistant`

**Bases:** object

#### `__init__`

```python
def __init__(self, agent: Any, budget: int = 6000) -> None
```

- **Returns:** `None`

#### `compress_history`

```python
def compress_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`

#### `initiate_chat`

```python
def initiate_chat(self, recipient: Any, message: str, **kwargs: Any) -> Any
```

- **Returns:** `Any`

#### `generate_reply`

```python
def generate_reply(self, messages: Optional[List[Dict[str, Any]]] = None, sender: Optional[Any] = None, **kwargs: Any) -> Any
```

- **Returns:** `Any`

#### `budget_status`

```python
def budget_status(self) -> Dict[str, int]
```

- **Returns:** `Dict[str, int]`

### `tokenpak.sdk.autogen.context.TokenPakAssistant`

**Bases:** object

Lightweight AutoGen-compatible assistant with TokenPak budget management.

Provides message receiving, context compression, and handoff integration
for AutoGen multi-agent workflows.

#### `__init__`

```python
def __init__(self, name: str, budget: int = 4000, manager = None) -> Any
```

- **Returns:** `Any`

#### `receive_message`

```python
def receive_message(self, content: str, sender_name: str = 'user') -> None
```

- **Returns:** `None`
- **Description:** Record an incoming message.

#### `get_messages`

```python
def get_messages(self, compress: bool = False) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Return messages, optionally compressed to budget.

#### `prepare_handoff`

```python
def prepare_handoff(self, to_agent: str = '', target: str = '', what_was_done: str = '', whats_next: str = '', extra_blocks: Optional[List[Any]] = None, **kwargs) -> str
```

- **Returns:** `str`
- **Description:** Prepare a HandoffWire JSON string for another agent.

#### `apply_handoff_wire`

```python
def apply_handoff_wire(self, wire: str) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Apply a HandoffWire JSON string, loading its context into this agent.

### `tokenpak.sdk.autogen.context.TokenPakCompressionReport`

**Bases:** object

Report of compression metrics for an AutoGen conversation.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert report to dictionary.

### `tokenpak.sdk.autogen.context.TokenPakConversationHook`

**Bases:** object

Hook for AutoGen agents to apply TokenPak context compression.

This hook intercepts AutoGen conversation context assembly and applies
TokenPak compression to system prompts, conversation history, and tool
definitions. It integrates transparently with AutoGen agents without
requiring API modifications.

Example:
    >>> hook = TokenPakConversationHook()
    >>> agent = AssistantAgent("agent", llm_config={...})
    >>> hook.compress_agent(agent)
    >>> # Conversation proceeds normally; compression applied automatically

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`
- **Description:** Initialize TokenPakConversationHook.

#### `compress_agent`

```python
def compress_agent(self, agent: Any, config: Optional[AgentContextConfig] = None) -> None
```

- **Returns:** `None`
- **Description:** Patch an AutoGen agent to apply TokenPak compression.

#### `restore_agent`

```python
def restore_agent(self, agent: Any) -> None
```

- **Returns:** `None`
- **Description:** Restore original get_context method (remove compression hook).

#### `get_report`

```python
def get_report(self, agent_name: str) -> Optional[TokenPakCompressionReport]
```

- **Returns:** `Optional[TokenPakCompressionReport]`
- **Description:** Get compression report for an agent.

### `tokenpak.sdk.autogen.groupchat.TokenPakGroupChat`

**Bases:** object

#### `__init__`

```python
def __init__(self, groupchat: Any, manager: Any, budget: int = 4000) -> None
```

- **Returns:** `None`

#### `get_compressed_history`

```python
def get_compressed_history(self) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`

#### `run`

```python
def run(self, initiator: Any, message: str, **kwargs: Any) -> Any
```

- **Returns:** `Any`

#### `message_count`

```python
def message_count(self) -> int
```

- **Returns:** `int`

#### `budget_status`

```python
def budget_status(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.sdk.autogen.message.TokenPakMessage`

**Bases:** object

#### `__init__`

```python
def __init__(self, role: str, content: str, budget: Optional[int] = None, avg_tokens_per_char: float = 0.25) -> None
```

- **Returns:** `None`

#### `content`

```python
def content(self) -> str
```

- **Returns:** `str`

#### `token_count`

```python
def token_count(self) -> int
```

- **Returns:** `int`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`

### `tokenpak.sdk.base.TokenPakAdapter`

**Bases:** ABC

Abstract base class for all TokenPak SDK/framework adapters.

Parameters
----------
base_url:
    TokenPak proxy endpoint, e.g. ``"http://127.0.0.1:8767"``.
    Must not have a trailing slash.
api_key:
    Provider API key forwarded transparently through the proxy.
timeout_s:
    Request timeout in seconds.  Defaults to 120.

#### `__init__`

```python
def __init__(self, base_url: str, api_key: str = '', timeout_s: float | None = None) -> None
```

- **Returns:** `None`

#### `prepare_request`

```python
def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Validate and normalise an SDK request dict into proxy format.

#### `send`

```python
def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** POST *prepared_request* to the TokenPak proxy and return the response.

#### `parse_response`

```python
def parse_response(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Convert a raw proxy response into the provider's native SDK format.

#### `extract_tokens`

```python
def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Extract token usage counts from a response.

#### `call`

```python
def call(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Full pipeline: prepare → send → parse_response.

### `tokenpak.sdk.base.TokenPakAdapterError`

**Bases:** Exception

Base exception for all TokenPak adapter errors.

#### `__init__`

```python
def __init__(self, message: str, status_code: int | None = None, raw: Any = None) -> None
```

- **Returns:** `None`

### `tokenpak.sdk.claude_cli.ClaudeCLIAdapter`

**Bases:** TokenPakAdapter

Adapter for Claude Code CLI environments.

In Claude CLI mode, the proxy handles auth — api_key is optional.
Requests are forwarded as-is (no normalization needed).

#### `__init__`

```python
def __init__(self, base_url: str = '', api_key: str = '') -> None
```

- **Returns:** `None`

#### `prepare_request`

```python
def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `send`

```python
def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** POST to the TokenPak proxy.

#### `parse_response`

```python
def parse_response(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `extract_tokens`

```python
def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.sdk.crewai.context.TokenPakContext`

**Bases:** object

#### `__init__`

```python
def __init__(self, total_budget = 8000, avg_tokens_per_char = 0.25) -> Any
```

- **Returns:** `Any`

#### `register_agent`

```python
def register_agent(self, agent_name, budget) -> Any
```

- **Returns:** `Any`

#### `add_context`

```python
def add_context(self, agent_name, content) -> Any
```

- **Returns:** `Any`

#### `get_context`

```python
def get_context(self, agent_name) -> Any
```

- **Returns:** `Any`

#### `get_usage`

```python
def get_usage(self, agent_name) -> Any
```

- **Returns:** `Any`

### `tokenpak.sdk.crewai.crew.TokenPakCrew`

**Bases:** object

#### `__init__`

```python
def __init__(self, crew, total_budget = 8000) -> Any
```

- **Returns:** `Any`

#### `kickoff`

```python
def kickoff(self, inputs = None) -> Any
```

- **Returns:** `Any`

#### `budget_status`

```python
def budget_status(self) -> Any
```

- **Returns:** `Any`

### `tokenpak.sdk.crewai.handoff.TokenPakHandoff`

**Bases:** object

#### `__init__`

```python
def __init__(self, budget = 2000, avg_tokens_per_char = 0.25) -> Any
```

- **Returns:** `Any`

#### `compress`

```python
def compress(self, state: Dict[str, Any]) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `package`

```python
def package(self, agent_output, metadata = None) -> Any
```

- **Returns:** `Any`

### `tokenpak.sdk.generic.GenericAdapter`

**Bases:** TokenPakAdapter

Generic fallback adapter for unknown environments.

Forwards requests as-is through the proxy with minimal transformation.

#### `__init__`

```python
def __init__(self, base_url: str = '', api_key: str = '') -> None
```

- **Returns:** `None`

#### `prepare_request`

```python
def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `send`

```python
def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** POST to the TokenPak proxy.

#### `parse_response`

```python
def parse_response(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `extract_tokens`

```python
def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.sdk.integrations.litellm.middleware.TokenPakMiddleware`

**Bases:** object

LiteLLM Router middleware that compiles Paks before sending.

Args:
    compaction: Default compaction strategy for all calls.
        ``"none"`` — no compaction (raw blocks concatenated)
        ``"balanced"`` — heuristic compaction (default)
        ``"aggressive"`` — hard-truncate to fit budget
    budget: Default token budget.  Per-call ``tokenpak_budget=`` overrides this.
    telemetry: Whether to attach ``tokenpak_stats`` to responses.

#### `__init__`

```python
def __init__(self, compaction: str = 'balanced', budget: int = 8000, telemetry: bool = True) -> None
```

- **Returns:** `None`

#### `pre_call_hook`

```python
def pre_call_hook(self, user_api_key_dict: Any, cache: Any, data: Dict[str, Any], call_type: str) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Called by LiteLLM Router before forwarding to provider.

#### `post_call_success_hook`

```python
def post_call_success_hook(self, data: Dict[str, Any], user_api_key_dict: Any, response: Any) -> Any
```

- **Returns:** `Any`
- **Description:** Attach ``tokenpak_stats`` to the response object if telemetry is on.

#### `wrap_kwargs`

```python
def wrap_kwargs(self, **kwargs: Any) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Pre-process kwargs for ``litellm.completion(**wrapped)``.

### `tokenpak.sdk.integrations.litellm.proxy.ProxyHandler`

**Bases:** object

ASGI-compatible handler for the ``/tokenpak`` proxy endpoint.

Args:
    default_model: Fallback model if request doesn't specify one.
    budget: Default token budget.
    compaction: Default compaction strategy.
    litellm_kwargs: Extra kwargs forwarded to every ``litellm.completion`` call.

#### `__init__`

```python
def __init__(self, default_model: str = 'gpt-4', budget: int = 8000, compaction: str = 'balanced', **litellm_kwargs: Any) -> None
```

- **Returns:** `None`

#### `handle`

```python
async def handle(self, request: Any) -> Any
```

- **Returns:** `Any`
- **Description:** Starlette-compatible request handler.

### `tokenpak.sdk.langchain.adapter.LangChainAdapter`

**Bases:** TokenPakAdapter

TokenPak adapter for LangChain-style requests.

#### `__init__`

```python
def __init__(self, base_url: str, api_key: str, timeout_s: float | None = None) -> None
```

- **Returns:** `None`

#### `prepare_request`

```python
def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `send`

```python
def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `parse_response`

```python
def parse_response(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `extract_tokens`

```python
def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.sdk.langchain.context.TokenPakContextManager`

**Bases:** object

#### `__init__`

```python
def __init__(self, total_budget: int = 8000, avg_tokens_per_char: float = 0.25) -> None
```

- **Returns:** `None`

#### `allocate`

```python
def allocate(self, source: str, tokens: int) -> int
```

- **Returns:** `int`

#### `allocated`

```python
def allocated(self) -> int
```

- **Returns:** `int`

#### `remaining`

```python
def remaining(self) -> int
```

- **Returns:** `int`

#### `estimate_tokens`

```python
def estimate_tokens(self, text: str) -> int
```

- **Returns:** `int`

#### `fits`

```python
def fits(self, source: str, text: str) -> bool
```

- **Returns:** `bool`

#### `trim_to_budget`

```python
def trim_to_budget(self, source: str, text: str) -> str
```

- **Returns:** `str`

#### `status`

```python
def status(self) -> dict[str, int | dict[str, int]]
```

- **Returns:** `dict[str, int | dict[str, int]]`

### `tokenpak.sdk.langchain.converters.Block`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.sdk.langchain.langgraph.state.TokenPakState`

**Bases:** object

#### `__init__`

```python
def __init__(self, budget = 8000) -> Any
```

- **Returns:** `Any`

#### `add_context`

```python
def add_context(self, key, content) -> Any
```

- **Returns:** `Any`

#### `add_message`

```python
def add_message(self, role, content) -> Any
```

- **Returns:** `Any`

#### `used_tokens`

```python
def used_tokens(self) -> Any
```

- **Returns:** `Any`

#### `remaining_tokens`

```python
def remaining_tokens(self) -> Any
```

- **Returns:** `Any`

#### `get_context`

```python
def get_context(self, key) -> Any
```

- **Returns:** `Any`

#### `to_dict`

```python
def to_dict(self) -> Any
```

- **Returns:** `Any`

#### `from_dict`

```python
def from_dict(cls, data) -> Any
```

- **Returns:** `Any`

### `tokenpak.sdk.langchain.memory.TokenPakMemory`

**Bases:** object

#### `__init__`

```python
def __init__(self, budget: int = 2000, compression_ratio: float = 0.5, avg_tokens_per_char: float = 0.25) -> None
```

- **Returns:** `None`

#### `add_message`

```python
def add_message(self, role: str, content: str) -> None
```

- **Returns:** `None`

#### `get_history`

```python
def get_history(self) -> list[dict[str, str]]
```

- **Returns:** `list[dict[str, str]]`

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`

#### `token_usage`

```python
def token_usage(self) -> dict[str, int]
```

- **Returns:** `dict[str, int]`

### `tokenpak.sdk.langchain.retrievers.TokenPakRetriever`

**Bases:** object

#### `__init__`

```python
def __init__(self, retriever: _Retriever, budget: int = 4000, avg_tokens_per_char: float = 0.25) -> None
```

- **Returns:** `None`

#### `get_relevant_documents`

```python
def get_relevant_documents(self, query: str) -> list[object]
```

- **Returns:** `list[object]`

#### `aget_relevant_documents`

```python
async def aget_relevant_documents(self, query: str) -> list[object]
```

- **Returns:** `list[object]`

### `tokenpak.sdk.langchain_adapter.LangChainAdapter`

**Bases:** TokenPakAdapter

TokenPak adapter for LangChain-style requests.

Routes to the underlying ``AnthropicAdapter`` or ``OpenAIAdapter``
based on the ``provider`` field in the request (default: ``"openai"``).

Usage
-----
>>> adapter = LangChainAdapter(
...     base_url="http://127.0.0.1:8767",
...     api_key="sk-...",
... )
>>> response = adapter.call({
...     "model": "gpt-4o",
...     "provider": "openai",
...     "messages": [
...         {"role": "system", "content": "You are helpful."},
...         {"role": "human", "content": "Hello"},
...     ],
... })

#### `__init__`

```python
def __init__(self, base_url: str, api_key: str, timeout_s: float | None = None) -> None
```

- **Returns:** `None`

#### `prepare_request`

```python
def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Normalise LangChain roles and delegate to provider adapter.

#### `send`

```python
def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Delegate send to the matching provider adapter.

#### `parse_response`

```python
def parse_response(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Delegate response parsing to the matching provider adapter.

#### `extract_tokens`

```python
def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Delegate token extraction to the matching provider adapter.

### `tokenpak.sdk.litellm.LiteLLMAdapter`

**Bases:** TokenPakAdapter

TokenPak adapter for LiteLLM-style requests.

Automatically resolves the provider from the model string and
delegates to the appropriate underlying adapter.

Usage
-----
>>> adapter = LiteLLMAdapter(
...     base_url="http://127.0.0.1:8767",
...     api_key="sk-...",
... )
>>> # OpenAI via LiteLLM prefix
>>> response = adapter.call({
...     "model": "openai/gpt-4o",
...     "messages": [{"role": "user", "content": "Hi"}],
... })
>>> # Anthropic via LiteLLM prefix
>>> response = adapter.call({
...     "model": "anthropic/claude-3-5-sonnet-20241022",
...     "messages": [{"role": "user", "content": "Hi"}],
...     "max_tokens": 512,
... })
>>> tokens = adapter.extract_tokens(response)

#### `__init__`

```python
def __init__(self, base_url: str, api_key: str, timeout_s: float | None = None) -> None
```

- **Returns:** `None`

#### `prepare_request`

```python
def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Parse LiteLLM model string, strip provider prefix, delegate.

#### `send`

```python
def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Delegate send based on the resolved provider.

#### `parse_response`

```python
def parse_response(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Delegate response parsing based on response shape.

#### `extract_tokens`

```python
def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Delegate token extraction based on response shape.

### `tokenpak.sdk.llamaindex.converters.LlamaBlock`

**Bases:** object

Portable Block representation compatible with TIP-1.0.

Maps LlamaIndex node data to Pak block semantics:
  - id          → node id
  - content     → node text
  - quality     → retrieval score (0-1)
  - tokens      → estimated token count
  - metadata    → node metadata
  - provenance  → source info (document, page, etc.)

#### `to_llamaindex_node`

```python
def to_llamaindex_node(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Export as LlamaIndex-compatible dict.

#### `to_tokenpak_dict`

```python
def to_tokenpak_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Export in the Pak wire format.

### `tokenpak.sdk.llamaindex.fusion.MultiIndexFusion`

**Bases:** object

Query multiple LlamaIndex indexes and fuse results with compression.

Fusion strategies:
  - "rank": Re-rank by score across all indexes (default)
  - "round_robin": Alternate results from each index
  - "weighted": Weight results by per-index weight

Usage:
    fusion = MultiIndexFusion(
        indexes={
            "docs": docs_query_engine,
            "code": code_query_engine,
            "wiki": wiki_query_engine,
        },
        budget=6000,
        weights={"docs": 0.5, "code": 0.3, "wiki": 0.2},
    )

    # Standard query
    result = fusion.query("How does context compression work?")
    print(result["context"])   # compressed, fused context

    # As a Pak
    pack = fusion.query_as_tokenpak("How does context compression work?")
    print(pack["blocks"])      # all compressed evidence blocks
    print(pack["sources"])     # which index each block came from

#### `__init__`

```python
def __init__(self, indexes: Dict[str, Any], budget: int = 6000, strategy: str = 'rank', weights: Optional[Dict[str, float]] = None, top_k_per_index: int = 5, keep_headers: bool = True, keep_code: bool = True) -> Any
```

- **Returns:** `Any`
- **Description:** Args:

#### `query`

```python
def query(self, query_str: str, **kwargs) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Query all indexes and return fused, compressed context.

#### `aquery`

```python
async def aquery(self, query_str: str, **kwargs) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Async version of query (queries all indexes in parallel).

#### `query_as_tokenpak`

```python
def query_as_tokenpak(self, query_str: str, **kwargs) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Query all indexes and return a complete Pak payload.

#### `aquery_as_tokenpak`

```python
async def aquery_as_tokenpak(self, query_str: str, **kwargs) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Async version of query_as_tokenpak.

### `tokenpak.sdk.llamaindex.index.TokenPakIndex`

**Bases:** object

LlamaIndex Index wrapper with TokenPak compression.

Compresses retrieved nodes during query execution to reduce
token costs while preserving answer quality.

Usage:
    # Wrap an existing index
    tp_index = TokenPakIndex(existing_index, budget=4000)
    engine = tp_index.as_query_engine()
    response = engine.query("What is TokenPak?")

    # Or build from documents (requires llama-index-core)
    tp_index = TokenPakIndex.from_documents(docs, budget=2000)

#### `__init__`

```python
def __init__(self, index: Any, budget: int = 2000, keep_headers: bool = True, keep_code: bool = True) -> Any
```

- **Returns:** `Any`
- **Description:** Args:

#### `from_documents`

```python
def from_documents(cls, documents: List[Any], budget: int = 2000, index_class: Optional[Any] = None, **index_kwargs) -> 'TokenPakIndex'
```

- **Returns:** `'TokenPakIndex'`
- **Description:** Create a TokenPakIndex from documents.

#### `as_query_engine`

```python
def as_query_engine(self, **kwargs) -> 'TokenPakQueryEngine'
```

- **Returns:** `'TokenPakQueryEngine'`
- **Description:** Get a TokenPakQueryEngine backed by this index.

#### `as_retriever`

```python
def as_retriever(self, **kwargs) -> Any
```

- **Returns:** `Any`
- **Description:** Get underlying retriever (no compression — use as_query_engine for that).

#### `compress_nodes`

```python
def compress_nodes(self, nodes: List[Any]) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Compress a list of nodes to fit within budget.

### `tokenpak.sdk.llamaindex.query_engine.TokenPakQueryEngine`

**Bases:** object

LlamaIndex QueryEngine wrapper with TokenPak compression.

Automatically compresses retrieved nodes before synthesis.

Usage:
    base_engine = index.as_query_engine()
    tp_engine = TokenPakQueryEngine(
        query_engine=base_engine,
        budget=4000,
    )

    # Standard query (compressed)
    response = tp_engine.query("What is context compression?")

    # Structured pack export
    pack = tp_engine.query_as_tokenpak("What is context compression?")
    print(pack["blocks"])   # compressed evidence blocks
    print(pack["context"])  # formatted context string
    print(pack["tokens"])   # token counts

#### `__init__`

```python
def __init__(self, query_engine: Any, budget: int = 4000, llm: Optional[Any] = None, keep_headers: bool = True, keep_code: bool = True) -> Any
```

- **Returns:** `Any`
- **Description:** Args:

#### `query`

```python
def query(self, query_str: str, **kwargs) -> Any
```

- **Returns:** `Any`
- **Description:** Execute query with compression.

#### `aquery`

```python
async def aquery(self, query_str: str, **kwargs) -> Any
```

- **Returns:** `Any`
- **Description:** Async query execution with compression.

#### `query_as_tokenpak`

```python
def query_as_tokenpak(self, query_str: str, extra_nodes: Optional[List[Any]] = None, **kwargs) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Execute query and return a structured Pak.

#### `aquery_as_tokenpak`

```python
async def aquery_as_tokenpak(self, query_str: str, extra_nodes: Optional[List[Any]] = None, **kwargs) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Async version of query_as_tokenpak.

#### `compression_stats`

```python
def compression_stats(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Stats from last synthesizer run.

### `tokenpak.sdk.llamaindex.synthesizer.TokenPakSynthesizer`

**Bases:** object

LlamaIndex-compatible synthesizer with TokenPak compression.

Automatically compresses retrieved nodes within a token budget
before calling the underlying LLM for synthesis.

Usage:
    synthesizer = TokenPakSynthesizer(budget=4000, llm=your_llm)

    # With LlamaIndex query engine:
    engine = index.as_query_engine(synthesizer=synthesizer)
    response = engine.query("What is context compression?")

    # Direct use:
    response = synthesizer.synthesize("question", nodes=nodes)

Compression strategy:
    1. Convert nodes to LlamaBlocks (score-aware)
    2. Sort by quality (highest-scored first)
    3. Trim content proportionally to fit budget
    4. Preserve structure (headers, code blocks) when possible

#### `__init__`

```python
def __init__(self, budget: int = 4000, llm: Optional[Any] = None, keep_headers: bool = True, keep_code: bool = True, system_prompt_reserve: int = 500) -> Any
```

- **Returns:** `Any`
- **Description:** Args:

#### `synthesize`

```python
def synthesize(self, query: str, nodes: List[Any], additional_source_nodes: Optional[List[Any]] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Synthesize a response from compressed nodes.

#### `asynthesize`

```python
async def asynthesize(self, query: str, nodes: List[Any], additional_source_nodes: Optional[List[Any]] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Async version of synthesize.

#### `last_stats`

```python
def last_stats(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Compression stats from the last synthesize() call.

### `tokenpak.sdk.local.lmstudio.TokenPakLMStudio`

**Bases:** TokenPakOpenAICompat

TokenPak integration for LM Studio.

LM Studio runs a local OpenAI-compatible server; this class sets the
correct defaults and provides LM Studio-specific helpers.

Usage:
    from tokenpak_local import TokenPakLMStudio, TokenPak, Block

    client = TokenPakLMStudio()  # auto-connects to localhost:1234

    pack = TokenPak()
    pack.instructions = "Answer concisely based on the context."
    pack.add(Block(type="evidence", content="The Eiffel Tower is in Paris."))

    response = client.complete(
        model="lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF",
        tokenpak=pack,
        user_message="Where is the Eiffel Tower?",
    )
    print(response.choices[0].message.content)

Context window detection:
    LM Studio loads models with specific context lengths visible in the
    app. Pass `context_length` to override:

    client = TokenPakLMStudio(context_length=8192)

Streaming:
    for chunk in client.complete(model=..., tokenpak=pack, stream=True, user_message="..."):
        delta = chunk.choices[0].delta.content
        if delta:
            print(delta, end="", flush=True)

#### `__init__`

```python
def __init__(self, host: str = 'localhost', port: int = 1234, output_fraction: float = 0.25, context_length: Optional[int] = None, **client_kwargs: Any) -> Any
```

- **Returns:** `Any`
- **Description:** Args:

#### `server_url`

```python
def server_url(self) -> str
```

- **Returns:** `str`

#### `list_models`

```python
def list_models(self) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** List models currently loaded in LM Studio.

### `tokenpak.sdk.local.ollama.TokenPakOllama`

**Bases:** object

Ollama client wrapper with automatic TokenPak context compression.

Supports both chat (chat completions) and generate (raw text generation)
endpoints. When a Pak is provided, the budget is automatically
set based on the model's known context window before compilation.

Usage:
    from tokenpak_local import TokenPakOllama, TokenPak, Block

    client = TokenPakOllama()  # connects to http://localhost:11434

    pack = TokenPak()
    pack.instructions = "Answer based on the evidence below."
    pack.add(Block(type="evidence", content="..."))

    response = client.chat(model="llama3", tokenpak=pack)
    print(response["message"]["content"])

Streaming:
    for chunk in client.chat(model="llama3", tokenpak=pack, stream=True):
        print(chunk["message"]["content"], end="", flush=True)

With extra messages (appended after TokenPak system message):
    response = client.chat(
        model="llama3",
        tokenpak=pack,
        messages=[{"role": "user", "content": "What is context compression?"}]
    )

#### `__init__`

```python
def __init__(self, host: str = 'http://localhost:11434', output_fraction: float = 0.25, auto_detect_context: bool = True, **client_kwargs: Any) -> Any
```

- **Returns:** `Any`
- **Description:** Args:

#### `chat`

```python
def chat(self, model: str, tokenpak: Optional[TokenPak] = None, messages: Optional[List[Dict[str, Any]]] = None, stream: bool = False, **kwargs: Any) -> Union[Dict[str, Any], Iterator[Dict[str, Any]]]
```

- **Returns:** `Union[Dict[str, Any], Iterator[Dict[str, Any]]]`
- **Description:** Send a chat request to Ollama, optionally with a Pak.

#### `generate`

```python
def generate(self, model: str, tokenpak: Optional[TokenPak] = None, prompt: str = '', stream: bool = False, **kwargs: Any) -> Union[Dict[str, Any], Iterator[Dict[str, Any]]]
```

- **Returns:** `Union[Dict[str, Any], Iterator[Dict[str, Any]]]`
- **Description:** Send a generate request to Ollama, optionally prepending TokenPak context.

#### `budget_for`

```python
def budget_for(self, model: str) -> int
```

- **Returns:** `int`
- **Description:** Return the computed input budget for a model.

#### `budget_info`

```python
def budget_info(self, model: str) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return full budget breakdown for a model (useful for debugging).

### `tokenpak.sdk.local.openai_compat.TokenPakOpenAICompat`

**Bases:** object

TokenPak wrapper for any OpenAI-compatible local LLM server.

Automatically compiles a Pak and sets an appropriate token
budget before sending to the local endpoint.

Usage (LM Studio):
    from tokenpak_local import TokenPakOpenAICompat, TokenPak, Block

    client = TokenPakOpenAICompat(base_url="http://localhost:1234/v1")

    pack = TokenPak()
    pack.instructions = "You are a helpful assistant."
    pack.add(Block(type="evidence", content="Earth orbits the Sun."))

    response = client.complete(
        model="lmstudio-community/Meta-Llama-3-8B",
        tokenpak=pack,
        user_message="What does Earth orbit?",
    )
    print(response.choices[0].message.content)

Usage (Ollama OpenAI mode):
    client = TokenPakOpenAICompat(base_url="http://localhost:11434/v1", api_key="ollama")
    response = client.complete(model="llama3", tokenpak=pack, user_message="...")

Streaming:
    for chunk in client.complete(..., stream=True):
        print(chunk.choices[0].delta.content or "", end="", flush=True)

#### `__init__`

```python
def __init__(self, base_url: str = 'http://localhost:1234/v1', api_key: str = 'lm-studio', output_fraction: float = 0.25, context_length: Optional[int] = None, **client_kwargs: Any) -> Any
```

- **Returns:** `Any`
- **Description:** Args:

#### `complete`

```python
def complete(self, model: str, tokenpak: Optional[TokenPak] = None, messages: Optional[List[Dict[str, Any]]] = None, user_message: Optional[str] = None, stream: bool = False, **kwargs: Any) -> Any
```

- **Returns:** `Any`
- **Description:** Send a chat completion request with an optional Pak.

#### `budget_for`

```python
def budget_for(self, model: str) -> int
```

- **Returns:** `int`
- **Description:** Return the computed input budget for a model.

### `tokenpak.sdk.local.utils.Block`

**Bases:** object

Minimal Pak block shim.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.sdk.local.utils.TokenPak`

**Bases:** object

Minimal TokenPak shim that compiles to OpenAI-style messages.

#### `__init__`

```python
def __init__(self, budget: Optional[int] = None, instructions: str = '') -> Any
```

- **Returns:** `Any`

#### `add`

```python
def add(self, block: Block) -> 'TokenPak'
```

- **Returns:** `'TokenPak'`

#### `to_messages`

```python
def to_messages(self) -> List[Dict[str, str]]
```

- **Returns:** `List[Dict[str, str]]`
- **Description:** Compile blocks to OpenAI chat messages list.

#### `compile`

```python
def compile(self) -> 'TokenPak'
```

- **Returns:** `'TokenPak'`

#### `total_tokens`

```python
def total_tokens(self) -> int
```

- **Returns:** `int`

### `tokenpak.sdk.openai.OpenAIAdapter`

**Bases:** TokenPakAdapter

TokenPak adapter for the OpenAI Chat Completions API.

Usage
-----
>>> adapter = OpenAIAdapter(
...     base_url="http://127.0.0.1:8767",
...     api_key="sk-...",
... )
>>> response = adapter.call({
...     "model": "gpt-4o",
...     "messages": [{"role": "user", "content": "Hello"}],
... })
>>> tokens = adapter.extract_tokens(response)

#### `prepare_request`

```python
def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Validate and normalise an OpenAI request.

#### `send`

```python
def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** POST to ``{base_url}/v1/chat/completions`` through the proxy.

#### `parse_response`

```python
def parse_response(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Validate proxy response and surface provider errors.

#### `extract_tokens`

```python
def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Extract OpenAI Chat Completions usage block.

### `tokenpak.security.auth_alert.NullNotificationHook`

**Bases:** object

A no-op hook — swallows all events. Useful for testing.

#### `__call__`

```python
def __call__(self, provider: str, event: str, details: AuthFailureDetails) -> None
```

- **Returns:** `None`

### `tokenpak.security.auth_alert.WebhookNotificationHook`

**Bases:** object

Send auth-failure alerts as JSON POST to any HTTP endpoint.

Args:
    url: The webhook URL to POST to.
    headers: Optional extra HTTP headers (e.g. Authorization).
    timeout: Request timeout in seconds (default: 15).

Example::

    hook = WebhookNotificationHook(
        url="https://hooks.slack.com/services/...",
        headers={"Content-Type": "application/json"},
    )
    register_auth_alert_hook(hook)

#### `__init__`

```python
def __init__(self, url: str, headers: Optional[dict[str, str]] = None, timeout: int = 15) -> None
```

- **Returns:** `None`

#### `__call__`

```python
def __call__(self, provider: str, event: str, details: AuthFailureDetails) -> None
```

- **Returns:** `None`

### `tokenpak.security.auth_guard.AuthGuard`

**Bases:** object

Thread-safe tracker for consecutive auth failures per provider.

- Records HTTP 401/403 responses from upstream providers.
- After AUTH_FAILURE_THRESHOLD consecutive failures, emits an event.
- Emits at most once per AUTH_ALERT_COOLDOWN_SEC seconds (spam protection).
- Resets counter on any successful (non-401/403) response.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `on_auth_failure`

```python
def on_auth_failure(self, handler: AuthFailureHandler) -> None
```

- **Returns:** `None`
- **Description:** Register a callback for auth-failure-detected events.

#### `record_response`

```python
def record_response(self, provider: str, status_code: int) -> None
```

- **Returns:** `None`
- **Description:** Call this for every upstream response.

#### `get_counters`

```python
def get_counters(self) -> dict[str, int]
```

- **Returns:** `dict[str, int]`

#### `get_last_alert_times`

```python
def get_last_alert_times(self) -> dict[str, float]
```

- **Returns:** `dict[str, float]`

### `tokenpak.security.dlp.DLPBlockError`

**Bases:** Exception

Raised in block mode when secrets are detected in outbound text.

#### `__init__`

```python
def __init__(self, findings: List[DLPMatch]) -> None
```

- **Returns:** `None`

### `tokenpak.security.dlp.DLPScanner`

**Bases:** object

Secret and PII scanner using gitleaks-derived regex patterns.

Parameters
----------
mode : str, optional
    ``warn`` (default), ``redact``, or ``block``.
    Falls back to ``TOKENPAK_DLP_MODE`` env var, then ``warn``.

#### `__init__`

```python
def __init__(self, mode: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `scan`

```python
def scan(self, text: str) -> List[DLPMatch]
```

- **Returns:** `List[DLPMatch]`
- **Description:** Scan *text* for secrets and PII.

#### `redact`

```python
def redact(self, text: str) -> str
```

- **Returns:** `str`
- **Description:** Return a copy of *text* with all secrets replaced by

#### `block_check`

```python
def block_check(self, text: str) -> bool
```

- **Returns:** `bool`
- **Description:** Return ``True`` if *text* is clean (no secrets), ``False`` otherwise.

### `tokenpak.services.memory_optimization.CgroupLimit`

**Bases:** object

One finite cgroup memory limit and its on-disk provenance.

#### `as_dict`

```python
def as_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.services.memory_optimization.HostFacts`

**Bases:** object

Normalized facts that fully explain a generated plan.

#### `as_dict`

```python
def as_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.services.memory_optimization.ManagedPaths`

**Bases:** object

Enumerable TokenPak-owned optimizer artifacts.

#### `as_dict`

```python
def as_dict(self) -> dict[str, str]
```

- **Returns:** `dict[str, str]`

### `tokenpak.services.memory_optimization.OptimizationPlan`

**Bases:** object

Immutable deterministic MemoryGuard plan.

#### `as_dict`

```python
def as_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

### `tokenpak.services.optimization.attribution_stage.AttributionStage`

**Bases:** object

Post-response stage that parses usage and emits SavingsAttribution records.

Usage
-----
Unlike most stages, AttributionStage works in two phases:

Phase A — eligibility check (pre-upstream, via eligible()):
    stage.eligible(ctx)

Phase B — attribution parsing (post-upstream, via parse_response()):
    stage.parse_response(ctx, response_body_bytes)

The standard pipeline calls only ``eligible()``. Callers that want
actual attribution data must call ``parse_response()`` after receiving
the upstream response.

#### `__init__`

```python
def __init__(self, env: Optional[Dict[str, str]] = None) -> None
```

- **Returns:** `None`

#### `eligible`

```python
def eligible(self, ctx: OptimizationContext) -> EligibilityResult
```

- **Returns:** `EligibilityResult`

#### `apply`

```python
def apply(self, ctx: OptimizationContext) -> OptimizationContext
```

- **Returns:** `OptimizationContext`

#### `parse_response`

```python
def parse_response(self, ctx: OptimizationContext, response_body: bytes, *, platform: Optional[str] = None, model: str = '') -> List[Any]
```

- **Returns:** `List[Any]`
- **Description:** Parse response body and return SavingsAttribution records.

### `tokenpak.services.optimization.cache_stage.SemanticCacheStage`

**Bases:** object

Wraps ``SemanticCache`` as a generic optimization stage.

Uses ``tokenpak.cache.semantic_cache.SemanticCache`` (Level 0) directly to
stay within the services/ → cache/ dependency tier. One instance is typically
created at process startup (or per test) and reused across requests so the
in-process cache persists.

#### `__init__`

```python
def __init__(self, env: Optional[Dict[str, str]] = None) -> None
```

- **Returns:** `None`

#### `eligible`

```python
def eligible(self, ctx: OptimizationContext) -> EligibilityResult
```

- **Returns:** `EligibilityResult`
- **Description:** Return whether this request should attempt a semantic cache lookup.

#### `apply`

```python
def apply(self, ctx: OptimizationContext) -> OptimizationContext
```

- **Returns:** `OptimizationContext`
- **Description:** Perform the semantic cache lookup and annotate *ctx*.

#### `record`

```python
def record(self, ctx: OptimizationContext, response: dict[str, object]) -> None
```

- **Returns:** `None`
- **Description:** Store *response* in the cache for *ctx*'s query (call after upstream).

### `tokenpak.services.optimization.cache_trace.CacheStageTrace`

**Bases:** object

Outcome of one semantic cache lookup/record cycle.

Attached to ``OptimizationContext.cache_result`` by ``SemanticCacheStage``.
Never stores raw prompt text — only hashed/normalized values.

#### `to_detail_str`

```python
def to_detail_str(self) -> str
```

- **Returns:** `str`
- **Description:** Serialize to a compact JSON string for ``StageTrace.detail``.

### `tokenpak.services.optimization.compression_stage.RouteClassCompressionStage`

**Bases:** object

OptimizationStage that maps route class to safe compression recipes.

name:                 stable identifier used in trace
required_capabilities: stage requests adapters declare TIP_COMPRESSION_V1
env:                  optional env dict (test injection); defaults to
                      ``os.environ`` at call time

Eligibility rules (in order, first match wins):

1. flag-off:               TOKENPAK_ROUTE_COMPRESSION_STAGE not truthy
2. route-unknown:          ctx.route is None / "" / "unknown"
3. capability-missing:     contract declares capabilities but not
                           ``tip.compression.v1`` (graceful unknown:
                           an *empty* capability set is allowed,
                           matching the proposal's "graceful unknowns")
4. fidelity-no-optimize:   policy.fidelity == "no_optimize"
5. no-recipes-for-route:   policy.recipe_names is empty
6. eligible=True:          would-apply detail records the recipe names

#### `eligible`

```python
def eligible(self, ctx: OptimizationContext) -> EligibilityResult
```

- **Returns:** `EligibilityResult`

#### `apply`

```python
def apply(self, ctx: OptimizationContext) -> OptimizationContext
```

- **Returns:** `OptimizationContext`
- **Description:** Compress ``ctx.raw_body`` in place per the route policy.

### `tokenpak.services.optimization.context.OptimizationContext`

**Bases:** object

All state for one request flowing through the pipeline.

request_id:      unique id (proxy-side request id)
raw_body:        original POST body bytes — IMMUTABLE in observe-only
canonical:       optional CanonicalRequest from the format adapter
                 (may be None when adapter normalization isn't safe)
adapter:         the FormatAdapter instance, when known
platform:        platform string (from sdk.registry.detect_platform())
route:           route-class string from _classify_route()
policy:          route policy dict from get_policy(route)
contract:        OptimizationContract — opaque to the pipeline
headers:         outbound headers dict (case as received)
target_url:      upstream URL the proxy will eventually call
trace:           pipeline trace, written to as stages run

The dataclass holds Optional types because not every call site has all
of them; the pipeline is defensive about None.

#### `body_size`

```python
def body_size(self) -> int
```

- **Returns:** `int`

### `tokenpak.services.optimization.pipeline.OptimizationPipeline`

**Bases:** object

Runs registered stages in observe-only mode.

Stateless; create once per process or once per request, both work.
Stage registration order determines run order.

#### `__init__`

```python
def __init__(self, registry: Optional[StageRegistry] = None) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, stage: OptimizationStage) -> None
```

- **Returns:** `None`

#### `run_observe_only`

```python
def run_observe_only(self, ctx: OptimizationContext) -> OptimizationTrace
```

- **Returns:** `OptimizationTrace`
- **Description:** Iterate every registered stage and record an eligibility trace.

### `tokenpak.services.optimization.protected_spans.ProtectedSpan`

**Bases:** object

A half-open range ``[start, end)`` of text marked protected.

#### `overlaps`

```python
def overlaps(self, other: 'ProtectedSpan') -> bool
```

- **Returns:** `bool`

### `tokenpak.services.optimization.registry.StageRegistry`

**Bases:** object

Ordered registry of optimization stages.

Insertion order is preserved (Python dict semantics). The pipeline runs
stages in the order they were registered. Stage names must be unique;
re-registering a name replaces the previous entry.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, stage: OptimizationStage) -> None
```

- **Returns:** `None`

#### `unregister`

```python
def unregister(self, name: str) -> None
```

- **Returns:** `None`

#### `get`

```python
def get(self, name: str) -> OptimizationStage
```

- **Returns:** `OptimizationStage`

#### `names`

```python
def names(self) -> List[str]
```

- **Returns:** `List[str]`

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`

### `tokenpak.services.optimization.route_recipe_policy.CompressionResult`

**Bases:** object

Result of applying a route policy to a piece of text.

#### `applied`

```python
def applied(self) -> bool
```

- **Returns:** `bool`

#### `ratio`

```python
def ratio(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.services.optimization.stage.NoOpStage`

**Bases:** object

Reference stage that is always eligible-but-skips with ``no-op-default``.

Used as a sentinel in the registry tests and as a safe placeholder for
stages whose mutating implementation lives in a later milestone.

#### `eligible`

```python
def eligible(self, ctx: 'OptimizationContext') -> EligibilityResult
```

- **Returns:** `EligibilityResult`

#### `apply`

```python
def apply(self, ctx: 'OptimizationContext') -> 'OptimizationContext'
```

- **Returns:** `'OptimizationContext'`

### `tokenpak.services.optimization.stage.OptimizationStage`

**Bases:** Protocol

Protocol every optimization stage must satisfy.

Subclasses set ``name`` (machine-readable identifier emitted in traces)
and ``required_capabilities`` (set of TIP capability label strings the
contract must report present for this stage to be eligible).

#### `eligible`

```python
def eligible(self, ctx: 'OptimizationContext') -> EligibilityResult
```

- **Returns:** `EligibilityResult`

#### `apply`

```python
def apply(self, ctx: 'OptimizationContext') -> 'OptimizationContext'
```

- **Returns:** `'OptimizationContext'`

### `tokenpak.services.optimization.telemetry_sink.TelemetrySink`

**Bases:** object

Persists optimization pipeline telemetry after a request completes.

Instantiate once at process startup; call ``persist()`` at the end of
every request that passed through the optimization pipeline.

Parameters
----------
db_path:
    Path to the TelemetryDB SQLite file.  Defaults to the location
    resolved by ``tokenpak.core.paths.get_db_path("telemetry.db")``.
env:
    Optional env dict override for feature flag checks (used in tests).

#### `__init__`

```python
def __init__(self, db_path: str | Path | None = None, env: Mapping[str, str] | None = None) -> None
```

- **Returns:** `None`

#### `persist`

```python
def persist(self, ctx: OptimizationContext, response_body: Optional[bytes] = None, *, platform: Optional[str] = None, model: str = '') -> None
```

- **Returns:** `None`
- **Description:** Persist savings attributions and cache miss reasons for *ctx*.

### `tokenpak.services.optimization.trace.OptimizationTrace`

**Bases:** object

Top-level trace for one request through the pipeline.

Service-local observe-only DTO. It shares its historical name with the
canonical ``tokenpak.core.contracts.trace.OptimizationTrace`` but is a
distinct, narrower shape; see ``to_tip_dict`` for the envelope it emits.

#### `add_stage`

```python
def add_stage(self, st: StageTrace) -> None
```

- **Returns:** `None`

#### `mark_bypass`

```python
def mark_bypass(self, reason: str) -> None
```

- **Returns:** `None`

#### `body_unchanged`

```python
def body_unchanged(self) -> bool
```

- **Returns:** `bool`
- **Description:** True when no stage was applied AND byte counts match.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `to_tip_dict`

```python
def to_tip_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return the legacy observe-only trace envelope.

### `tokenpak.services.optimization.trace.StageTrace`

**Bases:** object

Per-stage trace entry (service-local observe-only DTO).

Distinct from the canonical ``tokenpak.core.contracts.trace.StageTrace``
despite the shared historical name.

name:        stage's machine identifier
eligible:    eligibility verdict (True/False)
skip_reason: empty when eligible=True; otherwise a short token
applied:     True only if the stage actually mutated ctx. Observe-only
             pipelines must always emit applied=False for every stage.
duration_ms: monotonic-clock duration of the eligibility check (the
             only thing that ran in observe-only mode)
detail:      free-form note for debugging

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.services.preview.PreviewBlock`

**Bases:** object

A real segment identified by the compression pipeline.

``block_id`` and ``segment_type`` come from the pipeline's own
:class:`~tokenpak.compression.segmentizer.Segment` objects. They are never
synthesized: if the pipeline did not identify a segment, no block is
reported for it.

#### `to_json`

```python
def to_json(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.services.preview.PreviewProvenance`

**Bases:** object

What was measured, how, and with what version.

#### `to_json`

```python
def to_json(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.services.preview.PreviewResult`

**Bases:** object

Strict result contract for ``tokenpak preview``.

Invariants (enforced by :meth:`validate`, called from ``__post_init__``):

1. Token counts are non-negative.
2. ``saved_tokens == input_tokens - output_tokens`` exactly.
3. ``compression_ratio`` lies in ``[0.0, 1.0]``.
4. ``duration_ms`` is a real measurement (``> 0``) whenever state is
   ``MEASURED``.
5. Block identities come from the pipeline; no synthesized names.
6. Savings are never negative. If compression would *expand* the input,
   the original is retained and ``applied`` is ``False`` — the honest
   report of "we looked and chose not to change anything".

In any non-``MEASURED`` state every numeric field is ``None``, never ``0``.

#### `validate`

```python
def validate(self) -> None
```

- **Returns:** `None`
- **Description:** Raise :class:`PreviewInvariantError` if the contract is violated.

#### `to_json`

```python
def to_json(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.sources.base.Connector`

**Bases:** ABC

Base class for data source connectors.

Connectors handle:
- Authentication/authorization
- File listing and delta detection
- Content retrieval
- Sync state management

#### `__init__`

```python
def __init__(self, config: ConnectorConfig) -> None
```

- **Returns:** `None`

#### `connect`

```python
def connect(self) -> bool
```

- **Returns:** `bool`
- **Description:** Establish connection to the data source.

#### `list_files`

```python
def list_files(self, since: str | None = None) -> Iterator[RemoteFile]
```

- **Returns:** `Iterator[RemoteFile]`
- **Description:** List files from the source.

#### `get_content`

```python
def get_content(self, file: RemoteFile) -> bytes
```

- **Returns:** `bytes`
- **Description:** Retrieve file content.

#### `disconnect`

```python
def disconnect(self) -> None
```

- **Returns:** `None`
- **Description:** Close connection to the data source.

#### `get_sync_state`

```python
def get_sync_state(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Get current sync state for resumable syncs.

#### `set_sync_state`

```python
def set_sync_state(self, state: dict[str, object]) -> None
```

- **Returns:** `None`
- **Description:** Restore sync state.

### `tokenpak.sources.base_source.SourceAdapter`

**Bases:** ABC

Abstract base for on-demand source adapters.

Each subclass fetches content from one source_type and returns a
(content, Provenance) pair. The caller is responsible for wrapping
into a Block and persisting to the registry.

#### `ingest`

```python
def ingest(self, source_id: str, **kwargs: object) -> tuple[str, Provenance]
```

- **Returns:** `tuple[str, Provenance]`
- **Raises:** `SourceFetchError on non-recoverable failures.`
- **Description:** Fetch content from the source.

#### `has_changed`

```python
def has_changed(self, source_id: str, cached_version: str, **kwargs: object) -> bool
```

- **Returns:** `bool`
- **Description:** Check whether the source has changed since cached_version.

### `tokenpak.sources.git_adapter.GitAdapter`

**Bases:** SourceAdapter

Read file content from a local git repository at a given commit.

#### `ingest`

```python
def ingest(self, source_id: str, **kwargs) -> Tuple[str, Provenance]
```

- **Returns:** `Tuple[str, Provenance]`
- **Description:** Fetch a file from a local git repo.

#### `has_changed`

```python
def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool
```

- **Returns:** `bool`
- **Description:** Compare current HEAD SHA against cached_version (a full commit SHA).

### `tokenpak.sources.github.GitHubConnector`

**Bases:** Connector

Connector for GitHub repositories.

Requires:
- Personal access token (PAT) or GitHub App
- Repository access permissions

Features:
- Repository file sync
- Issue/PR content extraction
- Code file processing with language detection
- Incremental sync using commit SHAs

#### `__init__`

```python
def __init__(self, config: ConnectorConfig) -> Any
```

- **Returns:** `Any`

#### `connect`

```python
def connect(self) -> bool
```

- **Returns:** `bool`
- **Description:** Fail closed because the GitHub connector is available in Pro.

#### `list_files`

```python
def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]
```

- **Returns:** `Iterator[RemoteFile]`
- **Description:** List repository files using Git tree API.

#### `get_content`

```python
def get_content(self, file: RemoteFile) -> bytes
```

- **Returns:** `bytes`
- **Description:** Download file content from GitHub.

#### `list_issues`

```python
def list_issues(self, state: str = 'all') -> Iterator[RemoteFile]
```

- **Returns:** `Iterator[RemoteFile]`
- **Description:** List issues as virtual files.

### `tokenpak.sources.google_drive.GoogleDriveConnector`

**Bases:** Connector

Connector for Google Drive.

Requires:
- OAuth2 credentials (client_id, client_secret)
- User authorization flow

Features:
- Full Drive or specific folder sync
- Google Docs/Sheets/Slides export to text
- Shared drive support
- Incremental sync using Drive API changes

#### `__init__`

```python
def __init__(self, config: ConnectorConfig) -> Any
```

- **Returns:** `Any`

#### `connect`

```python
def connect(self) -> bool
```

- **Returns:** `bool`
- **Description:** Fail closed because the Google Drive connector is available in Pro.

#### `list_files`

```python
def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]
```

- **Returns:** `Iterator[RemoteFile]`
- **Description:** List files using Drive API.

#### `get_content`

```python
def get_content(self, file: RemoteFile) -> bytes
```

- **Returns:** `bytes`
- **Description:** Download file content.

### `tokenpak.sources.local.LocalConnector`

**Bases:** Connector

Connector for local directories.

Free tier — no authentication required.

#### `connect`

```python
def connect(self) -> bool
```

- **Returns:** `bool`
- **Description:** Verify source path exists.

#### `list_files`

```python
def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]
```

- **Returns:** `Iterator[RemoteFile]`
- **Description:** List files in the local directory.

#### `get_content`

```python
def get_content(self, file: RemoteFile) -> bytes
```

- **Returns:** `bytes`
- **Description:** Read file content.

### `tokenpak.sources.notion.NotionConnector`

**Bases:** Connector

Connector for Notion workspaces.

Requires:
- Notion integration token
- Workspace access permissions

Features:
- Page and database sync
- Block-level content extraction
- Property/metadata extraction
- Incremental sync using last_edited_time

#### `__init__`

```python
def __init__(self, config: ConnectorConfig) -> Any
```

- **Returns:** `Any`

#### `connect`

```python
def connect(self) -> bool
```

- **Returns:** `bool`
- **Description:** Fail closed because the Notion connector is available in Pro.

#### `list_files`

```python
def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]
```

- **Returns:** `Iterator[RemoteFile]`
- **Description:** Search all pages and databases in the workspace.

#### `get_content`

```python
def get_content(self, file: RemoteFile) -> bytes
```

- **Returns:** `bytes`
- **Description:** Retrieve page content by fetching all blocks.

### `tokenpak.sources.notion_adapter.NotionAdapter`

**Bases:** SourceAdapter

Fetch a single Notion page by page_id.

#### `__init__`

```python
def __init__(self, api_token: Optional[str] = None) -> Any
```

- **Returns:** `Any`

#### `ingest`

```python
def ingest(self, source_id: str, **kwargs) -> Tuple[str, Provenance]
```

- **Returns:** `Tuple[str, Provenance]`
- **Description:** Fetch a Notion page by page_id.

#### `has_changed`

```python
def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool
```

- **Returns:** `bool`
- **Description:** Compare last_edited_time from Notion API against cached version.

### `tokenpak.sources.obsidian.ObsidianConnector`

**Bases:** LocalConnector

Connector for Obsidian vaults.

Free tier — extends local connector with:
- Wiki-link parsing and resolution
- Frontmatter extraction
- Attachment detection
- Daily notes structure awareness

#### `__init__`

```python
def __init__(self, config: ConnectorConfig) -> Any
```

- **Returns:** `Any`

#### `list_files`

```python
def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]
```

- **Returns:** `Iterator[RemoteFile]`
- **Description:** List files, enriching with Obsidian metadata.

#### `extract_links`

```python
def extract_links(self, content: str) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Extract wiki-links from content.

#### `extract_frontmatter`

```python
def extract_frontmatter(self, content: str, strict: bool = False) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Extract YAML frontmatter from content.

### `tokenpak.sources.url_adapter.URLAdapter`

**Bases:** SourceAdapter

Fetch and index web pages by URL.

#### `ingest`

```python
def ingest(self, source_id: str, **kwargs: object) -> tuple[str, Provenance]
```

- **Returns:** `tuple[str, Provenance]`
- **Description:** Fetch a URL and return clean text + provenance.

#### `has_changed`

```python
def has_changed(self, source_id: str, cached_version: str, **kwargs: object) -> bool
```

- **Returns:** `bool`
- **Description:** Check whether the page has changed via a HEAD request + ETag comparison.

### `tokenpak.standards_oss.tools.standards.Report`

**Bases:** object

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `error`

```python
def error(self, msg: str) -> None
```

- **Returns:** `None`

#### `warn`

```python
def warn(self, msg: str) -> None
```

- **Returns:** `None`

#### `note`

```python
def note(self, msg: str) -> None
```

- **Returns:** `None`

#### `decision`

```python
def decision(self, missing: str, why: str, who: str) -> None
```

- **Returns:** `None`
- **Description:** An absent answer, surfaced for the operator.

#### `emit`

```python
def emit(self) -> int
```

- **Returns:** `int`

### `tokenpak.telemetry.__init__.CostTracker`

**Bases:** CompletionTracker

Deprecated alias for CompletionTracker. Will be removed in v2.0.

#### `__init__`

```python
def __init__(self, *args: Any, **kwargs: Any) -> None
```

- **Returns:** `None`

### `tokenpak.telemetry.adapters.anthropic.AnthropicAdapter`

**Bases:** BaseAdapter

Adapter for the Anthropic Messages API.

Supports:
- Text and tool-use content blocks in requests and responses.
- Prompt-caching usage fields (``cache_creation_input_tokens``,
  ``cache_read_input_tokens``).
- All ``stop_reason`` variants (end_turn, max_tokens, stop_sequence,
  tool_use).

#### `detect`

```python
def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]
```

- **Returns:** `tuple[str, float]`
- **Description:** Return high confidence for Anthropic payloads.

#### `to_canonical_request`

```python
def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`
- **Description:** Normalise an Anthropic request payload.

#### `to_canonical_response`

```python
def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse
```

- **Returns:** `CanonicalResponse`
- **Description:** Normalise an Anthropic response payload.

#### `extract_usage`

```python
def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage
```

- **Returns:** `CanonicalUsage`
- **Description:** Extract token-usage from an Anthropic response.

### `tokenpak.telemetry.adapters.base.BaseAdapter`

**Bases:** ABC

Protocol adapter that translates provider-specific payloads into
canonical TokenPak telemetry types.

Sub-classes
-----------
Each adapter is responsible for a single provider (e.g. Anthropic,
OpenAI, Gemini).  Adapters are stateless; every method is a pure
transformation from raw ``dict`` → canonical object.

Detection contract
------------------
``detect`` returns ``(provider_name, confidence)`` where *confidence* is
in the range ``[0.0, 1.0]``.  The registry picks the adapter with the
highest confidence score.  Return ``0.0`` if the payload definitively
does *not* match.

#### `detect`

```python
def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]
```

- **Returns:** `tuple[str, float]`
- **Description:** Determine whether *raw_payload* came from this adapter's provider.

#### `to_canonical_request`

```python
def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`
- **Description:** Normalise a raw request payload into a ``CanonicalRequest``.

#### `to_canonical_response`

```python
def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse
```

- **Returns:** `CanonicalResponse`
- **Description:** Normalise a raw response payload into a ``CanonicalResponse``.

#### `extract_usage`

```python
def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage
```

- **Returns:** `CanonicalUsage`
- **Description:** Extract token-usage information from a raw payload.

### `tokenpak.telemetry.adapters.gemini.GeminiAdapter`

**Bases:** BaseAdapter

Adapter for the Google Gemini GenerateContent API.

Handles:
- ``candidates[].content.parts[]`` for multi-part responses.
- ``usageMetadata`` extraction including cached content tokens.
- Graceful degradation when ``usageMetadata`` is absent.

#### `detect`

```python
def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]
```

- **Returns:** `tuple[str, float]`
- **Description:** Return confidence score for Gemini payloads.

#### `to_canonical_request`

```python
def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`
- **Description:** Normalise a Gemini request payload.

#### `to_canonical_response`

```python
def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse
```

- **Returns:** `CanonicalResponse`
- **Description:** Normalise a Gemini response payload.

#### `extract_usage`

```python
def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage
```

- **Returns:** `CanonicalUsage`
- **Description:** Extract token-usage from a Gemini response.

### `tokenpak.telemetry.adapters.openai.OpenAIAdapter`

**Bases:** BaseAdapter

Adapter for the OpenAI Chat Completions and Responses APIs.

Automatically distinguishes between:
- Chat Completions (``choices[].message``)
- Responses API (``output[]`` list)
- Codex / reasoning-enabled variants

#### `detect`

```python
def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]
```

- **Returns:** `tuple[str, float]`
- **Description:** Return confidence score for OpenAI payloads.

#### `to_canonical_request`

```python
def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`
- **Description:** Normalise an OpenAI request payload.

#### `to_canonical_response`

```python
def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse
```

- **Returns:** `CanonicalResponse`
- **Description:** Normalise an OpenAI response payload.

#### `extract_usage`

```python
def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage
```

- **Returns:** `CanonicalUsage`
- **Description:** Extract token-usage from an OpenAI response.

### `tokenpak.telemetry.adapters.registry.AdapterRegistry`

**Bases:** object

Registry that maps raw LLM payloads to their provider adapter.

Usage
-----
>>> registry = AdapterRegistry.build_default()
>>> adapter = registry.detect(raw_response)
>>> usage = adapter.extract_usage(raw_response)

You can also build a custom registry:

>>> registry = AdapterRegistry()
>>> registry.register(MyCustomAdapter())
>>> adapter = registry.detect(raw_payload)

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `register`

```python
def register(self, adapter: BaseAdapter) -> None
```

- **Returns:** `None`
- **Description:** Add *adapter* to the registry.

#### `adapters`

```python
def adapters(self) -> list[BaseAdapter]
```

- **Returns:** `list[BaseAdapter]`
- **Description:** Read-only view of registered adapters.

#### `detect`

```python
def detect(self, raw: dict[str, Any]) -> BaseAdapter
```

- **Returns:** `BaseAdapter`
- **Description:** Return the best-matching adapter for *raw*.

#### `build_default`

```python
def build_default(cls) -> 'AdapterRegistry'
```

- **Returns:** `'AdapterRegistry'`
- **Description:** Return a registry pre-populated with all built-in adapters.

### `tokenpak.telemetry.adapters.registry.UnknownAdapter`

**Bases:** BaseAdapter

Fallback adapter used when the provider cannot be determined.

All extraction methods return empty / zero-valued objects.
``extract_usage`` marks results with ``usage_source="proxy_estimate"``
and ``confidence="low"`` to signal unreliable data.

#### `detect`

```python
def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]
```

- **Returns:** `tuple[str, float]`
- **Description:** Always returns 0 confidence — used only as a fallback.

#### `to_canonical_request`

```python
def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest
```

- **Returns:** `CanonicalRequest`
- **Description:** Return a minimal canonical request preserving the raw payload.

#### `to_canonical_response`

```python
def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse
```

- **Returns:** `CanonicalResponse`
- **Description:** Return a minimal canonical response preserving the raw payload.

#### `extract_usage`

```python
def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage
```

- **Returns:** `CanonicalUsage`
- **Description:** Return a zero-usage record marked as proxy estimate.

### `tokenpak.telemetry.anon_metrics.MetricsRecord`

**Bases:** object

One anonymised request record. No content fields allowed.

#### `to_upload_dict`

```python
def to_upload_dict(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return a dict safe to send to the ingest endpoint (no local_id).

#### `from_row`

```python
def from_row(cls, row: sqlite3.Row) -> 'MetricsRecord'
```

- **Returns:** `'MetricsRecord'`

### `tokenpak.telemetry.anon_metrics.MetricsStore`

**Bases:** object

SQLite-backed local metrics store.

#### `__init__`

```python
def __init__(self, db_path: Path = METRICS_DB) -> Any
```

- **Returns:** `Any`

#### `record`

```python
def record(self, rec: MetricsRecord) -> None
```

- **Returns:** `None`
- **Description:** Insert a new metrics record.

#### `get_pending`

```python
def get_pending(self, limit: int = 500) -> List[MetricsRecord]
```

- **Returns:** `List[MetricsRecord]`
- **Description:** Return unsynced records.

#### `mark_synced`

```python
def mark_synced(self, local_ids: List[str]) -> None
```

- **Returns:** `None`
- **Description:** Mark records as successfully uploaded.

#### `history`

```python
def history(self, days: int = 30, limit: int = 500) -> List[MetricsRecord]
```

- **Returns:** `List[MetricsRecord]`
- **Description:** Return all records (synced + pending) for the last N days.

#### `daily_summary`

```python
def daily_summary(self, days: int = 30) -> List[dict]
```

- **Returns:** `List[dict]`
- **Description:** Aggregate stats per day for CLI display.

### `tokenpak.telemetry.artifact_store.ArtifactStore`

**Bases:** object

Store and retrieve artifacts for dynamic context.

#### `__init__`

```python
def __init__(self, db_path: Optional[str] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize artifact store.

#### `store_artifact`

```python
def store_artifact(self, artifact: ArtifactSchema) -> str
```

- **Returns:** `str`
- **Description:** Store artifact in database. Returns artifact ID.

#### `retrieve_artifact`

```python
def retrieve_artifact(self, artifact_id: str) -> Optional[ArtifactSchema]
```

- **Returns:** `Optional[ArtifactSchema]`
- **Description:** Retrieve artifact by ID.

#### `store_chunk`

```python
def store_chunk(self, chunk: ChunkSchema) -> str
```

- **Returns:** `str`
- **Description:** Store chunk in database. Returns chunk ID.

#### `retrieve_chunk`

```python
def retrieve_chunk(self, chunk_id: str) -> Optional[ChunkSchema]
```

- **Returns:** `Optional[ChunkSchema]`
- **Description:** Retrieve chunk by ID.

#### `get_chunk_neighbors`

```python
def get_chunk_neighbors(self, chunk_id: str) -> List[ChunkSchema]
```

- **Returns:** `List[ChunkSchema]`
- **Description:** Get neighboring chunks.

#### `cache_retrieval_results`

```python
def cache_retrieval_results(self, cache_entry: RetrievalCacheSchema) -> None
```

- **Returns:** `None`
- **Description:** Store retrieval cache entry.

#### `get_cached_results`

```python
def get_cached_results(self, query_fingerprint: str) -> Optional[RetrievalCacheSchema]
```

- **Returns:** `Optional[RetrievalCacheSchema]`
- **Description:** Get retrieval cache entry. Returns None if expired.

#### `invalidate_cache_entry`

```python
def invalidate_cache_entry(self, query_fingerprint: str) -> None
```

- **Returns:** `None`
- **Description:** Remove cache entry.

#### `invalidate_cache_by_repo`

```python
def invalidate_cache_by_repo(self, repo_id: str) -> None
```

- **Returns:** `None`
- **Description:** Invalidate all cache entries for a repo (on repo changes).

#### `store_source_map`

```python
def store_source_map(self, source_map: SourceMapSchema) -> None
```

- **Returns:** `None`
- **Description:** Store source map.

#### `get_source_map`

```python
def get_source_map(self, repo_id: str, session_id: str) -> Optional[SourceMapSchema]
```

- **Returns:** `Optional[SourceMapSchema]`
- **Description:** Get source map.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`
- **Description:** Close database connection.

### `tokenpak.telemetry.attribution.AttributionRecord`

**Bases:** object

Single request attribution.

#### `to_dict`

```python
def to_dict(self) -> AttributionRecordDict
```

- **Returns:** `AttributionRecordDict`
- **Description:** Convert attribution record to dictionary for JSON serialization.

### `tokenpak.telemetry.attribution.AttributionTracker`

**Bases:** object

Track and aggregate attribution data.

#### `__init__`

```python
def __init__(self, max_records: int = 5000) -> Any
```

- **Returns:** `Any`

#### `record`

```python
def record(self, rec: AttributionRecord) -> None
```

- **Returns:** `None`
- **Description:** Add an attribution record to the tracker.

#### `records`

```python
def records(self) -> List[AttributionRecord]
```

- **Returns:** `List[AttributionRecord]`
- **Description:** Get all tracked attribution records.

#### `rollup_by_source`

```python
def rollup_by_source(self, since: Optional[float] = None) -> Dict[str, SourceRollup]
```

- **Returns:** `Dict[str, SourceRollup]`
- **Description:** Aggregate stats by source.

#### `rollup_by_model`

```python
def rollup_by_model(self, since: Optional[float] = None) -> Dict[str, ModelRollup]
```

- **Returns:** `Dict[str, ModelRollup]`
- **Description:** Aggregate stats by model.

#### `leakage_pct`

```python
def leakage_pct(self, since: Optional[float] = None) -> float
```

- **Returns:** `float`
- **Description:** Percentage of requests with unknown source.

#### `save`

```python
def save(self, path: Optional[Path] = None) -> None
```

- **Returns:** `None`
- **Description:** Persist attribution data.

#### `load`

```python
def load(self, path: Optional[Path] = None) -> None
```

- **Returns:** `None`
- **Description:** Load attribution data from file.

### `tokenpak.telemetry.budget.BudgetConfig`

**Bases:** object

User-configured budget limits.

#### `to_dict`

```python
def to_dict(self) -> dict[str, float | bool | None]
```

- **Returns:** `dict[str, float | bool | None]`

#### `from_dict`

```python
def from_dict(cls, d: dict[str, Any]) -> 'BudgetConfig'
```

- **Returns:** `'BudgetConfig'`

### `tokenpak.telemetry.budget.BudgetStatus`

**Bases:** object

Current budget consumption snapshot.

#### `to_dict`

```python
def to_dict(self) -> dict[str, str | float | bool]
```

- **Returns:** `dict[str, str | float | bool]`

### `tokenpak.telemetry.budget.BudgetTracker`

**Bases:** object

Track actual API spend against configured budget limits.

Usage::

    tracker = BudgetTracker(db_path="~/.tokenpak/budget.db")
    tracker.record_spend(0.012, request_id="req-001", model="claude-sonnet")
    status = tracker.get_status("daily")
    print(status.to_dict())

#### `__init__`

```python
def __init__(self, config: Optional[BudgetConfig] = None, db_path: str = ':memory:') -> Any
```

- **Returns:** `Any`

#### `record_spend`

```python
def record_spend(self, cost_usd: float, *, request_id: str = '', model: str = '', tokens_input: int = 0, tokens_output: int = 0, agent: str = '', timestamp: Optional[datetime] = None) -> SpendRecord
```

- **Returns:** `SpendRecord`
- **Description:** Record spend for a completed request.

#### `total_spent`

```python
def total_spent(self, period: str = 'daily') -> float
```

- **Returns:** `float`
- **Description:** Return total spend for the given period ('daily' or 'monthly').

#### `get_status`

```python
def get_status(self, period: str = 'daily') -> Optional[BudgetStatus]
```

- **Returns:** `Optional[BudgetStatus]`
- **Description:** Return BudgetStatus for the period, or None if no limit is configured.

#### `is_budget_exceeded`

```python
def is_budget_exceeded(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if any configured limit is exceeded.

#### `list_spend`

```python
def list_spend(self, limit: int = 50, period: Optional[str] = None, model: Optional[str] = None, agent: Optional[str] = None) -> list[SpendRow]
```

- **Returns:** `list[SpendRow]`
- **Description:** List spend records with optional filters.

#### `by_model_summary`

```python
def by_model_summary(self, period: Optional[str] = None) -> list[ModelSpendSummary]
```

- **Returns:** `list[ModelSpendSummary]`
- **Description:** Return spend grouped by model.

#### `export_csv`

```python
def export_csv(self, period: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Return CSV string of spend records.

#### `prune`

```python
def prune(self, days: int = 90) -> int
```

- **Returns:** `int`
- **Description:** Delete spend records older than N days.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`

### `tokenpak.telemetry.budget_allocator.BudgetBlock`

**Bases:** object

Block metadata for budget allocation.

#### `importance`

```python
def importance(self) -> float
```

- **Returns:** `float`
- **Description:** Composite importance score (0-10), modulated by utility weight.

### `tokenpak.telemetry.budget_controller.BudgetController`

**Bases:** object

Choose budget tier and escalation policy for a single turn.

#### `__init__`

```python
def __init__(self, *, tier_map: Mapping[IntentClass, str] | None = None, tier_tokens: Mapping[str, int] | None = None, tier_order: list[str] | None = None, coverage_threshold: float = 0.55, max_auto_tier: str = 'T3_64K', t4_intents: tuple[IntentClass, ...] = (IntentClass.CODE_EDIT, IntentClass.REVIEW)) -> None
```

- **Returns:** `None`

#### `decide`

```python
def decide(self, classification: ClassificationResult) -> BudgetDecision
```

- **Returns:** `BudgetDecision`

#### `check_spending_threshold`

```python
def check_spending_threshold(self, spent_usd: float, budget_usd: float) -> list[BudgetThresholdAlert]
```

- **Returns:** `list[BudgetThresholdAlert]`
- **Description:** Check if spending has reached alert thresholds.

#### `maybe_escalate`

```python
def maybe_escalate(self, decision: BudgetDecision, *, coverage_score: float, intent: IntentClass, multi_module_edit: bool = False) -> BudgetDecision
```

- **Returns:** `BudgetDecision`

### `tokenpak.telemetry.budgeter.Budgeter`

**Bases:** object

Token budget allocator and trim controller.

Hard limit: components will be trimmed until total_tokens is met.
Never trims: STATE_JSON, output contract, CANON refs, current turn.

#### `__init__`

```python
def __init__(self, config_path: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `allocate`

```python
def allocate(self, components: Dict[str, Any]) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Allocate token budget across components, trimming as needed.

#### `budget_report`

```python
def budget_report(self, components: Dict[str, Any]) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return token usage per bucket.

### `tokenpak.telemetry.cache.CacheStore`

**Bases:** object

Thread-safe in-memory cache with per-entry TTL.

Parameters
----------
default_ttl:
    Default time-to-live in seconds (300 = 5 min).
max_size:
    Maximum number of entries before eviction (LRU-like — clears expired).

#### `__init__`

```python
def __init__(self, default_ttl: float = 300, max_size: int = 1000) -> None
```

- **Returns:** `None`

#### `get`

```python
def get(self, key: str) -> tuple[bool, Any]
```

- **Returns:** `tuple[bool, Any]`
- **Description:** Return (hit, value). hit=False means cache miss or expired.

#### `set`

```python
def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None
```

- **Returns:** `None`
- **Description:** Store value under key with given TTL (default: self.default_ttl).

#### `delete`

```python
def delete(self, key: str) -> bool
```

- **Returns:** `bool`
- **Description:** Delete a specific key. Returns True if key existed.

#### `invalidate_prefix`

```python
def invalidate_prefix(self, prefix: str) -> int
```

- **Returns:** `int`
- **Description:** Delete all keys starting with prefix. Returns count deleted.

#### `clear`

```python
def clear(self) -> int
```

- **Returns:** `int`
- **Description:** Clear all cache entries. Returns count deleted.

#### `evict_expired`

```python
def evict_expired(self) -> int
```

- **Returns:** `int`
- **Description:** Public: remove expired entries. Returns count evicted.

#### `stats`

```python
def stats(self) -> dict[str, int | float]
```

- **Returns:** `dict[str, int | float]`

### `tokenpak.telemetry.cache_miss.CacheMissRecord`

**Bases:** object

Single cache miss event.

Fields map to ``tp_cache_miss_reasons`` columns.

#### `to_row`

```python
def to_row(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.telemetry.cache_miss.MissReasonSummary`

**Bases:** object

Aggregated miss metrics for one reason.

#### `record`

```python
def record(self, *, route: str = '', model: str = '') -> None
```

- **Returns:** `None`

### `tokenpak.telemetry.canonical.Confidence`

**Bases:** object

Controlled vocabulary for ``CanonicalUsage.confidence``.

#### `validate`

```python
def validate(cls, value: str) -> str
```

- **Returns:** `str`
- **Description:** Return *value* if valid, else raise ``ValueError``.

### `tokenpak.telemetry.canonical.UsageSource`

**Bases:** object

Controlled vocabulary for ``CanonicalUsage.usage_source``.

#### `validate`

```python
def validate(cls, value: str) -> str
```

- **Returns:** `str`
- **Description:** Return *value* if valid, else raise ``ValueError``.

### `tokenpak.telemetry.collector.TelemetryCollector`

**Bases:** object

Watches the tokenpak telemetry DB and emits events to subscribers.

#### `__init__`

```python
def __init__(self, config: CollectorConfig) -> Any
```

- **Returns:** `Any`

#### `start`

```python
def start(self, blocking: bool = True) -> None
```

- **Returns:** `None`
- **Description:** Start the file watcher background thread.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Stop the file watcher and clean up resources.

#### `backfill`

```python
def backfill(self, paths: Optional[list[Path]] = None) -> None
```

- **Returns:** `None`
- **Description:** Emit stored events from before the watcher was started.

### `tokenpak.telemetry.config.CaptureConfig`

**Bases:** BaseModel

Capture settings.

#### `validate_sampling_rate`

```python
def validate_sampling_rate(cls, v) -> Any
```

- **Returns:** `Any`

### `tokenpak.telemetry.config.TelemetryConfig`

**Bases:** BaseModel

Top-level configuration.

#### `validate_version`

```python
def validate_version(cls, v) -> Any
```

- **Returns:** `Any`

### `tokenpak.telemetry.cost.CostEngine`

**Bases:** object

Cost calculation service with DB-backed versioned pricing.

Args:
    db_path: Path to telemetry SQLite database.

#### `__init__`

```python
def __init__(self, db_path: str = '') -> Any
```

- **Returns:** `Any`

#### `get_pricing`

```python
def get_pricing(self, model: str, event_ts: Optional[str] = None) -> Pricing
```

- **Returns:** `Pricing`
- **Description:** Resolve pricing for a model at a given event timestamp.

#### `calculate`

```python
def calculate(self, model: str, raw_input_tokens: int, final_input_tokens: int, output_tokens: int, event_ts: Optional[str] = None, cache_read_tokens: int = 0) -> CostResult
```

- **Returns:** `CostResult`
- **Description:** Calculate baseline, actual, and savings for a single event.

#### `list_pricing`

```python
def list_pricing(self, version: Optional[str] = None) -> List[dict[str, object]]
```

- **Returns:** `List[dict[str, object]]`
- **Description:** List all pricing entries, optionally filtered by version.

#### `add_pricing`

```python
def add_pricing(self, provider: str, model: str, input_rate: float, output_rate: float, version: Optional[str] = None, effective_date: Optional[str] = None, source: str = 'official') -> int
```

- **Returns:** `int`
- **Description:** Insert a new pricing record. Returns the new row id.

#### `reprocess_costs`

```python
def reprocess_costs(self, from_date: str, to_date: str, pricing_version: Optional[str] = None) -> dict[str, str | int]
```

- **Returns:** `dict[str, str | int]`
- **Description:** Recalculate costs for events in a date range.

### `tokenpak.telemetry.cost.CostResult`

**Bases:** object

Result of a cost calculation for a single event.

#### `to_dict`

```python
def to_dict(self) -> dict[str, PricingRowValue]
```

- **Returns:** `dict[str, PricingRowValue]`

### `tokenpak.telemetry.cost.Pricing`

**Bases:** object

A single model pricing record.

#### `input_per_token`

```python
def input_per_token(self) -> float
```

- **Returns:** `float`

#### `output_per_token`

```python
def output_per_token(self) -> float
```

- **Returns:** `float`

### `tokenpak.telemetry.cost_tracker.CostTracker`

**Bases:** object

Track per-request LLM cost with SQLite persistence.

Usage::

    tracker = CostTracker("~/.tokenpak/cost.db")
    cost = tracker.record_request("claude-sonnet-4-5", 1000, 250)
    summary = tracker.get_summary("day")

#### `__init__`

```python
def __init__(self, db_path: str | Path | None = ':memory:') -> Any
```

- **Returns:** `Any`

#### `record_request`

```python
def record_request(self, model: str, prompt_tokens: int, completion_tokens: int, *, http_status: int = 200, session_id: str = '', timestamp: Optional[str] = None) -> float
```

- **Returns:** `float`
- **Description:** Record a completed request and return the estimated cost_usd.

#### `get_summary`

```python
def get_summary(self, period: str = 'day') -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return summary dict for the given period.

#### `get_by_model`

```python
def get_by_model(self, period: str = 'day') -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return per-model breakdown for the given period.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`

### `tokenpak.telemetry.costs.budget_tracker.BudgetTracker`

**Bases:** object

Track spending against configurable budget thresholds

#### `__init__`

```python
def __init__(self, config: Optional[BudgetConfigInput] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize budget tracker.

#### `load_budget_config`

```python
def load_budget_config(self, config_dict: BudgetConfigInput) -> None
```

- **Returns:** `None`
- **Description:** Load budget configuration from dict

#### `check_spending_vs_limit`

```python
def check_spending_vs_limit(self, current_spend: float, limit_type: str = 'daily') -> tuple[bool, Optional[float]]
```

- **Returns:** `tuple[bool, Optional[float]]`
- **Description:** Check if spending exceeds limit.

#### `should_alert`

```python
def should_alert(self, current_spend: float, limit: float, limit_type: str = 'daily') -> Optional[BudgetAlert]
```

- **Returns:** `Optional[BudgetAlert]`
- **Description:** Check if alert should fire based on spending level.

#### `get_budget_summary`

```python
def get_budget_summary(self) -> BudgetSummary
```

- **Returns:** `BudgetSummary`
- **Description:** Get human-readable budget summary

#### `format_budget_display`

```python
def format_budget_display(self, current_spend: float, limit: float, limit_type: str = 'daily') -> str
```

- **Returns:** `str`
- **Description:** Format budget progress bar for display.

#### `reset_alert_history`

```python
def reset_alert_history(self) -> None
```

- **Returns:** `None`
- **Description:** Clear alert history (useful for testing)

### `tokenpak.telemetry.dashboard.pagination.CursorPaginationBuilder`

**Bases:** object

Builder for cursor-based pagination queries.

Replaces LIMIT OFFSET with WHERE clause to avoid O(n) scans.

#### `__init__`

```python
def __init__(self, table: str, cursor_fields: List[str], order_by: str = 'timestamp', order: str = 'desc') -> Any
```

- **Returns:** `Any`

#### `build_query`

```python
def build_query(self, cursor: Optional[str] = None, limit: int = 50, filters: Optional[Dict[str, Any]] = None) -> tuple
```

- **Returns:** `tuple`
- **Description:** Build pagination query. Returns (sql, params).

#### `extract_cursor_from_row`

```python
def extract_cursor_from_row(self, row: Dict[str, Any]) -> str
```

- **Returns:** `str`
- **Description:** Extract cursor from a row.

### `tokenpak.telemetry.dashboard.pagination.PaginatedResponse`

**Bases:** object

Standard paginated response envelope.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert to dict for JSON serialization.

### `tokenpak.telemetry.dashboard.query_builder.FilterSpec`

**Bases:** object

Single filter condition.

#### `is_valid`

```python
def is_valid(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.telemetry.dashboard.query_builder.QueryBuilder`

**Bases:** object

Safe server-side query builder with validation and indexing awareness.

- Prevents SQL injection via column whitelist + parameterized queries
- Validates sorts against indexed columns
- Applies guardrails (max rows, export limits, rate limits)

#### `__init__`

```python
def __init__(self, table: str, export_mode: bool = False) -> Any
```

- **Returns:** `Any`

#### `add_filter`

```python
def add_filter(self, spec: FilterSpec) -> 'QueryBuilder'
```

- **Returns:** `'QueryBuilder'`
- **Description:** Add a WHERE condition.

#### `add_sort`

```python
def add_sort(self, spec: SortSpec) -> 'QueryBuilder'
```

- **Returns:** `'QueryBuilder'`
- **Description:** Add ORDER BY (builds at end).

#### `build`

```python
def build(self, limit: int = 50) -> tuple
```

- **Returns:** `tuple`
- **Description:** Build final query with guardrails.

### `tokenpak.telemetry.dashboard.query_builder.SortSpec`

**Bases:** object

Sort specification.

#### `is_valid`

```python
def is_valid(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.telemetry.elo.EloRatings`

**Bases:** object

Persistent Elo rating store.
Ratings are keyed by (model, task_type) → float.

#### `__init__`

```python
def __init__(self, ratings_path: str = DEFAULT_ELO_PATH) -> Any
```

- **Returns:** `Any`

#### `get_elo`

```python
def get_elo(self, model: str, task_type: str) -> float
```

- **Returns:** `float`
- **Description:** Return current Elo rating for (model, task_type).

#### `update_elo`

```python
def update_elo(self, model: str, task_type: str, accepted: bool) -> float
```

- **Returns:** `float`
- **Description:** Update Elo rating for a model after a transaction outcome.

#### `get_all`

```python
def get_all(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return a copy of all ratings.

#### `get_rankings`

```python
def get_rankings(self, task_type: Optional[str] = None) -> list
```

- **Returns:** `list`
- **Description:** Return sorted list of (model, task_type, rating) tuples.

#### `reset`

```python
def reset(self, model: Optional[str] = None, task_type: Optional[str] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Reset ratings. Pass model and/or task_type to reset selectively.

### `tokenpak.telemetry.error_logger.ErrorLogger`

**Bases:** object

Thread-safe error telemetry logger for TokenPak proxy

#### `__init__`

```python
def __init__(self, log_dir: Optional[str] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize error logger.

#### `log_error`

```python
def log_error(self, request_id: str, error: Exception, context: Optional[Dict[str, Any]] = None) -> None
```

- **Returns:** `None`
- **Description:** Log an exception with context.

#### `get_error_summary`

```python
def get_error_summary(self, days: int = 1, error_type: Optional[str] = None) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Get error summary for reporting.

#### `get_metrics`

```python
def get_metrics(self) -> Dict[str, int]
```

- **Returns:** `Dict[str, int]`
- **Description:** Get current Prometheus-style metrics

### `tokenpak.telemetry.event_schema.ValidationResult`

**Bases:** object

#### `has_warnings`

```python
def has_warnings(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.telemetry.insights.Insight`

**Bases:** object

A single insight with optional action suggestion.

#### `to_dict`

```python
def to_dict(self) -> dict[str, str | float | None]
```

- **Returns:** `dict[str, str | float | None]`

#### `severity_rank`

```python
def severity_rank(self) -> int
```

- **Returns:** `int`

#### `delta_magnitude`

```python
def delta_magnitude(self) -> float
```

- **Returns:** `float`

### `tokenpak.telemetry.insights.InsightEngine`

**Bases:** object

Reads from telemetry rollup tables and generates actionable insights.

Args:
    db_path: Path to telemetry SQLite database.
    thresholds: Override default thresholds dict.

#### `__init__`

```python
def __init__(self, db_path: str = '', thresholds: Optional[dict[str, float]] = None) -> Any
```

- **Returns:** `Any`

#### `generate_insights`

```python
def generate_insights(self, days: int = 7) -> List[Insight]
```

- **Returns:** `List[Insight]`
- **Description:** Generate insights from the last `days` of data.

#### `invalidate_cache`

```python
def invalidate_cache(self) -> None
```

- **Returns:** `None`
- **Description:** Force next call to regenerate insights.

### `tokenpak.telemetry.integrity.validation.EventValidator`

**Bases:** object

Validates telemetry events on ingestion.

#### `__init__`

```python
def __init__(self) -> Any
```

- **Returns:** `Any`

#### `validate_token_counts`

```python
def validate_token_counts(self, event: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Reject if any token count < 0.

#### `validate_stage_progression`

```python
def validate_stage_progression(self, event: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Validate: raw ≥ qmd ≥ tokenpak ≥ final.

#### `validate_provider_model`

```python
def validate_provider_model(self, event: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Validate provider and model exist.

#### `validate_timestamp`

```python
def validate_timestamp(self, event: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Validate timestamp is reasonable.

#### `validate_required_fields`

```python
def validate_required_fields(self, event: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Validate required fields present.

#### `validate`

```python
def validate(self, event: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Run full validation suite.

#### `get_error_response`

```python
def get_error_response(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Format validation errors for API response.

### `tokenpak.telemetry.metering.UsageMeter`

**Bases:** object

Per-license usage collector.

Tracks:
- Tokens processed (input, output, saved)
- Requests made
- Models used

Reports daily to license server.

#### `__init__`

```python
def __init__(self, key_id: str, db_path: Optional[Path] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize meter for a license key.

#### `record`

```python
def record(self, model: str, input_tokens: int, output_tokens: int, saved_tokens: int, request_type: str) -> None
```

- **Returns:** `None`
- **Description:** Record a single request's usage.

#### `flush`

```python
def flush(self, timeout: float = 5.0) -> None
```

- **Returns:** `None`
- **Description:** Wait for all pending background write threads to complete.

#### `get_daily_summary`

```python
def get_daily_summary(self, date: str) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Aggregate usage for a given date (YYYY-MM-DD).

#### `report_to_server`

```python
def report_to_server(self, server_url: str, timeout: int = 10) -> bool
```

- **Returns:** `bool`
- **Description:** Upload unreported usage to license server.

#### `cleanup_old_data`

```python
def cleanup_old_data(self, days: int = 90) -> int
```

- **Returns:** `int`
- **Description:** Delete usage data older than N days (default 90).

### `tokenpak.telemetry.metering.UsageMeterManager`

**Bases:** object

Manages multiple UsageMeter instances (one per license key).

Thread-safe singleton for use in proxy.

#### `get_meter`

```python
def get_meter(self, key_id: str) -> UsageMeter
```

- **Returns:** `UsageMeter`
- **Description:** Get or create meter for key_id.

#### `record_usage`

```python
def record_usage(self, key_id: str, model: str, input_tokens: int, output_tokens: int, saved_tokens: int, request_type: str = 'chat') -> None
```

- **Returns:** `None`
- **Description:** Record usage for a license key.

#### `get_daily_summary`

```python
def get_daily_summary(self, key_id: str, date: str) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Get daily summary for a license key.

#### `report_all`

```python
def report_all(self, server_url: str) -> Dict[str, bool]
```

- **Returns:** `Dict[str, bool]`
- **Description:** Report all pending usage for all meters. Returns {key_id: success}.

### `tokenpak.telemetry.metrics.prometheus.PrometheusRegistry`

**Bases:** object

Collects metrics from SessionDict + Monitor and renders Prometheus text.

#### `__init__`

```python
def __init__(self, session: dict[str, Any], monitor: Any = None) -> None
```

- **Returns:** `None`
- **Description:** Args:

#### `render`

```python
def render(self) -> str
```

- **Returns:** `str`
- **Description:** Build and return full Prometheus text format output.

### `tokenpak.telemetry.model_analytics.ModelAnalyzer`

**Bases:** object

Aggregate compression events by model.

#### `__init__`

```python
def __init__(self, log_path: Optional[str] = None) -> Any
```

- **Returns:** `Any`

#### `load_from_file`

```python
def load_from_file(self, limit: int = 1000) -> Dict[str, ModelStats]
```

- **Returns:** `Dict[str, ModelStats]`
- **Description:** Load and aggregate events from the stats JSONL file.

#### `get_summary`

```python
def get_summary(self) -> Dict
```

- **Returns:** `Dict`
- **Description:** Get aggregate summary across all models.

### `tokenpak.telemetry.model_analytics.ModelStats`

**Bases:** object

Aggregated metrics for a single model.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`
- **Description:** Convert model stats to dictionary for serialization or reporting.

### `tokenpak.telemetry.models.ContextCapsule`

**Bases:** object

Structured wrapper for a compressed context payload.

Produced by the Context Composer before prompt injection. Contains the
final compressed content plus metadata about budget usage, segment
inclusion/exclusion, compression stats, and provenance.

#### `is_over_budget`

```python
def is_over_budget(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if actual_tokens exceeds budget_tokens.

#### `efficiency_score`

```python
def efficiency_score(self) -> float
```

- **Returns:** `float`
- **Description:** Budget utilization efficiency (0-1).

### `tokenpak.telemetry.models.Cost`

**Bases:** object

Cost computation result for a single LLM call.

Parameters
----------
trace_id:
    Parent trace identifier.
cost_input:
    Provider-reported cost for input tokens (USD).
cost_output:
    Provider-reported cost for output tokens (USD).
cost_cache_read:
    Provider-reported cost for cache read tokens (USD).
cost_cache_write:
    Provider-reported cost for cache write tokens (USD).
cost_total:
    Total provider-reported cost (USD).
cost_source:
    Source of cost data: ``"provider"``, ``"estimated"``, or ``"unknown"``.
baseline_cost:
    What the call would have cost without compression (USD).
savings_total:
    Total savings = ``baseline_cost - cost_total`` (USD).
savings_qmd:
    Savings attributable to the QMD pass (USD).
savings_tp:
    Savings attributable to the TokenPak compression pass (USD).

#### `savings_pct`

```python
def savings_pct(self) -> float
```

- **Returns:** `float`
- **Description:** Percentage savings relative to baseline (0–100).  Returns 0 when

### `tokenpak.telemetry.models.TelemetryEvent`

**Bases:** object

Top-level lifecycle event for a single LLM request/response cycle.

Parameters
----------
trace_id:
    Globally unique identifier for the full conversation trace.
request_id:
    Identifier for this specific request within the trace.
event_type:
    Lifecycle phase: ``"request_start"``, ``"request_end"``,
    ``"error"``, ``"cache_hit"``, ``"retry"``, …
ts:
    Unix timestamp (float seconds) at which the event was recorded.
provider:
    Lower-case provider name: ``"anthropic"``, ``"openai"``,
    ``"gemini"``, ``"unknown"``.
model:
    Model identifier as reported by the provider.
agent_id:
    Optional identifier for the agent / worker that issued the call.
api:
    API endpoint used (e.g. ``"anthropic-messages"``, ``"openai-responses"``).
stop_reason:
    Provider-reported stop reason (e.g. ``"end_turn"``, ``"max_tokens"``).
session_id:
    Session identifier from which this event originated.
duration_ms:
    Request duration in milliseconds.
status:
    Outcome: ``"ok"``, ``"error"``, ``"timeout"``, ``"cancelled"``.
error_class:
    Exception class name when ``status == "error"``; ``None`` otherwise.
payload:
    Arbitrary JSON-serialisable dict for additional event metadata.

#### `payload_json`

```python
def payload_json(self) -> str
```

- **Returns:** `str`
- **Description:** Return :attr:`payload` serialised as a JSON string.

### `tokenpak.telemetry.monitoring.audit_trail.AuditTrail`

**Bases:** object

Collects audit events for a single request and flushes them to the
RequestLogger in one batch.

Parameters
----------
request_id : str
    Shared with the corresponding RequestLogRecord for correlation.

#### `__init__`

```python
def __init__(self, request_id: str) -> None
```

- **Returns:** `None`

#### `record_compile`

```python
def record_compile(self, *, input_block_count: int = 0, output_block_count: int = 0, blocks_removed: Optional[List[Dict[str, Any]]] = None, compression_method: str = '', stage_timings: Optional[Dict[str, float]] = None, input_block_types: Optional[Dict[str, int]] = None, output_block_types: Optional[Dict[str, int]] = None, tokens_before: int = 0, tokens_after: int = 0) -> None
```

- **Returns:** `None`
- **Description:** Record a /compile (compression) decision.

#### `record_cache`

```python
def record_cache(self, *, operation: str = 'get', block_id: str = '', hit: Optional[bool] = None, cached_size: int = 0) -> None
```

- **Returns:** `None`
- **Description:** Record a /cache/* operation.

#### `record_metrics`

```python
def record_metrics(self, *, aggregation_window: str = '', data_points_returned: int = 0) -> None
```

- **Returns:** `None`
- **Description:** Record a /metrics aggregation event.

#### `record_error`

```python
def record_error(self, *, error_type: str, message: str, **extra: Any) -> None
```

- **Returns:** `None`
- **Description:** Record an error that occurred during request processing.

#### `flush`

```python
def flush(self) -> None
```

- **Returns:** `None`
- **Description:** Enqueue all recorded events to the RequestLogger.

### `tokenpak.telemetry.monitoring.health.HealthChecker`

**Bases:** object

Assembles a full /health response payload.

Parameters
----------
start_time : float
    Unix timestamp of when the proxy process started (for uptime calc).
version : str
    Proxy version string (defaults to tokenpak.__version__).

#### `__init__`

```python
def __init__(self, start_time: Optional[float] = None, version: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `check`

```python
def check(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Run all health checks and return the JSON-ready response dict.

### `tokenpak.telemetry.monitoring.metrics.ProxyMetricsCollector`

**Bases:** object

Collects and renders TokenPak proxy metrics in Prometheus text format.

Parameters
----------
proxy_server : ProxyServer, optional
    Live proxy server instance for session + circuit-breaker data.
db_path : str or Path, optional
    Path to the TelemetryDB for per-provider/model breakdowns.
    Falls back to the default ``telemetry.db`` path if not set.

#### `__init__`

```python
def __init__(self, proxy_server: Optional[Any] = None, db_path: Optional[Any] = None) -> None
```

- **Returns:** `None`

#### `collect`

```python
def collect(self) -> str
```

- **Returns:** `str`
- **Description:** Collect all metrics and return Prometheus text format string.

### `tokenpak.telemetry.monitoring.provider_health.ProviderHealthMonitor`

**Bases:** object

Thread-safe provider health tracking with rolling 1-hour window.

#### `__init__`

```python
def __init__(self) -> Any
```

- **Returns:** `Any`

#### `record_request`

```python
def record_request(self, provider: str, latency_ms: float, status_code: int, timestamp: Optional[float] = None) -> None
```

- **Returns:** `None`
- **Description:** Record a request outcome for a provider.

#### `get_provider_health`

```python
def get_provider_health(self, provider: str) -> Optional[dict]
```

- **Returns:** `Optional[dict]`
- **Description:** Get current health for a single provider.

#### `get_all_health`

```python
def get_all_health(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return health summary for all providers.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Clear all metrics (for testing).

### `tokenpak.telemetry.monitoring.provider_health.ProviderMetrics`

**Bases:** object

Per-provider aggregated metrics.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return dict without latencies list (summary only).

### `tokenpak.telemetry.monitoring.request_logger.RequestLogRecord`

**Bases:** object

Immutable snapshot of a single proxied request/response cycle.

#### `__init__`

```python
def __init__(self, *, request_id: str, timestamp: str, level: str = LEVEL_INFO, client_ip: str = '', method: str = 'POST', endpoint: str = '', request_body_size: int = 0, response_status: int = 0, response_body_size: int = 0, compression_ratio: Optional[float] = None, latency_ms: float = 0.0, model: str = '', provider: str = '', extra: Optional[Dict[str, Any]] = None) -> None
```

- **Returns:** `None`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `to_json`

```python
def to_json(self) -> str
```

- **Returns:** `str`

#### `to_text`

```python
def to_text(self) -> str
```

- **Returns:** `str`

### `tokenpak.telemetry.monitoring.request_logger.RequestLogger`

**Bases:** object

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

#### `__init__`

```python
def __init__(self, config: Optional[Dict[str, Any]] = None) -> None
```

- **Returns:** `None`

#### `get_instance`

```python
def get_instance(cls) -> 'RequestLogger'
```

- **Returns:** `'RequestLogger'`
- **Description:** Return the process-wide singleton, creating it if needed.

#### `reset_instance`

```python
def reset_instance(cls) -> None
```

- **Returns:** `None`
- **Description:** Reset singleton (test helper).

#### `new_request_id`

```python
def new_request_id(headers: Optional[Dict[str, str]] = None) -> str
```

- **Returns:** `str`
- **Description:** Generate a new request UUID (v4).

#### `build_record`

```python
def build_record(self, *, request_id: str, client_ip: str = '', method: str = 'POST', endpoint: str = '', request_body_size: int = 0, response_status: int = 0, response_body_size: int = 0, compression_ratio: Optional[float] = None, latency_ms: float = 0.0, model: str = '', provider: str = '', extra: Optional[Dict[str, Any]] = None) -> RequestLogRecord
```

- **Returns:** `RequestLogRecord`
- **Description:** Build a RequestLogRecord with current timestamp.

#### `log`

```python
def log(self, record: RequestLogRecord) -> None
```

- **Returns:** `None`
- **Description:** Enqueue a record for async writing (non-blocking).

#### `log_dict`

```python
def log_dict(self, level: str = LEVEL_INFO, **kwargs: Any) -> None
```

- **Returns:** `None`
- **Description:** Convenience: log an arbitrary dict (debug/info/warn).

#### `stop`

```python
def stop(self, timeout: float = 5.0) -> None
```

- **Returns:** `None`
- **Description:** Flush remaining queue entries and stop the background thread.

### `tokenpak.telemetry.monitoring.request_size.RequestSizeMonitor`

**Bases:** object

Thread-safe request size monitor with tiered alerting.

#### `__init__`

```python
def __init__(self, config: Optional[RequestSizeConfig] = None) -> Any
```

- **Returns:** `Any`

#### `check_request_size`

```python
def check_request_size(self, request_body_size: int, session_id: Optional[str] = None) -> Optional[SizeAlert]
```

- **Returns:** `Optional[SizeAlert]`
- **Description:** Check request size against thresholds.

#### `reset_session`

```python
def reset_session(self, session_id: Optional[str] = None) -> None
```

- **Returns:** `None`
- **Description:** Reset alert state for a session (e.g., after /compact).

#### `get_stats`

```python
def get_stats(self) -> RequestSizeStats
```

- **Returns:** `RequestSizeStats`
- **Description:** Get monitoring statistics.

#### `get_alert_history`

```python
def get_alert_history(self, limit: int = 50) -> List[SizeAlertRecord]
```

- **Returns:** `List[SizeAlertRecord]`
- **Description:** Get recent alert history.

#### `to_dict`

```python
def to_dict(self) -> RequestSizeSnapshot
```

- **Returns:** `RequestSizeSnapshot`
- **Description:** Serialize to dictionary for telemetry/logging.

### `tokenpak.telemetry.monitoring.server.MonitorHandler`

**Bases:** BaseHTTPRequestHandler

Simple HTTP handler for the monitor dashboard.

#### `log_message`

```python
def log_message(self, fmt: str, *args: object) -> None
```

- **Returns:** `None`

#### `do_GET`

```python
def do_GET(self) -> None
```

- **Returns:** `None`

### `tokenpak.telemetry.operational.metrics.MetricHistogram`

**Bases:** object

Histogram metric (latency buckets).

#### `observe`

```python
def observe(self, value: float) -> Any
```

- **Returns:** `Any`
- **Description:** Record a value in the histogram.

#### `mean`

```python
def mean(self) -> float
```

- **Returns:** `float`

### `tokenpak.telemetry.operational.metrics.MetricsCollector`

**Bases:** object

Central metrics collection.

#### `record_ingest`

```python
def record_ingest(self, latency: float, success: bool = True) -> Any
```

- **Returns:** `Any`
- **Description:** Record an ingest event.

#### `record_rollup`

```python
def record_rollup(self, duration: float) -> Any
```

- **Returns:** `Any`
- **Description:** Record a rollup job completion.

#### `to_prometheus_format`

```python
def to_prometheus_format(self) -> str
```

- **Returns:** `str`
- **Description:** Generate Prometheus-compatible output.

### `tokenpak.telemetry.operational.rbac_auth.RBACStore`

**Bases:** object

SQLite-backed store for users, sessions, and API keys.

#### `__init__`

```python
def __init__(self, db_path: str) -> Any
```

- **Returns:** `Any`

#### `create_user`

```python
def create_user(self, username: str, password: str, role: Role, email: Optional[str] = None, created_by_id: Optional[str] = None) -> User
```

- **Returns:** `User`

#### `get_user_by_id`

```python
def get_user_by_id(self, user_id: str) -> Optional[User]
```

- **Returns:** `Optional[User]`

#### `get_user_by_username`

```python
def get_user_by_username(self, username: str) -> Optional[User]
```

- **Returns:** `Optional[User]`

#### `list_users`

```python
def list_users(self, limit: int = 50, offset: int = 0) -> list[dict]
```

- **Returns:** `list[dict]`

#### `update_user`

```python
def update_user(self, user_id: str, *, role: Optional[str] = None, email: Optional[str] = None, is_active: Optional[bool] = None, updated_by_id: Optional[str] = None) -> Optional[User]
```

- **Returns:** `Optional[User]`

#### `deactivate_user`

```python
def deactivate_user(self, user_id: str, deactivated_by_id: Optional[str] = None) -> bool
```

- **Returns:** `bool`

#### `authenticate`

```python
def authenticate(self, username: str, password: str) -> Optional[User]
```

- **Returns:** `Optional[User]`

#### `create_session`

```python
def create_session(self, user: User, ip: Optional[str] = None, user_agent: Optional[str] = None) -> str
```

- **Returns:** `str`

#### `validate_session`

```python
def validate_session(self, token: str) -> Optional[User]
```

- **Returns:** `Optional[User]`

#### `invalidate_session`

```python
def invalidate_session(self, token: str) -> bool
```

- **Returns:** `bool`

#### `create_api_key`

```python
def create_api_key(self, user: User, name: str = '', role: Optional[Role] = None, expires_in_days: Optional[int] = None) -> tuple[str, dict]
```

- **Returns:** `tuple[str, dict]`
- **Description:** Returns (raw_key, key_record). Store raw_key — it won't be shown again.

#### `validate_api_key`

```python
def validate_api_key(self, raw_key: str) -> Optional[User]
```

- **Returns:** `Optional[User]`

#### `list_api_keys`

```python
def list_api_keys(self, user_id: str) -> list[dict]
```

- **Returns:** `list[dict]`

#### `revoke_api_key`

```python
def revoke_api_key(self, key_id: str, user_id: str) -> bool
```

- **Returns:** `bool`

#### `get_audit_log`

```python
def get_audit_log(self, limit: int = 100, offset: int = 0) -> list[dict]
```

- **Returns:** `list[dict]`

### `tokenpak.telemetry.operational.rbac_core.AccessControl`

**Bases:** object

Static helpers for permission checks.

#### `user_can`

```python
def user_can(user: Optional[User], permission: Permission) -> bool
```

- **Returns:** `bool`

#### `get_allowed_permissions`

```python
def get_allowed_permissions(role: Role) -> Set[Permission]
```

- **Returns:** `Set[Permission]`

#### `get_role_description`

```python
def get_role_description(role: Role) -> str
```

- **Returns:** `str`

### `tokenpak.telemetry.operational.rbac_core.User`

**Bases:** object

User with role and settings.

#### `has_permission`

```python
def has_permission(self, permission: Permission) -> bool
```

- **Returns:** `bool`
- **Description:** Check if user has permission.

#### `has_any_permission`

```python
def has_any_permission(self, *permissions: Permission) -> bool
```

- **Returns:** `bool`
- **Description:** Check if user has any of the permissions.

#### `has_all_permissions`

```python
def has_all_permissions(self, *permissions: Permission) -> bool
```

- **Returns:** `bool`
- **Description:** Check if user has all permissions.

### `tokenpak.telemetry.pipeline.ShadowHookProtocol`

**Bases:** Protocol

#### `record_request`

```python
def record_request(self, model: str, query: str, context_tokens: int = 0) -> Optional[int]
```

- **Returns:** `Optional[int]`

#### `record_response`

```python
def record_response(self, txn_key: Optional[int], response_text: str, response_tokens: int = 0, latency_ms: float = 0.0, context_blocks: Optional[list[str]] = None) -> Optional[int]
```

- **Returns:** `Optional[int]`

### `tokenpak.telemetry.pipeline.TelemetryPipeline`

**Bases:** object

Orchestrates telemetry event processing through stages.

Errors at any stage don't block storage of partial data.

#### `__init__`

```python
def __init__(self, storage: TelemetryDB) -> Any
```

- **Returns:** `Any`

#### `process`

```python
def process(self, raw_event: dict[str, Any]) -> PipelineResult
```

- **Returns:** `PipelineResult`
- **Description:** Process a raw telemetry event through all pipeline stages.

### `tokenpak.telemetry.pipeline_trace.PipelineTrace`

**Bases:** object

Complete trace for one request through the compression pipeline.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert to JSON-serializable dict.

### `tokenpak.telemetry.pipeline_trace.TraceStorage`

**Bases:** object

In-memory storage for pipeline traces (FIFO, last N traces).

#### `__init__`

```python
def __init__(self, max_size: int = 10) -> Any
```

- **Returns:** `Any`

#### `add`

```python
def add(self, trace: PipelineTrace) -> None
```

- **Returns:** `None`
- **Description:** Add a trace to storage.

#### `get_last`

```python
def get_last(self) -> Optional[PipelineTrace]
```

- **Returns:** `Optional[PipelineTrace]`
- **Description:** Get the most recent trace.

#### `get_by_id`

```python
def get_by_id(self, request_id: str) -> Optional[PipelineTrace]
```

- **Returns:** `Optional[PipelineTrace]`
- **Description:** Get a trace by request ID.

#### `get_all`

```python
def get_all(self) -> List[PipelineTrace]
```

- **Returns:** `List[PipelineTrace]`
- **Description:** Get all stored traces.

### `tokenpak.telemetry.post_run.IterationCache`

**Bases:** object

Tiny in-memory cache with TTL for iterative loops.

#### `__init__`

```python
def __init__(self, ttl_seconds: float = 120.0) -> None
```

- **Returns:** `None`

#### `set`

```python
def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None
```

- **Returns:** `None`

#### `get`

```python
def get(self, key: str) -> Any
```

- **Returns:** `Any`

### `tokenpak.telemetry.post_run.PostRunProcessor`

**Bases:** object

Handles logging, writeback, and adaptive post-run learning.

#### `__init__`

```python
def __init__(self, artifacts_dir: Path | str, log_path: Path | str, retrieval_rules_path: Path | str, index_path: Optional[Path | str] = None, cache_ttl_seconds: float = 120.0) -> None
```

- **Returns:** `None`

#### `process`

```python
def process(self, *, response_text: str, tokens_in: int, tokens_out: int, tier: str, injected_chunks: Iterable[str], latency_ms: float) -> PostRunResult
```

- **Returns:** `PostRunResult`

#### `cache_retrieval`

```python
def cache_retrieval(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None
```

- **Returns:** `None`

#### `get_cached_retrieval`

```python
def get_cached_retrieval(self, key: str) -> Any
```

- **Returns:** `Any`

### `tokenpak.telemetry.pricing.ModelPricing`

**Bases:** object

Pricing record for a single model.

Parameters
----------
model:
    Model identifier (as it appears in the catalog).
provider:
    Provider name (``"anthropic"``, ``"openai"``, ``"gemini"``).
input_per_token:
    USD cost per input token.
output_per_token:
    USD cost per output token.
cache_read_per_token:
    USD cost per cache-read token (``None`` if caching not supported).
cache_write_per_token:
    USD cost per cache-write token (``None`` if caching not supported).

#### `__init__`

```python
def __init__(self, model: str, provider: str, input_per_token: float, output_per_token: float, cache_read_per_token: Optional[float], cache_write_per_token: Optional[float]) -> None
```

- **Returns:** `None`

#### `from_dict`

```python
def from_dict(cls, model: str, data: dict[str, Any]) -> 'ModelPricing'
```

- **Returns:** `'ModelPricing'`
- **Description:** Construct from a catalog ``models`` entry dict.

### `tokenpak.telemetry.pricing.PricingCatalog`

**Bases:** object

Versioned pricing catalog loaded from ``pricing_catalog.json``.

Attributes
----------
version:
    Catalog version string (from ``_meta.version``).
models:
    Dict mapping model identifiers to :class:`ModelPricing` records.

Examples
--------
>>> catalog = PricingCatalog.load()
>>> cost = catalog.compute_cost(
...     trace_id="t1",
...     model="claude-sonnet-4-6",
...     baseline_input_tokens=100_000,
...     actual_input_tokens=60_000,
...     output_tokens=5_000,
...     cache_read=20_000,
... )

#### `__init__`

```python
def __init__(self, version: str, models: dict[str, ModelPricing], updated: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `load`

```python
def load(cls, path: Optional[os.PathLike[str]] = None) -> 'PricingCatalog'
```

- **Returns:** `'PricingCatalog'`
- **Description:** Load and parse the pricing catalog from *path*.

#### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> 'PricingCatalog'
```

- **Returns:** `'PricingCatalog'`
- **Description:** Construct a catalog from an already-parsed dict (useful in tests).

#### `check_staleness`

```python
def check_staleness(self) -> None
```

- **Returns:** `None`
- **Description:** Log a warning if catalog pricing data is older than _STALENESS_DAYS.

#### `get_model`

```python
def get_model(self, model: str) -> Optional[ModelPricing]
```

- **Returns:** `Optional[ModelPricing]`
- **Description:** Return pricing for *model*, or ``None`` if not in catalog.

#### `known_models`

```python
def known_models(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return a sorted list of all model identifiers in the catalog.

#### `compute_cost`

```python
def compute_cost(self, model: str, baseline_input_tokens: int, actual_input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0, trace_id: str = '', savings_qmd: float = 0.0, savings_tp: float = 0.0) -> Cost
```

- **Returns:** `Cost`
- **Description:** Compute cost and compression savings for a single LLM call.

### `tokenpak.telemetry.prometheus.PrometheusMetricsCollector`

**Bases:** object

Collects and renders TokenPak metrics in Prometheus text exposition format.

Usage::

    collector = PrometheusMetricsCollector(storage)
    text = collector.collect()
    # Return as text/plain; charset=utf-8

#### `__init__`

```python
def __init__(self, storage: 'TelemetryDB', circuit_breaker: Optional[Any] = None) -> None
```

- **Returns:** `None`

#### `collect`

```python
def collect(self) -> str
```

- **Returns:** `str`
- **Description:** Query storage and render full Prometheus metrics text.

### `tokenpak.telemetry.proxy_collector.RequestStats`

**Bases:** object

Stats for a single request through the TokenPak proxy.

#### `failover_indicator`

```python
def failover_indicator(self) -> Optional[str]
```

- **Returns:** `Optional[str]`
- **Description:** Generate failover indicator string if failover occurred.

#### `footer_oneline`

```python
def footer_oneline(self) -> str
```

- **Returns:** `str`

#### `to_dict`

```python
def to_dict(self) -> RequestStatsDict
```

- **Returns:** `RequestStatsDict`

### `tokenpak.telemetry.proxy_collector.SessionStats`

**Bases:** object

Aggregated stats across all requests since the proxy started.

#### `session_total_percent`

```python
def session_total_percent(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> SessionStatsDict
```

- **Returns:** `SessionStatsDict`

### `tokenpak.telemetry.proxy_collector.TelemetryCollector`

**Bases:** object

Thread-safe, in-memory stats collector for the TokenPak proxy.

#### `__init__`

```python
def __init__(self, max_history: int = 500) -> Any
```

- **Returns:** `Any`

#### `record`

```python
def record(self, request_id: str, input_tokens_raw: int, input_tokens_sent: int, cost_saved: float = 0.0) -> RequestStats
```

- **Returns:** `RequestStats`
- **Description:** Record a completed proxy request and return its stats.

#### `get_last`

```python
def get_last(self) -> Optional[RequestStats]
```

- **Returns:** `Optional[RequestStats]`

#### `get_session`

```python
def get_session(self) -> SessionStats
```

- **Returns:** `SessionStats`

#### `get_history`

```python
def get_history(self, limit: int = 10) -> list[RequestStats]
```

- **Returns:** `list[RequestStats]`

#### `reset_session`

```python
def reset_session(self) -> None
```

- **Returns:** `None`

#### `create_demo_stats`

```python
def create_demo_stats() -> tuple[RequestStats, SessionStats]
```

- **Returns:** `tuple[RequestStats, SessionStats]`

### `tokenpak.telemetry.proxy_storage.TelemetryStorage`

**Bases:** object

Persist request stats to a local SQLite database.

Usage::

    storage = TelemetryStorage("~/.tokenpak/telemetry.db")
    storage.save_request(stats)
    rows = storage.list_requests(limit=50)
    storage.close()

#### `__init__`

```python
def __init__(self, db_path: str = ':memory:') -> Any
```

- **Returns:** `Any`

#### `save_request`

```python
def save_request(self, stats: RequestStats) -> None
```

- **Returns:** `None`
- **Description:** Persist a single request's stats.

#### `list_requests`

```python
def list_requests(self, limit: int = 100) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return recent requests as dicts, most recent first.

#### `save_session`

```python
def save_session(self, session: SessionStats, ended_at: Optional[datetime] = None) -> int
```

- **Returns:** `int`
- **Description:** Persist session summary and return the row id.

#### `lifetime_totals`

```python
def lifetime_totals(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return all-time aggregates across persisted sessions.

#### `prune`

```python
def prune(self, days: int = 30) -> int
```

- **Returns:** `int`
- **Description:** Delete requests older than N days. Returns number of rows deleted.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`

### `tokenpak.telemetry.proxy_trace_integration.ProxyTraceCapture`

**Bases:** object

Helps proxy capture trace data as request flows through pipeline.

#### `__init__`

```python
def __init__(self, request_id: Optional[str] = None) -> Any
```

- **Returns:** `Any`

#### `record_capsule_stage`

```python
def record_capsule_stage(self, input_tokens: int, output_tokens: int, blocks_matched: int = 0, block_names: Optional[List[str]] = None, tokens_injected: int = 0, duration_ms: float = 0.0) -> None
```

- **Returns:** `None`
- **Description:** Record capsule/vault injection stage.

#### `record_segmentizer_stage`

```python
def record_segmentizer_stage(self, input_tokens: int, output_tokens: int, segments_found: int = 0, compressible: int = 0, protected: int = 0, duration_ms: float = 0.0) -> None
```

- **Returns:** `None`
- **Description:** Record segmentizer analysis stage.

#### `record_recipe_engine_stage`

```python
def record_recipe_engine_stage(self, input_tokens: int, output_tokens: int, recipe_applied: str = '', rules_fired: int = 0, tokens_pruned: int = 0, duration_ms: float = 0.0) -> None
```

- **Returns:** `None`
- **Description:** Record recipe engine transformation stage.

#### `record_slot_filler_stage`

```python
def record_slot_filler_stage(self, input_tokens: int, output_tokens: int, refs_resolved: int = 0, ref_names: Optional[List[str]] = None, tokens_saved: int = 0, duration_ms: float = 0.0) -> None
```

- **Returns:** `None`
- **Description:** Record slot/ref filler stage.

#### `record_validation_gate_stage`

```python
def record_validation_gate_stage(self, input_tokens: int, output_tokens: int, passed: bool = True, checks: Optional[List[str]] = None, duration_ms: float = 0.0) -> None
```

- **Returns:** `None`
- **Description:** Record validation gate stage.

#### `finalize`

```python
def finalize(self, cost_saved: float = 0.0) -> PipelineTrace
```

- **Returns:** `PipelineTrace`
- **Description:** Finalize the trace and calculate summary stats.

### `tokenpak.telemetry.query.api.EntryStore`

**Bases:** object

Load and aggregate entries from JSONL date-partitioned files.

#### `__init__`

```python
def __init__(self, entries_dir: Optional[Path] = None) -> None
```

- **Returns:** `None`

#### `read_entries`

```python
def read_entries(self, start_date: str, end_date: str, limit: Optional[int] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Load entries from JSONL files in the given date range.

#### `compute_stats`

```python
def compute_stats(self, date: str) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Aggregate metrics for a single date.

#### `compute_rollups`

```python
def compute_rollups(self, start_date: str, end_date: str, window_minutes: int = 5) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Time-series rollups with configurable window.

#### `top_users`

```python
def top_users(self, date: str, limit: int = 10) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return top agents by request count for a date.

#### `cache_trends`

```python
def cache_trends(self, start_date: str, end_date: str) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Cache hit rate over time (one point per day).

#### `compression_ratios`

```python
def compression_ratios(self, date: str) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Average compression ratio per agent for a date.

#### `usage_summary`

```python
def usage_summary(self, date: str) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Daily usage summary across all agents.

### `tokenpak.telemetry.query.audit.AuditGenerator`

**Bases:** object

Generate cost breakdowns by model and feature.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `model_breakdown`

```python
def model_breakdown(self, entries: list[dict[str, Any]]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Calculate total cost by model.

#### `feature_breakdown`

```python
def feature_breakdown(self, entries: list[dict[str, Any]]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Calculate cost attribution by feature.

#### `combined_breakdown`

```python
def combined_breakdown(self, entries: list[dict[str, Any]]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Combined model + feature breakdown.

#### `session_audit`

```python
def session_audit(self, entries: list[dict[str, Any]]) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Quick audit for current session (simple format).

### `tokenpak.telemetry.query.timeline.TimelineGenerator`

**Bases:** object

Generate hourly buckets of cost/token data from entries.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `hourly_buckets`

```python
def hourly_buckets(self, entries: list[dict[str, Any]], start_hour: Optional[datetime] = None, num_hours: int = 24) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Bucket entries by hour.

#### `model_breakdown`

```python
def model_breakdown(self, entries: list[dict[str, Any]]) -> dict[str, float]
```

- **Returns:** `dict[str, float]`
- **Description:** Calculate total cost by model.

### `tokenpak.telemetry.query_dsl.QueryFilter`

**Bases:** object

Filter parameters for telemetry database queries.

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Serialize this query result to a plain dict.

#### `is_empty`

```python
def is_empty(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if this filter has no active constraints.

### `tokenpak.telemetry.recommendations.Recommendation`

**Bases:** object

A single ranked, evidence-backed recommendation.

Telemetry-local DTO: ranked advice, not the canonical per-request TIP
recommendation contract, despite the shared historical name. The module
path and class name are kept as-is: they are compatibility surface for
reflection and default pickle globals across releases.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.telemetry.recommendations.RecommendationsEngine`

**Bases:** object

Telemetry-driven recommendation engine.

Parameters
----------
db_path:
    Optional path to ``telemetry.db``. When omitted, resolves via
    :func:`tokenpak.core.paths.get_db_path` so the engine reads the same
    store as the rest of the CLI.

#### `__init__`

```python
def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None
```

- **Returns:** `None`

#### `db_path`

```python
def db_path(self) -> str
```

- **Returns:** `str`

#### `run`

```python
def run(self, *, window_hours: int = DEFAULT_WINDOW_HOURS, model: Optional[str] = None, platform: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> RecommendationsResult
```

- **Returns:** `RecommendationsResult`
- **Description:** Evaluate all rules and return a ranked result.

### `tokenpak.telemetry.recommendations.RecommendationsResult`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.telemetry.regression.baseline_registry.BaselineEntry`

**Bases:** object

Stored baseline for a workflow.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> BaselineEntry
```

- **Returns:** `BaselineEntry`
- **Description:** Create from dict.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Convert to dict for serialization.

### `tokenpak.telemetry.regression.baseline_registry.BaselineRegistry`

**Bases:** object

Store and manage workflow baselines.

#### `__init__`

```python
def __init__(self, registry_path: Optional[str] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize baseline registry.

#### `store_baseline`

```python
def store_baseline(self, entry: BaselineEntry) -> None
```

- **Returns:** `None`
- **Description:** Store or update a baseline.

#### `get_baseline`

```python
def get_baseline(self, workflow_id: str) -> Optional[BaselineEntry]
```

- **Returns:** `Optional[BaselineEntry]`
- **Description:** Retrieve a baseline.

#### `delete_baseline`

```python
def delete_baseline(self, workflow_id: str) -> None
```

- **Returns:** `None`
- **Description:** Delete a baseline.

#### `update_pass_count`

```python
def update_pass_count(self, workflow_id: str, increment: int = 1) -> None
```

- **Returns:** `None`
- **Description:** Increment pass count for a baseline.

#### `list_baselines`

```python
def list_baselines(self) -> Dict[str, BaselineEntry]
```

- **Returns:** `Dict[str, BaselineEntry]`
- **Description:** Get all baselines.

### `tokenpak.telemetry.regression.delta_detector.DeltaDetector`

**Bases:** object

Detect meaningful changes between states.

#### `__init__`

```python
def __init__(self, max_trivial_lines: int = 15, max_trivial_files: int = 2, trivial_magnitude_threshold: float = 0.15) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize delta detector.

#### `compute_delta`

```python
def compute_delta(self, current_state: Dict[str, Any], baseline_state: Dict[str, Any]) -> DeltaResult
```

- **Returns:** `DeltaResult`
- **Description:** Compute delta between current and baseline state.

#### `should_reuse_baseline`

```python
def should_reuse_baseline(self, delta: DeltaResult, baseline_still_passes: bool) -> bool
```

- **Returns:** `bool`
- **Description:** Decide whether to reuse baseline artifact.

#### `should_validate_only`

```python
def should_validate_only(self, delta: DeltaResult) -> bool
```

- **Returns:** `bool`
- **Description:** Decide whether to run validation-only (not full regen).

#### `should_regenerate`

```python
def should_regenerate(self, delta: DeltaResult) -> bool
```

- **Returns:** `bool`
- **Description:** Decide whether to regenerate (full recomputation).

### `tokenpak.telemetry.regression.delta_detector.DeltaResult`

**Bases:** object

Result of delta measurement.

#### `is_moderate`

```python
def is_moderate(self) -> bool
```

- **Returns:** `bool`
- **Description:** Check if delta is moderate (not trivial, not large).

#### `is_large`

```python
def is_large(self) -> bool
```

- **Returns:** `bool`
- **Description:** Check if delta is large.

### `tokenpak.telemetry.regression.feature_detector.FeatureResult`

**Bases:** object

#### `drift_magnitude`

```python
def drift_magnitude(self) -> float
```

- **Returns:** `float`

#### `passed`

```python
def passed(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.telemetry.regression.retrieval_watchdog.QueryRetrievalRecord`

**Bases:** object

Snapshot of retrieval metrics for a single query.

#### `dedup_rate`

```python
def dedup_rate(self) -> float
```

- **Returns:** `float`
- **Description:** Fraction of chunks that survived deduplication (higher = less duplication).

#### `mean_relevance`

```python
def mean_relevance(self) -> float
```

- **Returns:** `float`
- **Description:** Mean relevance score across all chunks.

#### `irrelevant_source_rate`

```python
def irrelevant_source_rate(self) -> float
```

- **Returns:** `float`
- **Description:** Fraction of chunks whose relevance is below 0.3 (proxy for irrelevance).

#### `source_diversity`

```python
def source_diversity(self) -> float
```

- **Returns:** `float`
- **Description:** Fraction of unique sources vs total chunks (higher = more diverse).

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'QueryRetrievalRecord'
```

- **Returns:** `'QueryRetrievalRecord'`

### `tokenpak.telemetry.regression.retrieval_watchdog.RetrievalAlert`

**Bases:** object

Alert fired when retrieval quality drifts.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.telemetry.regression.retrieval_watchdog.RetrievalQualityWatchdog`

**Bases:** object

Monitor retrieval quality drift and optionally apply remediation.

Usage::

    watchdog = RetrievalQualityWatchdog()
    record = QueryRetrievalRecord(
        query_id="q1",
        query_text="How does auth work?",
        chunk_count=12,
        unique_chunk_count=10,
        relevance_scores=[0.9, 0.7, 0.2, ...],
        source_ids=["a.py", "b.py", ...],
        chunk_ids_ordered=["c1", "c2", ...],
    )
    alert = watchdog.observe(record)
    if alert:
        print(alert)

#### `__init__`

```python
def __init__(self, history_path: Optional[str] = None, baseline_window: int = _BASELINE_WINDOW, chunk_growth_threshold: float = _CHUNK_COUNT_GROWTH_PCT, dedup_drop_threshold: float = _DEDUP_RATE_DROP, irrelevant_source_threshold: float = _IRRELEVANT_SOURCE_PCT, order_instability_threshold: float = _ORDER_INSTABILITY_THRESHOLD, remediation_fn: Optional[Callable[[RetrievalAlert], List[str]]] = None, auto_remediate: bool = True) -> Any
```

- **Returns:** `Any`
- **Description:** Args:

#### `observe`

```python
def observe(self, record: QueryRetrievalRecord) -> Optional[RetrievalAlert]
```

- **Returns:** `Optional[RetrievalAlert]`
- **Description:** Record a retrieval event and check for quality drift.

#### `get_baseline`

```python
def get_baseline(self) -> RetrievalBaseline
```

- **Returns:** `RetrievalBaseline`
- **Description:** Return the current rolling baseline.

#### `get_alerts`

```python
def get_alerts(self, last_n: Optional[int] = None) -> List[RetrievalAlert]
```

- **Returns:** `List[RetrievalAlert]`
- **Description:** Return recent alerts.

#### `history`

```python
def history(self) -> List[QueryRetrievalRecord]
```

- **Returns:** `List[QueryRetrievalRecord]`
- **Description:** Return all records in the rolling window.

### `tokenpak.telemetry.regression.stability_scorer.RunRecord`

**Bases:** object

Single execution record for a workflow.

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'RunRecord'
```

- **Returns:** `'RunRecord'`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.telemetry.regression.stability_scorer.StabilityScore`

**Bases:** object

Computed stability score for a workflow.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: Dict[str, Any]) -> 'StabilityScore'
```

- **Returns:** `'StabilityScore'`

### `tokenpak.telemetry.regression.stability_scorer.StabilityScorer`

**Bases:** object

Persistent stability scorer for TokenPak workflows.

Stores run records and computed scores in
``~/.tokenpak/stability_scores.json``.

#### `__init__`

```python
def __init__(self, store_path: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `record_run`

```python
def record_run(self, workflow_id: str, record: RunRecord) -> None
```

- **Returns:** `None`
- **Description:** Append a run record for a workflow and persist to disk.

#### `get_records`

```python
def get_records(self, workflow_id: str) -> List[RunRecord]
```

- **Returns:** `List[RunRecord]`
- **Description:** Return all stored RunRecords for a workflow.

#### `score_workflow`

```python
def score_workflow(self, workflow_id: str) -> StabilityScore
```

- **Returns:** `StabilityScore`
- **Description:** Compute (or recompute) stability score from stored records.

#### `get_cached_score`

```python
def get_cached_score(self, workflow_id: str) -> Optional[StabilityScore]
```

- **Returns:** `Optional[StabilityScore]`
- **Description:** Return the last cached score without recomputing.

#### `adjust_budget`

```python
def adjust_budget(self, workflow_id: str, base_budget: int) -> Tuple[int, str]
```

- **Returns:** `Tuple[int, str]`
- **Description:** Apply stability-based budget adjustment.

#### `all_scores`

```python
def all_scores(self) -> Dict[str, StabilityScore]
```

- **Returns:** `Dict[str, StabilityScore]`
- **Description:** Return cached scores for every tracked workflow.

#### `summary`

```python
def summary(self) -> str
```

- **Returns:** `str`
- **Description:** Human-readable summary of all tracked workflows.

### `tokenpak.telemetry.replay.ReplayEntry`

**Bases:** object

Metadata snapshot of a single proxied request for replay.

``messages`` and ``response`` are opt-in content fields. They are
``None`` by default and only populated when content capture is
explicitly enabled.

#### `new`

```python
def new(cls, provider: str, model: str, input_tokens_raw: int, input_tokens_sent: int, tokens_saved: int, cost_usd: float = 0.0, messages: Optional[list[JsonValue]] = None, response: Optional[JsonObject] = None, metadata: Optional[JsonObject] = None) -> 'ReplayEntry'
```

- **Returns:** `'ReplayEntry'`
- **Description:** Create a new entry with a fresh UUID and current timestamp.

#### `to_dict`

```python
def to_dict(self) -> JsonObject
```

- **Returns:** `JsonObject`

#### `from_row`

```python
def from_row(cls, row: sqlite3.Row) -> 'ReplayEntry'
```

- **Returns:** `'ReplayEntry'`

#### `savings_pct`

```python
def savings_pct(self) -> float
```

- **Returns:** `float`

#### `summary_line`

```python
def summary_line(self) -> str
```

- **Returns:** `str`

### `tokenpak.telemetry.replay.ReplayStore`

**Bases:** object

SQLite-backed store for capturing and retrieving replay entries.

Thread-safe via per-thread connections (WAL mode).

Args:
    db_path: Path to SQLite file.  Pass ``":memory:"`` for ephemeral
             (useful in tests).

#### `__init__`

```python
def __init__(self, db_path: str = ':memory:') -> Any
```

- **Returns:** `Any`

#### `capture`

```python
def capture(self, entry: ReplayEntry) -> None
```

- **Returns:** `None`
- **Description:** Persist a replay entry to the store.

#### `list`

```python
def list(self, limit: int = 20, provider: Optional[str] = None) -> list[ReplayEntry]
```

- **Returns:** `list[ReplayEntry]`
- **Description:** Return recent entries, most recent first.

#### `get`

```python
def get(self, replay_id: str) -> Optional[ReplayEntry]
```

- **Returns:** `Optional[ReplayEntry]`
- **Description:** Retrieve a single entry by id. Returns ``None`` if not found.

#### `delete`

```python
def delete(self, replay_id: str) -> bool
```

- **Returns:** `bool`
- **Description:** Delete an entry. Returns True if a row was removed.

#### `prune`

```python
def prune(self, days: int = 7) -> int
```

- **Returns:** `int`
- **Description:** Delete entries older than *days* days. Returns count removed (default 7 days).

#### `count`

```python
def count(self) -> int
```

- **Returns:** `int`
- **Description:** Return total number of stored entries.

#### `clear`

```python
def clear(self) -> int
```

- **Returns:** `int`
- **Description:** Delete ALL entries from the store. Returns count removed.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`

### `tokenpak.telemetry.request_audit.RequestAudit`

**Bases:** object

Per-request audit record with savings breakdown.

#### `compression_tokens_saved`

```python
def compression_tokens_saved(self) -> int
```

- **Returns:** `int`

#### `baseline_cost`

```python
def baseline_cost(self) -> float
```

- **Returns:** `float`
- **Description:** Cost without TokenPak (all tokens at full input price).

#### `actual_cost`

```python
def actual_cost(self) -> float
```

- **Returns:** `float`
- **Description:** Cost with TokenPak (compression + cache discounts).

#### `compression_savings`

```python
def compression_savings(self) -> float
```

- **Returns:** `float`
- **Description:** USD saved by compression.

#### `cache_savings`

```python
def cache_savings(self) -> float
```

- **Returns:** `float`
- **Description:** USD saved by cache reads (vs paying full input price).

#### `total_savings`

```python
def total_savings(self) -> float
```

- **Returns:** `float`

#### `savings_pct`

```python
def savings_pct(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.telemetry.request_audit.RequestAuditor`

**Bases:** object

In-memory bounded request audit tracker.

#### `__init__`

```python
def __init__(self, max_recent: int = 1000) -> Any
```

- **Returns:** `Any`

#### `record`

```python
def record(self, audit: RequestAudit) -> None
```

- **Returns:** `None`

#### `get_recent`

```python
def get_recent(self, n: int = 10) -> List[RequestAudit]
```

- **Returns:** `List[RequestAudit]`

#### `filter`

```python
def filter(self, since: Optional[float] = None, model: Optional[str] = None, request_id: Optional[str] = None) -> List[RequestAudit]
```

- **Returns:** `List[RequestAudit]`

#### `stats`

```python
def stats(self) -> dict
```

- **Returns:** `dict`

#### `to_csv`

```python
def to_csv(self, records: Optional[List[RequestAudit]] = None) -> str
```

- **Returns:** `str`

### `tokenpak.telemetry.rollups.RollupEngine`

**Bases:** object

Manages rollup queries and refresh operations.

The rollup tables are created by TelemetryDB when it initializes.
This class provides query interfaces and delegates refresh to the DB.

Parameters
----------
db:
    TelemetryDB instance to query rollups from.

#### `__init__`

```python
def __init__(self, db: TelemetryDB) -> None
```

- **Returns:** `None`

#### `ensure_tables`

```python
def ensure_tables(self) -> None
```

- **Returns:** `None`
- **Description:** Create state table if it doesn't exist.

#### `refresh_all`

```python
def refresh_all(self, days: int = 7) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Refresh all rollup tables.

#### `get_daily_model_rollups`

```python
def get_daily_model_rollups(self, days: int = 30, model: Optional[str] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return daily model rollups for the last N days.

#### `get_daily_provider_rollups`

```python
def get_daily_provider_rollups(self, days: int = 30, provider: Optional[str] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return daily provider rollups for the last N days.

#### `get_daily_agent_rollups`

```python
def get_daily_agent_rollups(self, days: int = 30, agent_id: Optional[str] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return daily agent rollups for the last N days.

#### `get_timeseries`

```python
def get_timeseries(self, metric: str = 'cost', interval: str = 'day', days: int = 30, provider: Optional[str] = None, model: Optional[str] = None, agent_id: Optional[str] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return timeseries data for charting.

#### `get_summary`

```python
def get_summary(self, days: int = 30, provider: Optional[str] = None, model: Optional[str] = None, agent_id: Optional[str] = None) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return aggregated summary statistics.

#### `get_last_refresh`

```python
def get_last_refresh(self) -> Optional[float]
```

- **Returns:** `Optional[float]`
- **Description:** Return timestamp of last rollup refresh.

#### `get_cost_components`

```python
def get_cost_components(self, days: int = 30) -> dict[str, float]
```

- **Returns:** `dict[str, float]`
- **Description:** Return cost breakdown by component.

#### `get_cache_stats`

```python
def get_cache_stats(self, days: int = 30) -> dict[str, float]
```

- **Returns:** `dict[str, float]`
- **Description:** Return cache efficiency stats.

#### `compute_daily_rollups`

```python
def compute_daily_rollups(self, date: Date | str) -> int
```

- **Returns:** `int`
- **Description:** Compute rollups for a specific calendar date. Idempotent.

#### `compute_hourly_rollups`

```python
def compute_hourly_rollups(self, date: Date | str) -> int
```

- **Returns:** `int`
- **Description:** Compute hourly rollups for a specific date. Idempotent.

#### `rebuild_all_rollups`

```python
def rebuild_all_rollups(self, from_date: Date, to_date: Date) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Rebuild daily rollups for a date range. Returns {dates_processed, total_rows}.

#### `check_consistency`

```python
def check_consistency(self, days: int = 7) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Verify rollup totals match raw event aggregates.

### `tokenpak.telemetry.settings.AlertSettings`

**Bases:** object

Read/write alert configuration from a JSON file.

#### `__init__`

```python
def __init__(self, config_path: str | pathlib.Path) -> None
```

- **Returns:** `None`

#### `load`

```python
def load(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return current config, merging with defaults for missing keys.

#### `save`

```python
def save(self, config: dict[str, Any]) -> None
```

- **Returns:** `None`
- **Description:** Validate and persist config atomically.

### `tokenpak.telemetry.stats.RequestStats`

**Bases:** object

Stats for a single request through TokenPak.

#### `footer_oneline`

```python
def footer_oneline(self) -> str
```

- **Returns:** `str`
- **Description:** Generate single-line footer format (without session total).

#### `to_dict`

```python
def to_dict(self) -> dict[str, StatsValue]
```

- **Returns:** `dict[str, StatsValue]`
- **Description:** Convert to dict.

### `tokenpak.telemetry.stats.SessionStats`

**Bases:** object

Aggregated stats for the current session (proxy uptime).

#### `session_total_percent`

```python
def session_total_percent(self) -> float
```

- **Returns:** `float`
- **Description:** Overall savings percentage.

#### `to_dict`

```python
def to_dict(self) -> dict[str, StatsValue]
```

- **Returns:** `dict[str, StatsValue]`
- **Description:** Convert to dict.

### `tokenpak.telemetry.stats.StatsStorage`

**Bases:** object

Track request stats and session aggregates.

#### `__init__`

```python
def __init__(self, max_history: int = 100) -> Any
```

- **Returns:** `Any`

#### `add_request`

```python
def add_request(self, request_id: str, input_tokens_raw: int, input_tokens_sent: int, cost_saved: float) -> RequestStats
```

- **Returns:** `RequestStats`
- **Description:** Record a request and update session totals.

#### `get_last`

```python
def get_last(self) -> Optional[RequestStats]
```

- **Returns:** `Optional[RequestStats]`
- **Description:** Get most recent request stats.

#### `get_last_with_session`

```python
def get_last_with_session(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Get last request stats combined with session totals.

#### `get_session`

```python
def get_session(self) -> SessionStats
```

- **Returns:** `SessionStats`
- **Description:** Get current session stats.

### `tokenpak.telemetry.storage.TelemetryDB`

**Bases:** object

SQLite-backed telemetry store.

Parameters
----------
path:
    Path to the SQLite database file.  Pass ``":memory:"`` for an
    in-memory database (useful for testing).

#### `__init__`

```python
def __init__(self, path: Union[str, Path] = ':memory:') -> None
```

- **Returns:** `None`

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`
- **Description:** Close every connection this store has opened (all threads).

#### `insert_event`

```python
def insert_event(self, event: TelemetryEvent) -> None
```

- **Returns:** `None`
- **Description:** Persist a single :class:`TelemetryEvent`.

#### `insert_events`

```python
def insert_events(self, events: list[TelemetryEvent]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert a list of :class:`TelemetryEvent` records.

#### `insert_usage`

```python
def insert_usage(self, usage: Usage) -> None
```

- **Returns:** `None`
- **Description:** Persist a single :class:`Usage` record.

#### `insert_usages`

```python
def insert_usages(self, usages: list[Usage]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert a list of :class:`Usage` records.

#### `insert_cost`

```python
def insert_cost(self, cost: Cost) -> None
```

- **Returns:** `None`
- **Description:** Persist a single :class:`Cost` record.

#### `insert_costs`

```python
def insert_costs(self, costs: list[Cost]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert a list of :class:`Cost` records.

#### `insert_segment`

```python
def insert_segment(self, segment: Segment) -> None
```

- **Returns:** `None`
- **Description:** Persist a single :class:`Segment` record.

#### `insert_segments`

```python
def insert_segments(self, segments: list[Segment]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert a list of :class:`Segment` records.

#### `insert_trace`

```python
def insert_trace(self, event: TelemetryEvent, usage: Optional[Usage] = None, cost: Optional[Cost] = None, segments: Optional[list[Segment]] = None) -> None
```

- **Returns:** `None`
- **Description:** Insert all data for a single trace in one call.

#### `get_trace`

```python
def get_trace(self, trace_id: str) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return all stored data for *trace_id* as a plain dict.

#### `get_segments`

```python
def get_segments(self, trace_id: str) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return all segment rows for *trace_id*, ordered by ``ord``.

#### `get_trace_events`

```python
def get_trace_events(self, trace_id: str) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return all event rows for *trace_id*, ordered chronologically by timestamp.

#### `list_traces`

```python
def list_traces(self, limit: int = 100, offset: int = 0, provider: Optional[str] = None, model: Optional[str] = None, agent_id: Optional[str] = None, since_ts: Optional[float] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return a paginated list of trace event summaries.

#### `upsert_pricing_catalog`

```python
def upsert_pricing_catalog(self, version: str, catalog_json: str) -> None
```

- **Returns:** `None`
- **Description:** Store a JSON snapshot of the pricing catalog.

#### `get_pricing_catalog`

```python
def get_pricing_catalog(self, version: str) -> Optional[dict[str, Any]]
```

- **Returns:** `Optional[dict[str, Any]]`
- **Description:** Retrieve a stored pricing catalog snapshot by version.

#### `prune`

```python
def prune(self, days: int = 90) -> int
```

- **Returns:** `int`
- **Description:** Delete events (and associated data) older than *days* days.

#### `backfill_baseline_costs`

```python
def backfill_baseline_costs(self, dry_run: bool = False) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Populate ``baseline_input_tokens`` and ``baseline_cost`` for

#### `stats`

```python
def stats(self) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Return row counts for each telemetry table.

#### `get_summary`

```python
def get_summary(self, provider: Optional[str] = None, model: Optional[str] = None, agent_id: Optional[str] = None) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return aggregate summary statistics.

#### `get_timeseries`

```python
def get_timeseries(self, metric: str = 'cost', interval: str = 'hour', provider: Optional[str] = None, model: Optional[str] = None, agent_id: Optional[str] = None, since_ts: Optional[float] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return time-bucketed metric data for charting.

#### `get_unique_models`

```python
def get_unique_models(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return list of unique model identifiers seen.

#### `get_unique_providers`

```python
def get_unique_providers(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return list of unique provider names seen.

#### `get_unique_agents`

```python
def get_unique_agents(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return list of unique agent identifiers seen.

#### `export_trace`

```python
def export_trace(self, trace_id: str) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Export a complete trace bundle as JSON-serializable dict.

#### `compute_rollups`

```python
def compute_rollups(self) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Recompute all daily rollup tables from raw data.

#### `get_rollup_timeseries`

```python
def get_rollup_timeseries(self, entity_type: str = 'model', metric: str = 'cost', since_date: Optional[str] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Query rollup tables for fast timeseries data.

#### `insert_savings_attribution`

```python
def insert_savings_attribution(self, row: dict[str, Any]) -> None
```

- **Returns:** `None`
- **Description:** Insert one savings attribution record.

#### `batch_insert_savings_attributions`

```python
def batch_insert_savings_attributions(self, rows: list[dict[str, Any]]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert savings attribution records.

#### `query_savings_by_source`

```python
def query_savings_by_source(self, *, days: int = 7) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Aggregate savings tokens and costs grouped by source.

#### `insert_cache_miss`

```python
def insert_cache_miss(self, row: dict[str, Any]) -> None
```

- **Returns:** `None`
- **Description:** Insert one cache miss reason record.

#### `query_cache_miss_summary`

```python
def query_cache_miss_summary(self, *, days: int = 7) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Aggregate cache miss counts grouped by reason.

### `tokenpak.telemetry.storage_base.TelemetryDBBase`

**Bases:** object

SQLite-backed telemetry store.

Parameters
----------
path:
    Path to the SQLite database file.  Pass ``":memory:"`` for an
    in-memory database (useful for testing).

#### `__init__`

```python
def __init__(self, path: Union[str, Path] = ':memory:') -> None
```

- **Returns:** `None`

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`
- **Description:** Close every connection this store has opened (all threads).

### `tokenpak.telemetry.storage_events.EventsMixin`

**Bases:** object

Mixin providing TelemetryEvent insert, insert_trace, and query methods.

#### `insert_event`

```python
def insert_event(self, event: TelemetryEvent) -> None
```

- **Returns:** `None`
- **Description:** Persist a single :class:`TelemetryEvent`.

#### `insert_events`

```python
def insert_events(self, events: list[TelemetryEvent]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert a list of :class:`TelemetryEvent` records.

#### `insert_trace`

```python
def insert_trace(self, event: TelemetryEvent, usage: Optional[Usage] = None, cost: Optional[Cost] = None, segments: Optional[list[Segment]] = None) -> None
```

- **Returns:** `None`
- **Description:** Insert all data for a single trace in one call.

#### `get_trace`

```python
def get_trace(self, trace_id: str) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return all stored data for *trace_id* as a plain dict.

#### `get_trace_events`

```python
def get_trace_events(self, trace_id: str) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return all pipeline events for a trace in chronological order.

#### `list_traces`

```python
def list_traces(self, limit: int = 100, offset: int = 0, provider: Optional[str] = None, model: Optional[str] = None, agent_id: Optional[str] = None, since_ts: Optional[float] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return a paginated list of trace event summaries.

### `tokenpak.telemetry.storage_rollups.RollupsMixin`

**Bases:** object

Mixin providing rollup computation, summary, and timeseries query methods.

#### `get_summary`

```python
def get_summary(self, provider: Optional[str] = None, model: Optional[str] = None, agent_id: Optional[str] = None) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Return aggregate summary statistics.

#### `get_timeseries`

```python
def get_timeseries(self, metric: str = 'cost', interval: str = 'hour', provider: Optional[str] = None, model: Optional[str] = None, agent_id: Optional[str] = None, since_ts: Optional[float] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return time-bucketed metric data for charting.

#### `compute_rollups`

```python
def compute_rollups(self) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Recompute all daily rollup tables from raw data.

#### `get_rollup_timeseries`

```python
def get_rollup_timeseries(self, entity_type: str = 'model', metric: str = 'cost', since_date: Optional[str] = None) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Query rollup tables for fast timeseries data.

### `tokenpak.telemetry.storage_segments.SegmentsMixin`

**Bases:** object

Mixin providing Segment insert and query methods.

#### `insert_segment`

```python
def insert_segment(self, segment: Segment) -> None
```

- **Returns:** `None`
- **Description:** Persist a single :class:`Segment` record.

#### `insert_segments`

```python
def insert_segments(self, segments: list[Segment]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert a list of :class:`Segment` records.

#### `get_segments`

```python
def get_segments(self, trace_id: str) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`
- **Description:** Return all segment rows for *trace_id*, ordered by ``ord``.

### `tokenpak.telemetry.storage_usage.UsageMixin`

**Bases:** object

Mixin providing Usage/Cost CRUD, pricing catalog, prune, and stats methods.

#### `insert_usage`

```python
def insert_usage(self, usage: Usage) -> None
```

- **Returns:** `None`
- **Description:** Persist a single :class:`Usage` record.

#### `insert_usages`

```python
def insert_usages(self, usages: list[Usage]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert a list of :class:`Usage` records.

#### `insert_cost`

```python
def insert_cost(self, cost: Cost) -> None
```

- **Returns:** `None`
- **Description:** Persist a single :class:`Cost` record.

#### `insert_costs`

```python
def insert_costs(self, costs: list[Cost]) -> None
```

- **Returns:** `None`
- **Description:** Batch-insert a list of :class:`Cost` records.

#### `upsert_pricing_catalog`

```python
def upsert_pricing_catalog(self, version: str, catalog_json: str) -> None
```

- **Returns:** `None`
- **Description:** Store a JSON snapshot of the pricing catalog.

#### `get_pricing_catalog`

```python
def get_pricing_catalog(self, version: str) -> Optional[dict[str, Any]]
```

- **Returns:** `Optional[dict[str, Any]]`
- **Description:** Retrieve a stored pricing catalog snapshot by version.

#### `prune`

```python
def prune(self, days: int = 90) -> int
```

- **Returns:** `int`
- **Description:** Delete events (and associated data) older than *days* days.

#### `backfill_baseline_costs`

```python
def backfill_baseline_costs(self, dry_run: bool = False) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Populate ``baseline_input_tokens`` and ``baseline_cost`` for

#### `stats`

```python
def stats(self) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Return row counts for each telemetry table.

#### `get_unique_models`

```python
def get_unique_models(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return list of unique model identifiers seen.

#### `get_unique_providers`

```python
def get_unique_providers(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return list of unique provider names seen.

#### `get_unique_agents`

```python
def get_unique_agents(self) -> list[str]
```

- **Returns:** `list[str]`
- **Description:** Return list of unique agent identifiers seen.

#### `export_trace`

```python
def export_trace(self, trace_id: str) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Export a complete trace bundle as JSON-serializable dict.

### `tokenpak.telemetry.tokens.CacheStats`

**Bases:** Protocol

Public fields exposed by ``functools.lru_cache`` statistics.

#### `hits`

```python
def hits(self) -> int
```

- **Returns:** `int`

#### `misses`

```python
def misses(self) -> int
```

- **Returns:** `int`

#### `maxsize`

```python
def maxsize(self) -> int | None
```

- **Returns:** `int | None`

#### `currsize`

```python
def currsize(self) -> int
```

- **Returns:** `int`

### `tokenpak.vault.ast_parser.ASTParser`

**Bases:** object

Language-aware parser that extracts structural information from code files.

Supports Python natively via the stdlib ``ast`` module.
Falls back to regex-based extraction for JS/TS and other languages.

Usage::

    parser = ASTParser()
    nodes = parser.parse_file("mymodule.py", source_code)
    for node in nodes:
        print(node.kind, node.name, node.signature)

#### `parse_file`

```python
def parse_file(self, path: str, content: str) -> list[ParsedNode]
```

- **Returns:** `list[ParsedNode]`
- **Description:** Parse a source file and return a list of structural nodes.

### `tokenpak.vault.backend_protocol.RetrievalBackend`

**Bases:** Protocol

Protocol for vault retrieval backends.

Any class implementing these methods can serve as a vault search backend.
TokenPak ships with two built-in backends:

- ``json_blocks`` (default): In-memory BM25 over index.json + blocks/\*.txt
- ``sqlite``: SQLite-backed BM25 with incremental updates

Users can implement this protocol for custom backends:

- Vector databases (pgvector, Qdrant, Weaviate, Pinecone)
- Full-text search engines (Elasticsearch, Meilisearch, SQLite FTS5)
- Hybrid/custom retrieval pipelines

Minimal implementation requires only ``available``, ``maybe_reload()``, and
``search()``. Extend :class:`RetrievalBackendBase` to get a default
``compile_injection()`` for free.

#### `available`

```python
def available(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if the backend is loaded and ready to search.

#### `maybe_reload`

```python
def maybe_reload(self) -> None
```

- **Returns:** `None`
- **Description:** Check if the underlying data has changed and reload if needed.

#### `search`

```python
def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> List[RetrievalResult]
```

- **Returns:** `List[RetrievalResult]`
- **Description:** Search for relevant blocks.

#### `compile_injection`

```python
def compile_injection(self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0) -> Tuple[str, int, List[str]]
```

- **Returns:** `Tuple[str, int, List[str]]`
- **Description:** Search and compile injection text within a token budget.

### `tokenpak.vault.backend_protocol.RetrievalBackendBase`

**Bases:** object

Base class providing default ``compile_injection()`` from ``search()``.

Subclass this and implement ``search()``, ``available``, and ``maybe_reload()``.
``compile_injection()`` is provided for free — it calls ``search()`` and
formats the results within the token budget.

This lowers the bar for custom backends to ~20 lines of user code.

Example::

    from tokenpak.vault.backend_protocol import RetrievalBackendBase

    class MyBackend(RetrievalBackendBase):
        def __init__(self, vault_path: str):
            self._ready = True

        @property
        def available(self) -> bool:
            return self._ready

        def maybe_reload(self) -> None:
            pass  # my backend auto-refreshes

        def search(self, query, top_k=5, min_score=2.0):
            # ... your search logic here ...
            return [(block_dict, score), ...]

#### `available`

```python
def available(self) -> bool
```

- **Returns:** `bool`

#### `maybe_reload`

```python
def maybe_reload(self) -> None
```

- **Returns:** `None`

#### `search`

```python
def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> List[RetrievalResult]
```

- **Returns:** `List[RetrievalResult]`

#### `compile_injection`

```python
def compile_injection(self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0) -> Tuple[str, int, List[str]]
```

- **Returns:** `Tuple[str, int, List[str]]`
- **Description:** Search and compile injection text within token budget.

### `tokenpak.vault.backend_protocol.SemanticScorer`

**Bases:** Protocol

Protocol for semantic scoring backends (Augment mode).

Called after BM25 retrieval with candidate block IDs. Returns similarity
scores that fuse with BM25 via the multi-signal scorer.

In Augment mode:

1. BM25 runs and returns candidate blocks (as today)
2. SemanticScorer receives the query + candidate block IDs
3. It returns similarity scores for those candidates
4. ``score_and_sort()`` fuses both signals using existing weights

Example implementation::

    class PgVectorScorer:
        def __init__(self):
            self.conn = psycopg2.connect(os.environ["DATABASE_URL"])

        def score(self, query: str, block_ids: list[str]) -> dict[str, float]:
            embedding = self._embed(query)
            # Query pgvector for similarity scores for these specific block_ids
            ...
            return {bid: similarity for bid, similarity in results}

#### `score`

```python
def score(self, query: str, block_ids: List[str]) -> Dict[str, float]
```

- **Returns:** `Dict[str, float]`
- **Description:** Return semantic similarity scores for given blocks.

### `tokenpak.vault.blocks.BlockRecord`

**Bases:** object

A compressed content block stored on disk.

#### `compression_ratio`

```python
def compression_ratio(self) -> float
```

- **Returns:** `float`

#### `tokens_saved`

```python
def tokens_saved(self) -> int
```

- **Returns:** `int`

#### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> 'BlockRecord'
```

- **Returns:** `'BlockRecord'`

### `tokenpak.vault.blocks.BlockStore`

**Bases:** object

JSON-backed block storage for compressed file content.

Each collection is stored as a single JSON file (suitable for small-medium
vaults). For large vaults, Phase 1 introduces SQLite persistence.

Usage::

    store = BlockStore("~/.tokenpak/blocks.json")
    store.save(record)
    block = store.get("path/to/file.py#abc123")
    results = store.search("token compression", top_k=5)
    store.flush()

#### `__init__`

```python
def __init__(self, store_path: str = ':memory:') -> None
```

- **Returns:** `None`

#### `save`

```python
def save(self, record: BlockRecord) -> None
```

- **Returns:** `None`
- **Description:** Upsert a block record.

#### `get`

```python
def get(self, block_id: str) -> Optional[BlockRecord]
```

- **Returns:** `Optional[BlockRecord]`

#### `get_by_path`

```python
def get_by_path(self, path: str) -> list[BlockRecord]
```

- **Returns:** `list[BlockRecord]`

#### `delete`

```python
def delete(self, block_id: str) -> bool
```

- **Returns:** `bool`

#### `all`

```python
def all(self) -> list[BlockRecord]
```

- **Returns:** `list[BlockRecord]`

#### `search`

```python
def search(self, query: str, top_k: int = 10) -> list[BlockRecord]
```

- **Returns:** `list[BlockRecord]`
- **Description:** Naive keyword search over compressed content. Phase 1 adds embeddings.

#### `stats`

```python
def stats(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `flush`

```python
def flush(self) -> None
```

- **Returns:** `None`
- **Description:** Write blocks to the JSON store file (atomic; see vault/_atomic.py).

### `tokenpak.vault.blocks.SliceStore`

**Bases:** object

In-memory + optional JSON persistence for :class:`~tokenpak.vault.slicer.SliceRecord`.

Keeps an index keyed by ``slice_id`` and a secondary index from
``parent_block_id`` → list of slice IDs for efficient provenance lookup.

Usage::

    store = SliceStore(":memory:")
    store.save(slice_record)
    children = store.get_by_parent("path/to/doc.md#abc123")
    results = store.search("Script 1")

#### `__init__`

```python
def __init__(self, store_path: str = ':memory:') -> None
```

- **Returns:** `None`

#### `save`

```python
def save(self, record: SliceRecord) -> None
```

- **Returns:** `None`
- **Description:** Upsert a slice record.

#### `get`

```python
def get(self, slice_id: str) -> Optional[SliceRecord]
```

- **Returns:** `Optional[SliceRecord]`

#### `get_by_parent`

```python
def get_by_parent(self, parent_block_id: str) -> list[SliceRecord]
```

- **Returns:** `list[SliceRecord]`
- **Description:** Return all slices for a given parent block ID, ordered by slice_index.

#### `get_by_path`

```python
def get_by_path(self, path: str) -> list[SliceRecord]
```

- **Returns:** `list[SliceRecord]`
- **Description:** Return all slices whose parent_path matches.

#### `delete_by_parent`

```python
def delete_by_parent(self, parent_block_id: str) -> int
```

- **Returns:** `int`
- **Description:** Remove all slices for a parent block. Returns count removed.

#### `all`

```python
def all(self) -> list[SliceRecord]
```

- **Returns:** `list[SliceRecord]`

#### `search`

```python
def search(self, query: str, top_k: int = 10) -> list[SliceRecord]
```

- **Returns:** `list[SliceRecord]`
- **Description:** Keyword search over slice content (multi-term, case-insensitive TF scoring).

#### `stats`

```python
def stats(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`

#### `flush`

```python
def flush(self) -> None
```

- **Returns:** `None`
- **Description:** Write slices to the JSON store file (atomic; see vault/_atomic.py).

### `tokenpak.vault.config.VaultConfig`

**Bases:** object

Top-level ``vault.yaml`` document.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

#### `find`

```python
def find(self, path: str) -> Optional[VaultPathEntry]
```

- **Returns:** `Optional[VaultPathEntry]`
- **Description:** Return the registered entry for ``path`` (normalized), or None.

### `tokenpak.vault.config.VaultPathEntry`

**Bases:** object

One registered directory in ``vault.yaml``.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.vault.doctor_check.PathFinding`

**Bases:** object

One per-path result emitted by :func:`check_vault_paths`.

``status`` is one of ``ok``, ``stale``, ``missing``, ``never``, ``corrupt``,
``failed``. ``severity`` is the doctor verdict (``pass`` / ``warn``) the
caller should record.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.vault.indexer.VaultIndexer`

**Bases:** object

Index a directory of code and doc files into compressed block storage.

Usage::

    indexer = VaultIndexer()
    results = indexer.index_directory("~/projects/myapp")
    print(f"Indexed {results['files_indexed']} files")

    # Search indexed content
    blocks = indexer.search("authentication middleware")

#### `__init__`

```python
def __init__(self, block_store: Optional[BlockStore] = None, symbol_table: Optional[SymbolTable] = None, slice_store: Optional[SliceStore] = None) -> None
```

- **Returns:** `None`

#### `index_file`

```python
def index_file(self, path: str, content: Optional[str] = None) -> Optional[BlockRecord]
```

- **Returns:** `Optional[BlockRecord]`
- **Description:** Index a single file. Reads from disk if content not provided.

#### `index_directory`

```python
def index_directory(self, root: str, on_progress: Optional[Callable[[str], None]] = None) -> dict[str, int]
```

- **Returns:** `dict[str, int]`
- **Description:** Walk and index all supported files under root.

#### `search`

```python
def search(self, query: str, top_k: int = 10) -> list[BlockRecord]
```

- **Returns:** `list[BlockRecord]`
- **Description:** Search indexed blocks by keyword.

#### `search_slices`

```python
def search_slices(self, query: str, top_k: int = 10) -> list[SliceRecord]
```

- **Returns:** `list[SliceRecord]`
- **Description:** Search semantic sub-blocks (slices) by keyword.

#### `get_slices_for_file`

```python
def get_slices_for_file(self, path: str) -> list[SliceRecord]
```

- **Returns:** `list[SliceRecord]`
- **Description:** Return all slices for the given source file path, in document order.

#### `lookup_symbol`

```python
def lookup_symbol(self, name: str) -> list[Symbol]
```

- **Returns:** `list[Symbol]`
- **Description:** Look up a symbol by exact name.

#### `stats`

```python
def stats(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return indexer stats.

#### `stats_by_type`

```python
def stats_by_type(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return indexed file count broken down by file type and extension.

### `tokenpak.vault.ingest.claim_indexer.ClaimEvidence`

**Bases:** object

A claim linked with supporting evidence, metrics, and citations.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`
- **Description:** Convert to dictionary for JSON serialization.

### `tokenpak.vault.ingest.cross_doc.AgreementMap`

**Bases:** object

Per-field consensus / disagreement map across N documents.

Each field maps to:
  - ``agreement``: fields where all docs agree (same value)
  - ``partial``:   fields where some docs agree
  - ``conflict``:  fields where all docs disagree

#### `agreement_ratio`

```python
def agreement_ratio(self) -> float
```

- **Returns:** `float`

#### `status`

```python
def status(self) -> str
```

- **Returns:** `str`

### `tokenpak.vault.ingest.cross_doc.ComparisonReport`

**Bases:** object

Unified output from a cross-document comparison run.

#### `summary`

```python
def summary(self) -> str
```

- **Returns:** `str`
- **Description:** Human-readable summary.

### `tokenpak.vault.ingest.cross_doc.CrossDocAnalyzer`

**Bases:** object

Normalize and compare N documents via compact DocCards.

Usage::

    analyzer = CrossDocAnalyzer()

    # Option A: pass raw texts
    docs = [
        {"source": "paper_a.pdf", "text": "..."},
        {"source": "paper_b.pdf", "text": "..."},
    ]
    cards = analyzer.normalize(docs)
    report = analyzer.compare(cards, mode="side_by_side")
    print(report.summary())

    # Option B: pass pre-built DocCards
    report = analyzer.compare(my_cards, mode="conflict")

#### `__init__`

```python
def __init__(self, converter: Optional[SchemaConverter] = None) -> Any
```

- **Returns:** `Any`

#### `normalize`

```python
def normalize(self, docs: Sequence[Dict[str, Any]]) -> List[DocCard]
```

- **Returns:** `List[DocCard]`
- **Description:** Normalize a list of raw document dicts into DocCards.

#### `compare`

```python
def compare(self, cards: Sequence[DocCard], mode: str = 'side_by_side') -> ComparisonReport
```

- **Returns:** `ComparisonReport`
- **Description:** Compare N DocCards using the given mode.

### `tokenpak.vault.ingest.cross_doc.DocCard`

**Bases:** object

Compact research card: normalized representation of a single document.

10 papers → 10 DocCards instead of 10 long texts.
Each card is a structured, token-efficient summary.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `token_estimate`

```python
def token_estimate(self) -> int
```

- **Returns:** `int`
- **Description:** Rough token estimate (≈4 chars/token).

### `tokenpak.vault.ingest.cross_doc.EvidenceMatrix`

**Bases:** object

Document × claim evidence matrix.

Rows = documents, columns = claims (from all findings).
Cell = True if the doc supports the claim, False if it contradicts, None if silent.

#### `to_table`

```python
def to_table(self) -> str
```

- **Returns:** `str`
- **Description:** Render as ASCII table.

### `tokenpak.vault.ingest.cross_doc.MetricTable`

**Bases:** object

Numeric metric comparison table across N documents.

#### `to_table`

```python
def to_table(self) -> str
```

- **Returns:** `str`
- **Description:** Render as ASCII table.

#### `divergence`

```python
def divergence(self) -> Dict[str, float]
```

- **Returns:** `Dict[str, float]`
- **Description:** For numeric metrics, compute coefficient of variation (std/mean).

### `tokenpak.vault.ingest.cross_doc.SchemaConverter`

**Bases:** object

Convert raw document text into a compact DocCard.

Designed to be reusable across document types: research papers,
technical reports, meeting notes, blog posts.

#### `__init__`

```python
def __init__(self, max_abstract_chars: int = 300, max_findings: int = 5, max_methods: int = 5, max_conclusions: int = 5, max_keywords: int = 10) -> Any
```

- **Returns:** `Any`

#### `convert`

```python
def convert(self, text: str, source: str = 'unknown', metadata: Optional[Dict[str, Any]] = None) -> DocCard
```

- **Returns:** `DocCard`
- **Description:** Normalize raw text into a DocCard.

### `tokenpak.vault.ingest.document_parser.DocumentParser`

**Bases:** object

Parse prose documents into :class:`DocumentStructure`.

Supports markdown, HTML, and plain text. Format is auto-detected
when ``fmt`` is ``"auto"`` (default).

Example::

    parser = DocumentParser()
    doc = parser.parse(content, fmt="markdown")
    print(doc.heading_tree)
    for sec in doc.sections:
        print(sec.heading, sec.section_type)

#### `parse`

```python
def parse(self, text: str, fmt: str = 'auto') -> DocumentStructure
```

- **Returns:** `DocumentStructure`
- **Description:** Parse *text* and return a :class:`DocumentStructure`.

### `tokenpak.vault.ingest.document_parser.DocumentSection`

**Bases:** object

A single section of a document.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.vault.ingest.document_parser.DocumentStructure`

**Bases:** object

Full structural representation of a parsed document.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.vault.ingest.table_extractor.NormalizedTable`

**Bases:** object

Normalized table extracted from a document section.

Serves structured row objects instead of raw table text, enabling
query-targeted row/column filtering and token-efficient summaries.

#### `summary`

```python
def summary(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Compact summary: headers + row_count + first data row sample.

#### `filter_rows`

```python
def filter_rows(self, query: str, *, max_rows: int = 20) -> 'NormalizedTable'
```

- **Returns:** `'NormalizedTable'`
- **Description:** Return a new NormalizedTable with only query-relevant rows.

#### `filter_columns`

```python
def filter_columns(self, columns: Sequence[str]) -> 'NormalizedTable'
```

- **Returns:** `'NormalizedTable'`
- **Description:** Return a new NormalizedTable with only the specified columns.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Full serializable dict.

### `tokenpak.vault.ingest.table_extractor.TableExtractor`

**Bases:** object

Extract and normalize tables from document text.

Detects:
* Markdown pipe-delimited tables
* HTML ``<table>`` elements
* Plain-text aligned tables (column-aligned whitespace heuristic)

Example::

    extractor = TableExtractor()
    tables = extractor.extract(text, source_section="Results")

#### `extract`

```python
def extract(self, text: str, *, source_section: str = '') -> list[NormalizedTable]
```

- **Returns:** `list[NormalizedTable]`
- **Description:** Extract all tables from *text*.

### `tokenpak.vault.retrieval.base.FusedResult`

**Bases:** object

Result after RRF fusion across multiple retrievers.

#### `content`

```python
def content(self) -> str
```

- **Returns:** `str`

#### `metadata`

```python
def metadata(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.vault.retrieval.base.HybridSearchConfig`

**Bases:** object

Configuration for the hybrid retriever.

#### `from_env`

```python
def from_env(cls) -> 'HybridSearchConfig'
```

- **Returns:** `'HybridSearchConfig'`
- **Description:** Load configuration from environment variables.

### `tokenpak.vault.retrieval.base.Retriever`

**Bases:** ABC

Abstract base class for all retrievers.

#### `retriever_type`

```python
def retriever_type(self) -> RetrieverType
```

- **Returns:** `RetrieverType`

#### `search`

```python
async def search(self, query: RetrievalQuery) -> List[RetrievalResult]
```

- **Returns:** `List[RetrievalResult]`
- **Description:** Search and return ranked results.

#### `index`

```python
async def index(self, documents: List[Dict[str, Any]]) -> int
```

- **Returns:** `int`
- **Description:** Index documents. Returns count of indexed documents.

#### `is_available`

```python
def is_available(self) -> bool
```

- **Returns:** `bool`
- **Description:** Whether this retriever is ready to serve queries.

### `tokenpak.vault.retrieval.bm25.BM25Index`

**Bases:** object

In-memory BM25 index over a document corpus.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `build`

```python
def build(self, documents: List[Dict[str, Any]]) -> int
```

- **Returns:** `int`
- **Description:** Build index from document list. Each doc needs 'id' and 'content' keys.

#### `search`

```python
def search(self, query: str, top_k: int = 10, min_score: float = 0.0) -> List[RetrievalResult]
```

- **Returns:** `List[RetrievalResult]`

#### `doc_count`

```python
def doc_count(self) -> int
```

- **Returns:** `int`

### `tokenpak.vault.retrieval.bm25.BM25Retriever`

**Bases:** Retriever

BM25 retriever. Can be used standalone with in-memory documents,
or backed by a vault index directory.

#### `__init__`

```python
def __init__(self, vault_index_path: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `retriever_type`

```python
def retriever_type(self) -> RetrieverType
```

- **Returns:** `RetrieverType`

#### `is_available`

```python
def is_available(self) -> bool
```

- **Returns:** `bool`

#### `index`

```python
async def index(self, documents: List[Dict[str, Any]]) -> int
```

- **Returns:** `int`
- **Description:** Index a list of documents in-memory.

#### `search`

```python
async def search(self, query: RetrievalQuery) -> List[RetrievalResult]
```

- **Returns:** `List[RetrievalResult]`

### `tokenpak.vault.retrieval.fusion.WeightedFusion`

**Bases:** object

Stateful wrapper around rrf_fusion with stored configuration.

#### `__init__`

```python
def __init__(self, weights: Optional[Dict[str, float]] = None, k: int = 60, top_n: int = 20) -> None
```

- **Returns:** `None`

#### `fuse`

```python
def fuse(self, result_lists: Dict[str, List[RetrievalResult]]) -> List[FusedResult]
```

- **Returns:** `List[FusedResult]`
- **Description:** Run RRF fusion with stored config. Returns FusedResult list.

#### `fuse_simple`

```python
def fuse_simple(self, result_lists: Dict[str, List[RetrievalResult]]) -> List[Tuple[str, float, RetrievalResult]]
```

- **Returns:** `List[Tuple[str, float, RetrievalResult]]`
- **Description:** Run RRF fusion. Returns (doc_id, score, result) tuples.

### `tokenpak.vault.retrieval.hybrid.HybridRetriever`

**Bases:** object

Orchestrates BM25 + optional vector retrieval, fusing results via RRF.

Usage:
    config = HybridSearchConfig(vault_index_path="/path/to/.tokenpak")
    retriever = HybridRetriever(config)
    results = await retriever.search("my query", top_k=5)

#### `__init__`

```python
def __init__(self, config: Optional[HybridSearchConfig] = None) -> None
```

- **Returns:** `None`

#### `is_available`

```python
def is_available(self) -> bool
```

- **Returns:** `bool`
- **Description:** Returns True if at least BM25 is ready.

#### `vector_available`

```python
def vector_available(self) -> bool
```

- **Returns:** `bool`

#### `index`

```python
async def index(self, documents: list[dict[str, object]]) -> int
```

- **Returns:** `int`
- **Description:** Index documents into both BM25 and vector retrievers.

#### `search`

```python
async def search(self, query_text: str, top_k: int = 5) -> List[FusedResult]
```

- **Returns:** `List[FusedResult]`
- **Description:** Run enabled retrievers in parallel, fuse with RRF, return top_k FusedResults.

### `tokenpak.vault.retrieval.vault_index.VaultIndex`

**Bases:** object

Read-only BM25-searchable index loaded from .tokenpak/index.json + blocks/.
Reloads periodically to pick up git-pulled changes.

#### `__init__`

```python
def __init__(self, tokenpak_dir: str) -> Any
```

- **Returns:** `Any`

#### `available`

```python
def available(self) -> bool
```

- **Returns:** `bool`

#### `is_ready`

```python
def is_ready(self) -> bool
```

- **Returns:** `bool`
- **Description:** Returns True once the vault index has completed its initial load.

#### `maybe_reload`

```python
def maybe_reload(self) -> None
```

- **Returns:** `None`
- **Description:** Reload if index file changed or enough time passed.

#### `cache_stats`

```python
def cache_stats(self) -> dict[str, int | float]
```

- **Returns:** `dict[str, int | float]`
- **Description:** Return current cache statistics (thread-safe snapshot).

#### `search`

```python
def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> List[Tuple[VaultBlock, float]]
```

- **Returns:** `List[Tuple[VaultBlock, float]]`
- **Description:** BM25 search across vault blocks. Returns [(block_dict, score), ...].

#### `compile_injection`

```python
def compile_injection(self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0) -> Tuple[str, int, List[str]]
```

- **Returns:** `Tuple[str, int, List[str]]`
- **Description:** Search vault and compile injection text within budget.

### `tokenpak.vault.retrieval.vector_local.LocalVectorRetriever`

**Bases:** Retriever

Vector similarity retriever backed by sentence-transformers embeddings.
Falls back gracefully if sentence-transformers or numpy is unavailable.

Storage layout (when index_path is set):
    <index_path>/embeddings.npy   — float32 numpy array (N, dim)
    <index_path>/doc_ids.txt      — one doc_id per line
    <index_path>/contents.txt     — one content per line (newline-escaped)
    <index_path>/meta.json        — list of metadata dicts

#### `__init__`

```python
def __init__(self, model_name: str = 'all-MiniLM-L6-v2', index_path: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `retriever_type`

```python
def retriever_type(self) -> RetrieverType
```

- **Returns:** `RetrieverType`

#### `is_available`

```python
def is_available(self) -> bool
```

- **Returns:** `bool`

#### `index`

```python
async def index(self, documents: list[dict[str, object]]) -> int
```

- **Returns:** `int`
- **Description:** Embed and index documents. Each doc needs 'id' and 'content' keys.

#### `search`

```python
async def search(self, query: RetrievalQuery) -> List[RetrievalResult]
```

- **Returns:** `List[RetrievalResult]`

#### `save`

```python
def save(self) -> None
```

- **Returns:** `None`
- **Description:** Persist embeddings to disk.

#### `load`

```python
def load(self) -> bool
```

- **Returns:** `bool`
- **Description:** Load embeddings from disk. Returns True on success.

### `tokenpak.vault.semantic.loader.SemanticMapLoader`

**Bases:** object

Load, validate, and expose the semantic map.

Args:
    path: Path to semantic_map.yaml. Defaults to the bundled config.

#### `__init__`

```python
def __init__(self, path: Optional[str] = None) -> None
```

- **Returns:** `None`

#### `load`

```python
def load(self) -> SemanticMap
```

- **Returns:** `SemanticMap`
- **Description:** Load and validate the semantic map. Cached after first call.

#### `reload`

```python
def reload(self) -> SemanticMap
```

- **Returns:** `SemanticMap`
- **Description:** Force reload from disk.

### `tokenpak.vault.semantic.resolver.SemanticResolver`

**Bases:** object

Deterministic alias → canonical resolver for intents and entities.

Thread-safe (read-only after construction). Loads the semantic map
lazily on first use.

Args:
    loader: SemanticMapLoader instance. Defaults to bundled map.

#### `__init__`

```python
def __init__(self, loader: Optional[SemanticMapLoader] = None) -> None
```

- **Returns:** `None`

#### `map`

```python
def map(self) -> SemanticMap
```

- **Returns:** `SemanticMap`
- **Description:** Lazily loaded semantic map.

#### `resolve_intent`

```python
def resolve_intent(self, text: str) -> Optional[ResolveResult]
```

- **Returns:** `Optional[ResolveResult]`
- **Description:** Resolve user text to a canonical intent.

#### `resolve_entity`

```python
def resolve_entity(self, text: str) -> Optional[ResolveResult]
```

- **Returns:** `Optional[ResolveResult]`
- **Description:** Resolve user text to a canonical entity.

#### `resolve_all_entities`

```python
def resolve_all_entities(self, text: str) -> List[ResolveResult]
```

- **Returns:** `List[ResolveResult]`
- **Description:** Find all entity aliases in text and return their resolved canonicals.

#### `preprocess`

```python
def preprocess(self, text: str) -> Tuple[str, PreprocessResult]
```

- **Returns:** `Tuple[str, PreprocessResult]`
- **Description:** Normalize raw text by resolving aliases to canonical terms.

### `tokenpak.vault.semantic.term_card_resolver.TermCardResolver`

**Bases:** object

Placeholder for term card resolution.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `resolve`

```python
def resolve(self, term: str, context: Mapping[str, object] | None = None) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Placeholder resolver — returns empty.

### `tokenpak.vault.semantic.term_resolver.TermCardSnippet`

**Bases:** object

Short-form glossary snippet for injection.

#### `to_injection_format`

```python
def to_injection_format(self) -> str
```

- **Returns:** `str`
- **Description:** Format for inclusion in prompt context.

### `tokenpak.vault.semantic.term_resolver.TermResolver`

**Bases:** object

Deterministic resolver for glossary terms.

#### `__init__`

```python
def __init__(self, cards_path: Optional[Path] = None, config: Optional[TermResolverConfig] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Initialize resolver.

#### `load_cards`

```python
def load_cards(self, cards_path: Path) -> None
```

- **Returns:** `None`
- **Description:** Load and parse term_cards.json.

#### `resolve_terms`

```python
def resolve_terms(self, text: str) -> TermResolution
```

- **Returns:** `TermResolution`
- **Description:** Extract and resolve glossary terms from text.

### `tokenpak.vault.slicer.SliceRecord`

**Bases:** object

A single semantic sub-block sliced from a parent document.

Attributes:
    slice_id:         Stable unique identifier for this slice.
    parent_block_id:  Block ID of the parent file record.
    parent_path:      Source file path.
    slice_index:      0-based position in the slice list (stable ordering).
    heading:          The heading text that started this slice (empty string
                      for preamble / headingless content).
    content:          Raw text content of this slice.
    content_hash:     SHA-256 of content (hex, full).
    strategy:         Split strategy used (``heading`` | ``script`` | ``section``).
    metadata:         Arbitrary extra data (e.g. heading level, line number).

#### `tokens_hint`

```python
def tokens_hint(self) -> int
```

- **Returns:** `int`
- **Description:** Rough token estimate (4 chars ≈ 1 token).

### `tokenpak.vault.sources.claude_transcript.TranscriptBlock`

**Bases:** object

#### `render`

```python
def render(self) -> str
```

- **Returns:** `str`
- **Description:** Render the session as a deterministic, search-friendly text body.

### `tokenpak.vault.sqlite_backend.SQLiteRetrievalBackend`

**Bases:** object

SQLite-backed BM25 retrieval for proxy vault injection.

Implements the same public interface as ``VaultIndex``:
  - ``available: bool``
  - ``maybe_reload()``
  - ``search(query, top_k, min_score) -> [(block, score)]``
  - ``compile_injection(query, budget, top_k, min_score) -> (text, tokens, refs)``

Block count and token count are exposed for metrics parity.

#### `__init__`

```python
def __init__(self, tokenpak_dir: str) -> Any
```

- **Returns:** `Any`

#### `available`

```python
def available(self) -> bool
```

- **Returns:** `bool`

#### `block_count`

```python
def block_count(self) -> int
```

- **Returns:** `int`

#### `token_count`

```python
def token_count(self) -> int
```

- **Returns:** `int`

#### `maybe_reload`

```python
def maybe_reload(self) -> None
```

- **Returns:** `None`
- **Description:** Check if the vault index has changed and rebuild if necessary.

#### `search`

```python
def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> List[Tuple[VaultBlock, float]]
```

- **Returns:** `List[Tuple[VaultBlock, float]]`
- **Description:** BM25 search. Returns [(block_dict, score), ...] sorted deterministically.

#### `compile_injection`

```python
def compile_injection(self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0) -> Tuple[str, int, List[str]]
```

- **Returns:** `Tuple[str, int, List[str]]`
- **Description:** Search and compile injection text within token budget.

#### `blocks`

```python
def blocks(self) -> Dict[str, VaultBlock]
```

- **Returns:** `Dict[str, VaultBlock]`
- **Description:** Return all blocks as a dict for compatibility with VaultIndex callers.

### `tokenpak.vault.symbol_extraction.Symbol`

**Bases:** object

A named code symbol (function, class, constant, etc.).

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.vault.symbol_extraction.SymbolTable`

**Bases:** object

Build and query a symbol table from source files.

Usage::

    table = SymbolTable()
    table.index_file("mymodule.py", source_code)
    results = table.lookup("MyClass")
    all_syms = table.all_symbols()

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `index_file`

```python
def index_file(self, path: str, content: str) -> list[Symbol]
```

- **Returns:** `list[Symbol]`
- **Description:** Parse a file and add its symbols to the table. Returns new symbols.

#### `lookup`

```python
def lookup(self, name: str) -> list[Symbol]
```

- **Returns:** `list[Symbol]`
- **Description:** Find all symbols matching the given name (exact).

#### `search`

```python
def search(self, query: str) -> list[Symbol]
```

- **Returns:** `list[Symbol]`
- **Description:** Case-insensitive substring search across symbol names.

#### `all_symbols`

```python
def all_symbols(self, kind: Optional[str] = None) -> list[Symbol]
```

- **Returns:** `list[Symbol]`
- **Description:** Return all symbols, optionally filtered by kind.

#### `symbols_in_file`

```python
def symbols_in_file(self, path: str) -> list[Symbol]
```

- **Returns:** `list[Symbol]`
- **Description:** Return all symbols defined in a given file.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Remove all indexed symbols.

### `tokenpak.vault.vault_health.HealthCheckResult`

**Bases:** object

Result of a vault-health check.

#### `is_ok`

```python
def is_ok(self) -> bool
```

- **Returns:** `bool`

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.vault.vault_health.RepairResult`

**Bases:** object

Result of a vault-health repair operation.

#### `to_dict`

```python
def to_dict(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`

### `tokenpak.vault.vault_health.VaultHealth`

**Bases:** object

Vault index health monitor and rebuilder.

Parameters
----------
vault_dir : Path | str | None
    Root of the vault. Defaults to ~/vault.
stale_seconds : float
    Age in seconds after which the index is considered stale.

#### `__init__`

```python
def __init__(self, vault_dir: str | Path | None = None, stale_seconds: float = DEFAULT_STALE_SECONDS) -> None
```

- **Returns:** `None`

#### `check`

```python
def check(self) -> HealthCheckResult
```

- **Returns:** `HealthCheckResult`
- **Description:** Check vault index health. Returns OK / STALE / MISSING / CORRUPT.

#### `check_index_staleness`

```python
def check_index_staleness(self) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if index is stale, missing, or corrupt.

#### `get_status`

```python
def get_status(self) -> str
```

- **Returns:** `str`
- **Description:** Return status string: OK / STALE / MISSING / CORRUPT.

#### `rebuild_index`

```python
def rebuild_index(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Rebuild the vault index by walking vault_dir.

#### `repair`

```python
def repair(self) -> RepairResult
```

- **Returns:** `RepairResult`
- **Description:** Detect staleness and rebuild if needed.

### `tokenpak.vault.watcher.VaultWatcher`

**Bases:** object

Watch directories and trigger re-indexing on file changes.

Features:
- watchdog-based filesystem events (inotify/FSEvents)
- Debounced re-indexing: coalesces rapid bursts into one reindex
- Pattern filtering (ignore __pycache__, .git, etc.)
- Status / stats reporting
- Graceful Ctrl+C handling when blocking=True

#### `__init__`

```python
def __init__(self, config: WatcherConfig, on_change: Optional[Callable[[str], None]] = None) -> None
```

- **Returns:** `None`

#### `start`

```python
def start(self, blocking: bool = False) -> None
```

- **Returns:** `None`
- **Description:** Start watching. If blocking=True, run until Ctrl+C.

#### `stop`

```python
def stop(self) -> None
```

- **Returns:** `None`
- **Description:** Stop watching gracefully.

#### `is_running`

```python
def is_running(self) -> bool
```

- **Returns:** `bool`

#### `status`

```python
def status(self) -> dict[str, object]
```

- **Returns:** `dict[str, object]`
- **Description:** Return a status/stats dict.

### `tokenpak.vault.watcher.WatcherStats`

**Bases:** object

#### `uptime_seconds`

```python
def uptime_seconds(self) -> float
```

- **Returns:** `float`
