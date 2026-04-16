# TokenPak API Reference

> Auto-generated from source code docstrings and type hints via `scripts/generate_api_reference.py`.

**Public classes:** 313  
**Public methods:** 1275

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

### `tokenpak.adapters.anthropic.AnthropicAdapter`

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

### `tokenpak.adapters.base.TokenPakAdapter`

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
def __init__(self, base_url: str, api_key: str, timeout_s: float | None = None) -> None
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

### `tokenpak.adapters.base.TokenPakAdapterError`

**Bases:** Exception

Base exception for all TokenPak adapter errors.

#### `__init__`

```python
def __init__(self, message: str, status_code: int | None = None, raw: Any = None) -> None
```

- **Returns:** `None`

### `tokenpak.adapters.langchain.LangChainAdapter`

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

### `tokenpak.adapters.litellm.LiteLLMAdapter`

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

### `tokenpak.adapters.openai.OpenAIAdapter`

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

### `tokenpak.agent.adapters.base.BaseAdapter`

**Bases:** ABC

Abstract base class for TokenPak platform adapters.

Each concrete adapter must implement:
  - ``platform_name`` property
  - ``detect(request_headers, env)`` classmethod
  - ``get_config()`` instance method

#### `platform_name`

```python
def platform_name(self) -> str
```

- **Returns:** `str`
- **Description:** Human-readable platform identifier (e.g. "openclaw", "claude_cli").

#### `detect`

```python
def detect(cls, request_headers: Dict[str, str], env: Dict[str, str]) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if this adapter recognises the calling platform from the

#### `get_config`

```python
def get_config(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return a configuration dict consumed by the proxy pipeline.

### `tokenpak.agent.adapters.claude_cli.ClaudeCLIAdapter`

**Bases:** BaseAdapter

Adapter for requests originating from the Claude CLI tool.

#### `platform_name`

```python
def platform_name(self) -> str
```

- **Returns:** `str`

#### `detect`

```python
def detect(cls, request_headers: Dict[str, str], env: Dict[str, str]) -> bool
```

- **Returns:** `bool`

#### `get_config`

```python
def get_config(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.agent.adapters.generic.GenericAdapter`

**Bases:** BaseAdapter

Catch-all adapter for unrecognised or unknown platforms.

#### `platform_name`

```python
def platform_name(self) -> str
```

- **Returns:** `str`

#### `detect`

```python
def detect(cls, request_headers: Dict[str, str], env: Dict[str, str]) -> bool
```

- **Returns:** `bool`

#### `get_config`

```python
def get_config(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.agent.adapters.openclaw.OpenClawAdapter`

**Bases:** BaseAdapter

Adapter for requests originating from the OpenClaw agent runtime.

#### `platform_name`

```python
def platform_name(self) -> str
```

- **Returns:** `str`

#### `detect`

```python
def detect(cls, request_headers: Dict[str, str], env: Dict[str, str]) -> bool
```

- **Returns:** `bool`

#### `get_config`

```python
def get_config(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.agent.agentic.capabilities.AgentCapabilities`

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

### `tokenpak.agent.agentic.capabilities.CapabilityMatcher`

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

### `tokenpak.agent.agentic.capabilities.MatchResult`

**Bases:** object

Result of capability matching.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.agent.agentic.capabilities.TaskRequirements`

**Bases:** object

Requirements a task has for agent capabilities.

All fields are optional — unset means "any".

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.agent.agentic.handoff.ContextRef`

**Bases:** object

A single context reference passed in a handoff.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'ContextRef'
```

- **Returns:** `'ContextRef'`

### `tokenpak.agent.agentic.handoff.Handoff`

**Bases:** object

A context handoff record.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'Handoff'
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

### `tokenpak.agent.agentic.handoff.HandoffBlock`

**Bases:** object

A single content block inside a TokenPak.

Attributes:
    type:     Semantic type label, e.g. "memory", "evidence", "task_state".
    id:       Unique identifier within the pack.
    content:  Text content.
    metadata: Optional key/value metadata.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'HandoffBlock'
```

- **Returns:** `'HandoffBlock'`

### `tokenpak.agent.agentic.handoff.HandoffManager`

**Bases:** object

Manage context handoffs between agents.

#### `__init__`

```python
def __init__(self, handoff_dir: Optional[Path] = None) -> Any
```

- **Returns:** `Any`

#### `create_handoff`

```python
def create_handoff(self, from_agent: str, to_agent: str, context_refs: Optional[List[ContextRef]] = None, what_was_done: str = '', whats_next: str = '', relevant_files: Optional[List[str]] = None, ttl_hours: float = DEFAULT_TTL_HOURS, metadata: Optional[Dict[str, Any]] = None) -> Handoff
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

### `tokenpak.agent.agentic.handoff.HandoffWire`

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
def __init__(self, pack: TokenPak, from_agent: str, to_agent: str, summary: str = '', metadata: Optional[Dict[str, Any]] = None, handoff_id: Optional[str] = None) -> Any
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

### `tokenpak.agent.agentic.handoff.TokenPak`

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
def to_dict(self) -> dict
```

- **Returns:** `dict`

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'TokenPak'
```

- **Returns:** `'TokenPak'`

#### `to_prompt`

```python
def to_prompt(self) -> str
```

- **Returns:** `str`
- **Description:** Render all blocks as a structured prompt string.

### `tokenpak.agent.agentic.locks.FileLockManager`

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
def claim(self, path: str | Path, timeout_s: Optional[int] = None) -> dict
```

- **Returns:** `dict`
- **Description:** Claim a lock on *path*.

#### `release`

```python
def release(self, path: str | Path) -> bool
```

- **Returns:** `bool`
- **Description:** Release the lock on *path*.

#### `query`

```python
def query(self, path: str | Path) -> Optional[dict]
```

- **Returns:** `Optional[dict]`
- **Description:** Return lock info for *path*, or None if unlocked / expired.

#### `locks`

```python
def locks(self) -> list[dict]
```

- **Returns:** `list[dict]`
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
def renew(self, path: str | Path, timeout_s: Optional[int] = None) -> dict
```

- **Returns:** `dict`
- **Description:** Renew (extend) an existing lock held by this agent.

### `tokenpak.agent.agentic.locks.LockConflictError`

**Bases:** Exception

Raised when a file is already locked by another agent/process.

#### `__init__`

```python
def __init__(self, path: str, lock_info: dict) -> Any
```

- **Returns:** `Any`

### `tokenpak.agent.agentic.registry.AgentInfo`

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

### `tokenpak.agent.agentic.registry.AgentRegistry`

**Bases:** object

Persistent agent registry with heartbeat tracking.

Usage:
    registry = AgentRegistry()
    agent_id = registry.register("worker-1", "host-1", {"gpu": False, "memory_gb": 4})
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

### `tokenpak.agent.agentic.retry.ImmediateAlertError`

**Bases:** Exception

Raised by per-error routing when an auth/fatal error demands immediate alert.

#### `__init__`

```python
def __init__(self, status_code: str, original: Exception) -> Any
```

- **Returns:** `Any`

### `tokenpak.agent.agentic.retry.RetryAttempt`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.agentic.retry.RetryEngine`

**Bases:** object

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
def __init__(self, fn: Callable[[dict, dict], Any], context: dict, partial_state: Optional[dict] = None, state_dir: Optional[Path | str] = None, agent_id: Optional[str] = None, wait_seconds: Optional[list[float]] = None, per_error: Optional[dict[str, str]] = None, on_model_downgrade: Optional[Callable[[str], str]] = None, on_provider_switch: Optional[Callable[[str], str]] = None, on_handoff: Optional[Callable[[dict, dict], bool]] = None, on_human_alert: Optional[Callable[[dict], None]] = None) -> Any
```

- **Returns:** `Any`

#### `load_state`

```python
def load_state(cls, state_file: Path) -> dict
```

- **Returns:** `dict`
- **Description:** Reload persisted state for inspection or resume.

#### `run`

```python
def run(self) -> Any
```

- **Returns:** `Any`
- **Description:** Execute the task with full escalation.

### `tokenpak.agent.agentic.retry.RetryExhaustedError`

**Bases:** Exception

Raised when all 5 escalation levels have failed.

#### `__init__`

```python
def __init__(self, context: dict, partial_state: dict, attempts: list[dict]) -> Any
```

- **Returns:** `Any`

### `tokenpak.agent.agentic.workflow.WorkflowManager`

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

### `tokenpak.agent.agentic.workflow.WorkflowRecord`

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

### `tokenpak.agent.agentic.workflow.WorkflowStep`

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

### `tokenpak.agent.agentic.workflow_budget.BudgetEvent`

**Bases:** object

#### `is_warning`

```python
def is_warning(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.agent.agentic.workflow_budget.WorkflowBudget`

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

### `tokenpak.agent.auth.cooldown_manager.BackgroundCooldownClearer`

**Bases:** object

Asyncio background task that auto-clears expired cooldowns every N seconds.

Runs inside the proxy event loop (no extra threads needed).

Config key: auth.auto_clear_cooldowns (bool, default True)
Backoff: skips clear if any key has errorCount >= HIGH_ERROR_THRESHOLD.

#### `__init__`

```python
def __init__(self, interval: int = 60, manager: Optional[CooldownManager] = None, enabled: bool = True) -> Any
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

### `tokenpak.agent.auth.cooldown_manager.CooldownManager`

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
def __init__(self, cooldowns_file: Path = COOLDOWNS_FILE, auth_profiles_file: Path = AUTH_PROFILES_FILE) -> Any
```

- **Returns:** `Any`

#### `clear_expired`

```python
def clear_expired(self) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Clear cooldowns where cooldownUntil < now (and errorCount is low).

#### `clear_expired_from_profiles`

```python
def clear_expired_from_profiles(self) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Clear cooldownUntil fields from auth-profiles.json when expired.

#### `get_active_cooldowns`

```python
def get_active_cooldowns(self) -> Dict[str, float]
```

- **Returns:** `Dict[str, float]`
- **Description:** Return map of profile key → seconds remaining for active cooldowns.

#### `run_cycle`

```python
def run_cycle(self) -> int
```

- **Returns:** `int`
- **Description:** Run one clear cycle across both sources. Returns count of cleared entries.

### `tokenpak.agent.auth.oauth_manager.BackgroundOAuthRefresher`

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

### `tokenpak.agent.auth.oauth_manager.OAuthManager`

**Bases:** object

Check OAuth token expiry and refresh tokens proactively.

Reads/writes ~/.tokenpak/auth-profiles.json.
SECURITY: Never logs token values. Only logs metadata.

#### `__init__`

```python
def __init__(self, auth_profiles_file: Path = AUTH_PROFILES_FILE, refresh_window: int = REFRESH_WINDOW_SECONDS) -> Any
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

### `tokenpak.agent.cli.commands.doctor.Colors`

**Bases:** object

ANSI color codes + emoji markers.

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

### `tokenpak.agent.compression.directives.DirectiveApplier`

**Bases:** object

Apply compression directives to a messages list.

Apply compression directives to a messages list using the rule-based directive engine.

Parameters
----------
directives : list[dict], optional
    List of directive dicts.

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

### `tokenpak.agent.compression.directives.DirectiveCache`

**Bases:** object

In-process cache for server directive responses with 5-minute TTL.

#### `__init__`

```python
def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None
```

- **Returns:** `None`

#### `get`

```python
def get(self, raw: dict) -> 'dict | None'
```

- **Returns:** `'dict | None'`

#### `set`

```python
def set(self, raw: dict, parsed: dict) -> None
```

- **Returns:** `None`

#### `invalidate`

```python
def invalidate(self, raw: dict) -> bool
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

### `tokenpak.agent.compression.pipeline.CompressionPipeline`

**Bases:** object

Orchestrates the TokenPak compression pipeline.

Stages (all optional, enabled by default):
  1. dedup    — remove duplicate / near-duplicate message turns
  2. segment  — classify messages into typed Segment objects
  3. directives — apply compression directives

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
def __init__(self, enable_dedup: bool = True, enable_segmentation: bool = True, enable_directives: bool = True, trace_id: str = '') -> None
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
def run(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> PipelineResult
```

- **Returns:** `PipelineResult`
- **Description:** Run the full compression pipeline on *messages*.

### `tokenpak.agent.compression.pipeline.PipelineResult`

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

### `tokenpak.agent.compression.recipes.CompressionRecipe`

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
def operations(self) -> list[dict[str, Any]]
```

- **Returns:** `list[dict[str, Any]]`

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

### `tokenpak.agent.compression.recipes.CompressionRecipeEngine`

**Bases:** object

Loads and indexes OSS compression recipes from YAML files.

#### `__init__`

```python
def __init__(self) -> None
```

- **Returns:** `None`

#### `load_from_dir`

```python
def load_from_dir(self, path: str | Path | None = None) -> None
```

- **Returns:** `None`
- **Description:** Load all YAML recipe files from *path* (defaults to bundled OSS dir).

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

### `tokenpak.agent.compression.recipes.CompressionRuleEngine`

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

### `tokenpak.agent.compression.recipes.ContentSegment`

**Bases:** object

Lightweight segment that carries raw text and its classification.

#### `with_content`

```python
def with_content(self, new_content: str) -> 'ContentSegment'
```

- **Returns:** `'ContentSegment'`
- **Description:** Return a new ContentSegment with updated content (and recounted tokens).

### `tokenpak.agent.compression.recipes.Recipe`

**Bases:** object

#### `from_dict`

```python
def from_dict(cls, data: dict[str, Any], *, source: str) -> 'Recipe'
```

- **Returns:** `'Recipe'`

### `tokenpak.agent.compression.recipes.RecipeEngine`

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

### `tokenpak.agent.compression.slot_filler.SlotFiller`

**Bases:** object

Extracts slot values from raw text for a given intent.

Slot definitions are loaded from slot_definitions.yaml (co-located with
this module).  All extraction is regex/keyword based — no LLM.

Usage::

    filler = SlotFiller()
    result = filler.fill("summarize", "summarize the vault for last 7 days")

#### `__init__`

```python
def __init__(self, definitions: Optional[Dict[str, Any]] = None) -> None
```

- **Returns:** `None`

#### `fill`

```python
def fill(self, intent: str, text: str) -> FilledSlots
```

- **Returns:** `FilledSlots`

#### `definitions`

```python
def definitions(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

#### `known_intents`

```python
def known_intents(self) -> List[str]
```

- **Returns:** `List[str]`

### `tokenpak.agent.dashboard.export_api.ExportAPI`

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

### `tokenpak.agent.dashboard.export_csv.CSVExporter`

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

### `tokenpak.agent.dashboard.session_filter.FilterParams`

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

### `tokenpak.agent.dashboard.session_filter.SessionFilter`

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

### `tokenpak.agent.debug.logger.DebugLogger`

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

### `tokenpak.agent.debug.state.DebugState`

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
def status(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return a dict suitable for display.

### `tokenpak.agent.fingerprint.generator.Fingerprint`

**Bases:** object

Structural fingerprint of a prompt — no raw content.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.fingerprint.generator.FingerprintGenerator`

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
def generate_from_messages(self, messages: list[dict]) -> Fingerprint
```

- **Returns:** `Fingerprint`
- **Description:** Generate a fingerprint from an OpenAI-style messages list.

### `tokenpak.agent.fingerprint.sync.Directive`

**Bases:** object

A recipe/strategy directive received from the intelligence server.

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'Directive'
```

- **Returns:** `'Directive'`

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.fingerprint.sync.FingerprintSync`

**Bases:** object

Syncs fingerprints to the intelligence server and caches returned directives.

Syncs fingerprints and caches returned directives. Falls back to cached directives when offline.

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

### `tokenpak.agent.fingerprint.sync.SyncResult`

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

### `tokenpak.agent.license.keys.LicensePayload`

**Bases:** object

The decoded payload embedded in a license.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'LicensePayload'
```

- **Returns:** `'LicensePayload'`

### `tokenpak.agent.license.store.CachedLicense`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'CachedLicense'
```

- **Returns:** `'CachedLicense'`

#### `within_grace_period`

```python
def within_grace_period(self) -> bool
```

- **Returns:** `bool`
- **Description:** True if last online validation was within GRACE_PERIOD_DAYS.

#### `grace_expires_at`

```python
def grace_expires_at(self) -> datetime
```

- **Returns:** `datetime`
- **Description:** Absolute datetime when offline grace expires.

### `tokenpak.agent.license.store.LicenseStore`

**Bases:** object

Persist and retrieve cached license data.

Default storage: ~/.config/tokenpak/license_cache.json
Override via TOKENPAK_CONFIG_DIR env var or store_dir argument.

#### `__init__`

```python
def __init__(self, store_dir: Optional[Path] = None) -> Any
```

- **Returns:** `Any`

#### `save`

```python
def save(self, token: str, tier: str, expires_at: Optional[str] = None) -> CachedLicense
```

- **Returns:** `CachedLicense`
- **Description:** Persist a successfully validated license.

#### `touch`

```python
def touch(self) -> None
```

- **Returns:** `None`
- **Description:** Update last_validated timestamp (called after each successful online check).

#### `load`

```python
def load(self) -> Optional[CachedLicense]
```

- **Returns:** `Optional[CachedLicense]`
- **Description:** Load cached license, or None if not present / corrupt.

#### `clear`

```python
def clear(self) -> None
```

- **Returns:** `None`
- **Description:** Remove cached license (e.g., on explicit deactivation).

#### `is_within_grace`

```python
def is_within_grace(self) -> bool
```

- **Returns:** `bool`

#### `grace_status`

```python
def grace_status(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.license.validator.LicenseValidator`

**Bases:** object

Validates TokenPak license tokens.

Usage:
    validator = LicenseValidator(public_pem=PUBLIC_KEY_BYTES)
    result = validator.validate(token)
    if result.is_usable:
        ...

#### `__init__`

```python
def __init__(self, public_pem: Optional[bytes] = None, public_pem_path: Optional[Path] = None, seat_registry: Optional[SeatRegistry] = None) -> Any
```

- **Returns:** `Any`

#### `validate`

```python
def validate(self, token: str, agent_id: Optional[str] = None) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Full license validation:

#### `has_feature`

```python
def has_feature(self, token: str, feature: str) -> bool
```

- **Returns:** `bool`

### `tokenpak.agent.license.validator.SeatRegistry`

**Bases:** object

Track active seat claims.

#### `claim`

```python
def claim(self, agent_id: str) -> None
```

- **Returns:** `None`

#### `release`

```python
def release(self, agent_id: str) -> None
```

- **Returns:** `None`

#### `active_count`

```python
def active_count(self) -> int
```

- **Returns:** `int`

#### `active_ids`

```python
def active_ids(self) -> list[str]
```

- **Returns:** `list[str]`

### `tokenpak.agent.license.validator.ValidationResult`

**Bases:** object

#### `is_usable`

```python
def is_usable(self) -> bool
```

- **Returns:** `bool`

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.macros.engine.MacroDefinition`

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

### `tokenpak.agent.macros.engine.MacroEngine`

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

### `tokenpak.agent.macros.engine.MacroResult`

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

### `tokenpak.agent.macros.engine.MacroStep`

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

### `tokenpak.agent.macros.engine.StepResult`

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

### `tokenpak.agent.macros.hooks.EventType`

**Bases:** str, Enum

Supported event types.

#### `from_string`

```python
def from_string(cls, value: str) -> 'EventType'
```

- **Returns:** `'EventType'`
- **Description:** Parse event type from string.

### `tokenpak.agent.macros.hooks.Trigger`

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

### `tokenpak.agent.macros.hooks.TriggerLogEntry`

**Bases:** object

Log entry for a trigger activation.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.agent.macros.hooks.TriggerRegistry`

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

### `tokenpak.agent.macros.premade_macros.PremadeMacroRunner`

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

### `tokenpak.agent.macros.scheduler.MacroScheduler`

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

### `tokenpak.agent.macros.scheduler.ScheduledMacro`

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

### `tokenpak.agent.proxy.circuit_breaker.CircuitBreaker`

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
- **Description:** Returns True if the request should proceed, False to fast-fail.

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
def status(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return a status dict for the /health endpoint.

#### `reset`

```python
def reset(self) -> None
```

- **Returns:** `None`
- **Description:** Manually reset the circuit to CLOSED. Admin use only.

### `tokenpak.agent.proxy.circuit_breaker.CircuitBreakerConfig`

**Bases:** object

Configuration for all circuit breakers.

#### `from_env`

```python
def from_env(cls) -> 'CircuitBreakerConfig'
```

- **Returns:** `'CircuitBreakerConfig'`

### `tokenpak.agent.proxy.circuit_breaker.CircuitBreakerRegistry`

**Bases:** object

Thread-safe registry of per-provider circuit breakers.

Breakers are created on first access and reused thereafter.

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
- **Description:** Returns True if the request should proceed for this provider.

#### `record_success`

```python
def record_success(self, provider: str) -> None
```

- **Returns:** `None`
- **Description:** Record a successful request for this provider.

#### `record_failure`

```python
def record_failure(self, provider: str) -> None
```

- **Returns:** `None`
- **Description:** Record a failed request for this provider.

#### `get_state`

```python
def get_state(self, provider: str) -> CircuitState
```

- **Returns:** `CircuitState`
- **Description:** Return the current circuit state for this provider.

#### `all_statuses`

```python
def all_statuses(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return status dicts for all known providers.

#### `reset`

```python
def reset(self, provider: str) -> None
```

- **Returns:** `None`
- **Description:** Manually reset a specific provider's circuit.

#### `reset_all`

```python
def reset_all(self) -> None
```

- **Returns:** `None`
- **Description:** Reset all circuits to CLOSED.

#### `enabled`

```python
def enabled(self) -> bool
```

- **Returns:** `bool`

### `tokenpak.agent.proxy.connection_pool.ConnectionPool`

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
def request(self, method: str, url: str, *, content: Optional[bytes] = None, headers: Optional[dict] = None) -> httpx.Response
```

- **Returns:** `httpx.Response`
- **Description:** Send a non-streaming HTTP request via the pool.

#### `stream`

```python
def stream(self, method: str, url: str, *, content: Optional[bytes] = None, headers: Optional[dict] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Send a streaming HTTP request via the pool.

#### `http2_enabled`

```python
def http2_enabled(self) -> bool
```

- **Returns:** `bool`
- **Description:** True if HTTP/2 will be used (config says yes AND h2 is installed).

#### `active_providers`

```python
def active_providers(self) -> list
```

- **Returns:** `list`
- **Description:** List of netloc strings for which a client has been created.

#### `metrics`

```python
def metrics(self) -> dict
```

- **Returns:** `dict`
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
- **Description:** Close all pooled clients and release TCP/TLS resources.

### `tokenpak.agent.proxy.connection_pool.PoolConfig`

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

#### `from_env`

```python
def from_env(cls) -> 'PoolConfig'
```

- **Returns:** `'PoolConfig'`
- **Description:** Build a PoolConfig from environment variables.

### `tokenpak.agent.proxy.connection_pool.PoolMetrics`

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
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.proxy.degradation.DegradationEvent`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.agent.proxy.degradation.DegradationTracker`

**Bases:** object

Thread-safe, bounded in-memory log of degradation events.

Usage::

    from tokenpak.agent.proxy.degradation import get_degradation_tracker
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
- **Description:** True if there was a degradation event in the last 10 minutes.

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

### `tokenpak.agent.proxy.failover.FailoverConfig`

**Bases:** object

Parsed failover configuration block.

#### `available_chain`

```python
def available_chain(self) -> List[ProviderEntry]
```

- **Returns:** `List[ProviderEntry]`
- **Description:** Return only providers whose credentials are present in the environment.

### `tokenpak.agent.proxy.failover.FailoverManager`

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

### `tokenpak.agent.proxy.failover.ProviderEntry`

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

### `tokenpak.agent.proxy.failover_engine.CircuitBreaker`

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

### `tokenpak.agent.proxy.failover_engine.ClassifiedError`

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

### `tokenpak.agent.proxy.failover_engine.FailoverEngine`

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

### `tokenpak.agent.proxy.failover_engine.FailoverEvent`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.agent.proxy.failover_engine.FailoverEventLog`

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

### `tokenpak.agent.proxy.passthrough.CredentialPassthrough`

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

### `tokenpak.agent.proxy.prompt_builder.PromptBuilder`

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

### `tokenpak.agent.proxy.prompt_builder.PromptCacheStats`

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

#### `summary`

```python
def summary(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.proxy.prompt_builder.PromptParts`

**Bases:** object

Decomposed prompt parts for inspection and reassembly.

#### `to_request_body`

```python
def to_request_body(self) -> dict[str, Any]
```

- **Returns:** `dict[str, Any]`
- **Description:** Reassemble into a complete Anthropic request body.

### `tokenpak.agent.proxy.providers.anthropic.AnthropicFormat`

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
def build_request(model: str, messages: List[Dict[str, Any]], system: Optional[str] = None, max_tokens: int = 4096, stream: bool = True, **kwargs) -> bytes
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

### `tokenpak.agent.proxy.providers.anthropic.AnthropicMessage`

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

### `tokenpak.agent.proxy.providers.google.GoogleContent`

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

### `tokenpak.agent.proxy.providers.google.GoogleFormat`

**Bases:** object

Handler for Google Gemini API format (stub).

Google uses:
- "contents" array instead of "messages"
- "parts" array within each content
- "systemInstruction" for system prompt
- Different role names ("model" instead of "assistant")

TODO: Full implementation for multi-provider support.

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
def is_streaming(data: Dict[str, Any]) -> bool
```

- **Returns:** `bool`
- **Description:** Check if request is streaming (determined by URL, not body).

#### `build_request`

```python
def build_request(contents: List[Dict[str, Any]], system_instruction: Optional[str] = None, generation_config: Optional[Dict[str, Any]] = None, **kwargs) -> bytes
```

- **Returns:** `bytes`
- **Description:** Build a Google API request body.

#### `extract_response_tokens`

```python
def extract_response_tokens(body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract output token count from response.

### `tokenpak.agent.proxy.providers.openai.OpenAIFormat`

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
def build_request(model: str, messages: List[Dict[str, Any]], max_tokens: Optional[int] = None, stream: bool = True, **kwargs) -> bytes
```

- **Returns:** `bytes`
- **Description:** Build an OpenAI API request body.

#### `extract_response_tokens`

```python
def extract_response_tokens(body: bytes) -> int
```

- **Returns:** `int`
- **Description:** Extract output token count from response.

### `tokenpak.agent.proxy.providers.openai.OpenAIMessage`

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

### `tokenpak.agent.proxy.providers.stream_translator.StreamingTranslator`

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

### `tokenpak.agent.proxy.router.ProviderRouter`

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
- **Description:** Route a request to the appropriate provider.

### `tokenpak.agent.proxy.server.GracefulShutdown`

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
def track_request(self) -> Generator[None, None, None]
```

- **Returns:** `Generator[None, None, None]`
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

### `tokenpak.agent.proxy.server.PipelineTrace`

**Bases:** object

Complete trace for a single request through the pipeline.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.proxy.server.ProxyServer`

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
def __init__(self, host: str = '127.0.0.1', port: Optional[int] = None, compilation_mode: Optional[str] = None, request_hook: Optional[Callable] = None, shutdown_timeout: Optional[float] = None) -> Any
```

- **Returns:** `Any`

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
- **Description:** Gracefully shut down the proxy server.

#### `is_running`

```python
def is_running(self) -> bool
```

- **Returns:** `bool`

#### `health`

```python
def health(self, deep: bool = False) -> dict
```

- **Returns:** `dict`

#### `stats`

```python
def stats(self) -> dict
```

- **Returns:** `dict`

#### `session_stats`

```python
def session_stats(self) -> dict
```

- **Returns:** `dict`

#### `last_request_stats`

```python
def last_request_stats(self) -> dict
```

- **Returns:** `dict`

#### `reset_session`

```python
def reset_session(self) -> None
```

- **Returns:** `None`

### `tokenpak.agent.proxy.server.StageTrace`

**Bases:** object

Trace for a single pipeline stage.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.proxy.server.TraceStorage`

**Bases:** object

Thread-safe storage for recent pipeline traces.

#### `__init__`

```python
def __init__(self, max_traces: int = 10) -> Any
```

- **Returns:** `Any`

#### `store`

```python
def store(self, trace: PipelineTrace) -> None
```

- **Returns:** `None`

#### `get_last`

```python
def get_last(self) -> Optional[PipelineTrace]
```

- **Returns:** `Optional[PipelineTrace]`

#### `get_by_id`

```python
def get_by_id(self, request_id: str) -> Optional[PipelineTrace]
```

- **Returns:** `Optional[PipelineTrace]`

#### `get_all`

```python
def get_all(self) -> List[PipelineTrace]
```

- **Returns:** `List[PipelineTrace]`

### `tokenpak.agent.proxy.server_async.ConcurrencyLimiterMiddleware`

**Bases:** BaseHTTPMiddleware

Return HTTP 503 when MAX_CONCURRENCY in-flight requests are active.

#### `__init__`

```python
def __init__(self, app, max_concurrency: int = MAX_CONCURRENCY) -> Any
```

- **Returns:** `Any`

#### `dispatch`

```python
async def dispatch(self, request: Request, call_next) -> Any
```

- **Returns:** `Any`

### `tokenpak.agent.proxy.stats.CompressionStats`

**Bases:** object

Thread-safe compression telemetry recorder.

Usage::

    stats = CompressionStats()
    stats.record_compression(
        model="claude-sonnet-4-6",
        input_tokens=4200,
        output_tokens=1800,
        ratio=0.57,
        latency_ms=42,
        status="ok",
    )
    summary = stats.get_stats()

#### `__init__`

```python
def __init__(self, log_path: Optional[str] = None, start_time: Optional[float] = None) -> Any
```

- **Returns:** `Any`

#### `record_compression`

```python
def record_compression(self, model: str, input_tokens: int, output_tokens: int, ratio: float, latency_ms: int, status: str = 'ok') -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Record one compression event.

#### `get_stats`

```python
def get_stats(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Return aggregated stats over the rolling window (last 100 requests).

#### `read_events`

```python
def read_events(self, limit: int = ROLLING_WINDOW) -> List[Dict[str, Any]]
```

- **Returns:** `List[Dict[str, Any]]`
- **Description:** Read the last *limit* events from the JSONL log file on disk.

#### `stats_from_file`

```python
def stats_from_file(self, limit: int = ROLLING_WINDOW) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Compute stats entirely from the on-disk JSONL (no in-memory state).

#### `flush_shutdown_record`

```python
def flush_shutdown_record(self, record: Dict[str, Any]) -> None
```

- **Returns:** `None`
- **Description:** Append a ``event: shutdown`` record to the telemetry JSONL file.

### `tokenpak.agent.proxy.stats_api.StatsAPI`

**Bases:** object

Handles HTTP requests for stats endpoints.

#### `handle_stats_last`

```python
def handle_stats_last() -> tuple[str, dict]
```

- **Returns:** `tuple[str, dict]`
- **Description:** Handle GET /stats/last request.

#### `handle_stats_session`

```python
def handle_stats_session() -> tuple[str, dict]
```

- **Returns:** `tuple[str, dict]`
- **Description:** Handle GET /stats/session request.

#### `route`

```python
def route(path: str) -> tuple[str, dict] | None
```

- **Returns:** `tuple[str, dict] | None`
- **Description:** Route HTTP requests to appropriate handler.

### `tokenpak.agent.proxy.streaming.StreamHandler`

**Bases:** object

Handles streaming responses with buffering and metrics extraction.

Supports gzip decompression and chunk-by-chunk forwarding.

#### `__init__`

```python
def __init__(self, content_encoding: str = '') -> Any
```

- **Returns:** `Any`
- **Description:** Initialize stream handler.

#### `process_chunk`

```python
def process_chunk(self, chunk: bytes) -> bytes
```

- **Returns:** `bytes`
- **Description:** Process a chunk from the stream.

#### `get_buffer`

```python
def get_buffer(self) -> bytes
```

- **Returns:** `bytes`
- **Description:** Get all buffered data.

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

### `tokenpak.agent.proxy.streaming.StreamUsage`

**Bases:** object

Usage metrics extracted from streaming response.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, int]
```

- **Returns:** `Dict[str, int]`

### `tokenpak.agent.proxy.tool_schema_registry.ToolSchemaRegistry`

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
def stats(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.query.api.EntryStore`

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

### `tokenpak.agent.recipe_sdk.RecipeSDK`

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

### `tokenpak.agent.routing.fallback.FallbackExhaustedError`

**Bases:** Exception

Raised when all fallback options (retry + provider chain) are exhausted.

#### `__init__`

```python
def __init__(self, context: dict, cause: RetryExhaustedError) -> Any
```

- **Returns:** `Any`

### `tokenpak.agent.routing.fallback.FallbackRouter`

**Bases:** object

Proxy-layer fallback router.

Wraps any provider request function with:
- Exponential backoff (Level 0)
- Model downgrade within provider (Level 1)
- Provider switch via FailoverManager (Level 2)
- Agent handoff (Level 3, if on_handoff provided)
- Human alert (Level 4)

Parameters
----------
agent_id : str
    Identifier of the agent/proxy instance making calls.
state_dir : Path | None
    Override state persistence directory.
on_handoff : callable | None
    Hook: (context, partial_state) -> bool
on_human_alert : callable | None
    Hook: (alert_dict) -> None
failover_manager : FailoverManager | None
    Pre-configured failover manager (loads from config.yaml if None).

#### `__init__`

```python
def __init__(self, agent_id: str = 'proxy-worker', state_dir: Optional[Path] = None, on_handoff: Optional[Callable[[dict, dict], bool]] = None, on_human_alert: Optional[Callable[[dict], None]] = None, failover_manager: Optional[FailoverManager] = None) -> Any
```

- **Returns:** `Any`

#### `call`

```python
def call(self, fn: Callable[[dict, dict], Any], context: dict, partial_state: Optional[dict] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Execute *fn* with full retry/fallback intelligence.

### `tokenpak.agent.team.agent_registry.AgentRecord`

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

### `tokenpak.agent.team.agent_registry.AgentRegistry`

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

### `tokenpak.agent.team.shared_vault.SharedVault`

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

### `tokenpak.agent.team.shared_vault.SharedVaultBlock`

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

### `tokenpak.agent.team.templates.Template`

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

### `tokenpak.agent.team.templates.TemplateStore`

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

### `tokenpak.agent.telemetry.budget.BudgetConfig`

**Bases:** object

User-configured budget limits.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

#### `from_dict`

```python
def from_dict(cls, d: dict) -> 'BudgetConfig'
```

- **Returns:** `'BudgetConfig'`

### `tokenpak.agent.telemetry.budget.BudgetStatus`

**Bases:** object

Current budget consumption snapshot.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.telemetry.budget.BudgetTracker`

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
def list_spend(self, limit: int = 50, period: Optional[str] = None, model: Optional[str] = None, agent: Optional[str] = None) -> list[dict]
```

- **Returns:** `list[dict]`
- **Description:** List spend records with optional filters.

#### `by_model_summary`

```python
def by_model_summary(self, period: Optional[str] = None) -> list[dict]
```

- **Returns:** `list[dict]`
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

### `tokenpak.agent.telemetry.collector.RequestStats`

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
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.telemetry.collector.SessionStats`

**Bases:** object

Aggregated stats across all requests since the proxy started.

#### `session_total_percent`

```python
def session_total_percent(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.telemetry.collector.TelemetryCollector`

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
def get_history(self, limit: int = 10) -> list
```

- **Returns:** `list`

#### `reset_session`

```python
def reset_session(self) -> None
```

- **Returns:** `None`

#### `create_demo_stats`

```python
def create_demo_stats() -> tuple
```

- **Returns:** `tuple`

### `tokenpak.agent.telemetry.cost_tracker.CostTracker`

**Bases:** object

Track per-request LLM cost with SQLite persistence.

Usage::

    tracker = CostTracker("~/.tokenpak/cost.db")
    cost = tracker.record_request("claude-sonnet-4-5", 1000, 250)
    summary = tracker.get_summary("day")

#### `__init__`

```python
def __init__(self, db_path: str = ':memory:') -> Any
```

- **Returns:** `Any`

#### `record_request`

```python
def record_request(self, model: str, prompt_tokens: int, completion_tokens: int, *, session_id: str = '', timestamp: Optional[str] = None) -> float
```

- **Returns:** `float`
- **Description:** Record a completed request and return the estimated cost_usd.

#### `get_summary`

```python
def get_summary(self, period: str = 'day') -> dict
```

- **Returns:** `dict`
- **Description:** Return summary dict for the given period.

#### `get_by_model`

```python
def get_by_model(self, period: str = 'day') -> list[dict]
```

- **Returns:** `list[dict]`
- **Description:** Return per-model breakdown for the given period.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`

### `tokenpak.agent.telemetry.replay.ReplayEntry`

**Bases:** object

Metadata snapshot of a single proxied request for replay.

``messages`` and ``response`` are opt-in content fields. They are
``None`` by default and only populated when content capture is
explicitly enabled.

#### `new`

```python
def new(cls, provider: str, model: str, input_tokens_raw: int, input_tokens_sent: int, tokens_saved: int, cost_usd: float = 0.0, messages: Optional[list] = None, response: Optional[dict] = None, metadata: Optional[dict] = None) -> 'ReplayEntry'
```

- **Returns:** `'ReplayEntry'`
- **Description:** Create a new entry with a fresh UUID and current timestamp.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

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

### `tokenpak.agent.telemetry.replay.ReplayStore`

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
def list(self, limit: int = 20, provider: Optional[str] = None) -> list
```

- **Returns:** `list`
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

### `tokenpak.agent.telemetry.storage.TelemetryStorage`

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
def list_requests(self, limit: int = 100) -> list[dict]
```

- **Returns:** `list[dict]`
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

### `tokenpak.agent.triggers.daemon.TriggerDaemon`

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

### `tokenpak.agent.triggers.store.TriggerStore`

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

### `tokenpak.agent.vault.ast_parser.ASTParser`

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

### `tokenpak.agent.vault.blocks.BlockRecord`

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
def to_dict(self) -> dict
```

- **Returns:** `dict`

#### `from_dict`

```python
def from_dict(cls, data: dict) -> 'BlockRecord'
```

- **Returns:** `'BlockRecord'`

### `tokenpak.agent.vault.blocks.BlockStore`

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
def __init__(self, store_path: str = ':memory:') -> Any
```

- **Returns:** `Any`

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
- **Description:** Write blocks to the JSON store file.

### `tokenpak.agent.vault.indexer.VaultIndexer`

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
def __init__(self, block_store: Optional[BlockStore] = None, symbol_table: Optional[SymbolTable] = None) -> Any
```

- **Returns:** `Any`

#### `index_file`

```python
def index_file(self, path: str, content: Optional[str] = None) -> Optional[BlockRecord]
```

- **Returns:** `Optional[BlockRecord]`
- **Description:** Index a single file. Reads from disk if content not provided.

#### `index_directory`

```python
def index_directory(self, root: str, on_progress: Optional[Callable[[str], None]] = None) -> dict
```

- **Returns:** `dict`
- **Description:** Walk and index all supported files under root.

#### `search`

```python
def search(self, query: str, top_k: int = 10) -> list[BlockRecord]
```

- **Returns:** `list[BlockRecord]`
- **Description:** Search indexed blocks by keyword.

#### `lookup_symbol`

```python
def lookup_symbol(self, name: str) -> Any
```

- **Returns:** `Any`
- **Description:** Look up a symbol by exact name.

#### `stats`

```python
def stats(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return indexer stats.

#### `stats_by_type`

```python
def stats_by_type(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return indexed file count broken down by file type and extension.

### `tokenpak.agent.vault.symbols.Symbol`

**Bases:** object

A named code symbol (function, class, constant, etc.).

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.agent.vault.symbols.SymbolTable`

**Bases:** object

Build and query a symbol table from source files.

Usage::

    table = SymbolTable()
    table.index_file("mymodule.py", source_code)
    results = table.lookup("MyClass")
    all_syms = table.all_symbols()

#### `__init__`

```python
def __init__(self) -> Any
```

- **Returns:** `Any`

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

### `tokenpak.agent.vault.watcher.VaultWatcher`

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
def __init__(self, config: WatcherConfig, on_change: Optional[Callable[[str], None]] = None) -> Any
```

- **Returns:** `Any`

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
def status(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return a status/stats dict.

### `tokenpak.agent.vault.watcher.WatcherStats`

**Bases:** object

#### `uptime_seconds`

```python
def uptime_seconds(self) -> float
```

- **Returns:** `float`

### `tokenpak.api.routes.HealthRoute`

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

### `tokenpak.api.routes.MetricsRoute`

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

### `tokenpak.api.routes.RouteRegistry`

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

### `tokenpak.assembler.CanonBlockRegistry`

**Bases:** object

Lightweight file-based registry for CANON blocks.

Stores canonical block wire text at:
  .ocp/blocks/BLOCK_ID@vN.ocpb

Tracks versions in manifest:
  .ocp/blocks/manifest.json  →  {block_id: {hash, version}}

#### `__init__`

```python
def __init__(self, base_dir: str = '.ocp') -> Any
```

- **Returns:** `Any`

#### `get_or_register`

```python
def get_or_register(self, block_id: str, content: str) -> Tuple[str, bool]
```

- **Returns:** `Tuple[str, bool]`
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
- **Description:** Read stored .ocpb content for a block/version pair.

### `tokenpak.assembler.ContextAssembler`

**Bases:** object

Assembles OCP wire-format context payloads.

  Session state (which blocks have been sent at which version) is
  persisted to .ocp/state/session_<id>.state.json so it survives
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
def __init__(self, session_id: str, base_dir: str = '.ocp') -> Any
```

- **Returns:** `Any`

#### `sent_blocks`

```python
def sent_blocks(self) -> Dict[str, str]
```

- **Returns:** `Dict[str, str]`
- **Description:** Map of {block_id: version_str} for blocks already sent this session.

#### `add_canon_block`

```python
def add_canon_block(self, block_id: str, block_content: str, version: Optional[str] = None) -> str
```

- **Returns:** `str`
- **Description:** Produce the wire entry for one CANON block.

#### `assemble_context`

```python
def assemble_context(self, required_blocks: Dict[str, Tuple[str, Optional[str]]], save_session: bool = True) -> str
```

- **Returns:** `str`
- **Description:** Build the full CANON section for a request payload.

#### `assemble_full_payload`

```python
def assemble_full_payload(self, required_blocks: Dict[str, Tuple[str, Optional[str]]], state_manager = None, evidence_pack = None, recent_text: str = '', tools_text: str = '', budgeter = None) -> str
```

- **Returns:** `str`
- **Description:** Build the complete OCP payload: CANON section + optional STATE_JSON.

#### `session_summary`

```python
def session_summary(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return current session metadata for logging/debugging.

### `tokenpak.broker.Broker`

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

### `tokenpak.budget.BudgetBlock`

**Bases:** object

Block metadata for budget allocation.

#### `importance`

```python
def importance(self) -> float
```

- **Returns:** `float`
- **Description:** Composite importance score (0-10), modulated by utility weight.

### `tokenpak.budgeter.Budgeter`

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
def summary(cls) -> dict[str, dict]
```

- **Returns:** `dict[str, dict]`
- **Description:** Return a size snapshot for all registered caches.

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
def is_cached(self, key: str) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if *key* is present and not expired.

#### `retrieve`

```python
def retrieve(self, key: str) -> Optional[Any]
```

- **Returns:** `Optional[Any]`
- **Description:** Return cached value for *key*, or None if missing / expired.

#### `get`

```python
def get(self, key: str, default: Any = None) -> Any
```

- **Returns:** `Any`

#### `set`

```python
def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None
```

- **Returns:** `None`
- **Description:** Store *value* under *key*.  Evicts LRU entry if at capacity.

#### `invalidate`

```python
def invalidate(self, key: str) -> bool
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
def to_dict(self) -> dict
```

- **Returns:** `dict`

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
def recent_requests(self, n: int = 10) -> List[dict]
```

- **Returns:** `List[dict]`
- **Description:** Return the last *n* requests as dicts (newest last).

#### `summary`

```python
def summary(self) -> dict
```

- **Returns:** `dict`
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
def is_cached(self, key: str) -> bool
```

- **Returns:** `bool`
- **Description:** Return True if *key* exists and has not expired.

#### `retrieve`

```python
def retrieve(self, key: str) -> Optional[Any]
```

- **Returns:** `Optional[Any]`
- **Description:** Return the cached value or None if missing / expired.

#### `get`

```python
def get(self, key: str, default: Any = None) -> Any
```

- **Returns:** `Any`

#### `set`

```python
def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None
```

- **Returns:** `None`
- **Description:** Store *value* under *key* with an optional per-entry TTL override.

#### `invalidate`

```python
def invalidate(self, key: str) -> bool
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

### `tokenpak.capsule.builder.CapsuleBuilder`

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

### `tokenpak.cli.Colors`

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

### `tokenpak.cli_doctor.Colors`

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

### `tokenpak.compaction.policy.BlockPolicy`

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

### `tokenpak.compaction.policy.CompactionPolicy`

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

### `tokenpak.connectors.base.Connector`

**Bases:** ABC

Base class for data source connectors.

Connectors handle:
- Authentication/authorization
- File listing and delta detection
- Content retrieval
- Sync state management

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
- **Description:** Establish connection to the data source.

#### `list_files`

```python
def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]
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
def disconnect(self) -> Any
```

- **Returns:** `Any`
- **Description:** Close connection to the data source.

#### `get_sync_state`

```python
def get_sync_state(self) -> dict
```

- **Returns:** `dict`
- **Description:** Get current sync state for resumable syncs.

#### `set_sync_state`

```python
def set_sync_state(self, state: dict) -> Any
```

- **Returns:** `Any`
- **Description:** Restore sync state.

### `tokenpak.connectors.base_source.SourceAdapter`

**Bases:** ABC

Abstract base for on-demand source adapters.

Each subclass fetches content from one source_type and returns a
(content, Provenance) pair. The caller is responsible for wrapping
into a Block and persisting to the registry.

#### `ingest`

```python
def ingest(self, source_id: str, **kwargs) -> tuple
```

- **Returns:** `tuple`
- **Raises:** `SourceFetchError on non-recoverable failures.`
- **Description:** Fetch content from the source.

#### `has_changed`

```python
def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool
```

- **Returns:** `bool`
- **Description:** Check whether the source has changed since cached_version.

### `tokenpak.connectors.git_adapter.GitAdapter`

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

### `tokenpak.connectors.github.GitHubConnector`

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
- **Description:** Establish connection using PAT.

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

### `tokenpak.connectors.google_drive.GoogleDriveConnector`

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
- **Description:** Establish connection using OAuth2.

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

### `tokenpak.connectors.local.LocalConnector`

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

### `tokenpak.connectors.notion.NotionConnector`

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
- **Description:** Establish connection using integration token.

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

### `tokenpak.connectors.notion_adapter.NotionAdapter`

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

### `tokenpak.connectors.obsidian.ObsidianConnector`

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
def extract_links(self, content: str) -> list
```

- **Returns:** `list`
- **Description:** Extract wiki-links from content.

#### `extract_frontmatter`

```python
def extract_frontmatter(self, content: str) -> dict
```

- **Returns:** `dict`
- **Description:** Extract YAML frontmatter from content.

### `tokenpak.connectors.url_adapter.URLAdapter`

**Bases:** SourceAdapter

Fetch and index web pages by URL.

#### `ingest`

```python
def ingest(self, source_id: str, **kwargs) -> Tuple[str, Provenance]
```

- **Returns:** `Tuple[str, Provenance]`
- **Description:** Fetch a URL and return clean text + provenance.

#### `has_changed`

```python
def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool
```

- **Returns:** `bool`
- **Description:** Check whether the page has changed via a HEAD request + ETag comparison.

### `tokenpak.core.IndexRegistry`

**Bases:** object

Return value of index_directory(). Has .blocks and .tokenpak_dir.

#### `__init__`

```python
def __init__(self, vault_dir: Path, blocks: dict) -> Any
```

- **Returns:** `Any`

### `tokenpak.elo.EloRatings`

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

### `tokenpak.engines.base.CompactionEngine`

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

### `tokenpak.engines.heuristic.HeuristicEngine`

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

### `tokenpak.engines.llmlingua.LLMLinguaEngine`

**Bases:** CompactionEngine

ML-powered compaction using Microsoft LLMLingua.

Requires: pip install llmlingua

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

### `tokenpak.enterprise.audit.AuditLog`

**Bases:** object

Immutable append-only audit log backed by SQLite WAL.

The log is *append-only*: rows are never updated or deleted during normal
operation (only the retention pruner removes rows older than the configured
retention window).  Each row carries a SHA-256 hash of its own content
chained to the previous row's hash, making tampering detectable.

Parameters
----------
path:
    File-system path to the SQLite database, or ``":memory:"`` for tests.
retention_days:
    How long to keep entries (default: 90 days, configurable).

#### `__init__`

```python
def __init__(self, path: Union[str, Path] = '.tokenpak/audit.db', retention_days: int = 90) -> None
```

- **Returns:** `None`

#### `record`

```python
def record(self, action: str, user_id: str = '', agent_id: str = '', model: str = '', provider: str = '', data_classification: str = 'unclassified', outcome: str = 'ok', source_ip: str = '', session_id: str = '', metadata: Optional[dict] = None) -> str
```

- **Returns:** `str`
- **Description:** Append a new audit entry. Returns the entry ``id``.

#### `list`

```python
def list(self, since: Optional[str] = None, until: Optional[str] = None, user_id: Optional[str] = None, action: Optional[str] = None, model: Optional[str] = None, outcome: Optional[str] = None, limit: int = 500, offset: int = 0) -> list[dict]
```

- **Returns:** `list[dict]`
- **Description:** Return audit entries matching the given filters.

#### `count`

```python
def count(self, since: Optional[str] = None, user_id: Optional[str] = None) -> int
```

- **Returns:** `int`
- **Description:** Count matching audit entries.

#### `export`

```python
def export(self, path: Union[str, Path], fmt: str = 'json', **list_kwargs) -> int
```

- **Returns:** `int`
- **Description:** Export audit log to *path* in *fmt* (``'json'`` or ``'csv'``).

#### `verify_chain`

```python
def verify_chain(self) -> tuple[bool, List[str]]
```

- **Returns:** `tuple[bool, List[str]]`
- **Description:** Verify the hash chain integrity.

#### `prune`

```python
def prune(self, retention_days: Optional[int] = None) -> int
```

- **Returns:** `int`
- **Description:** Delete entries older than *retention_days*. Returns rows deleted.

#### `summary`

```python
def summary(self, since: Optional[str] = None) -> dict
```

- **Returns:** `dict`
- **Description:** Return aggregate summary stats for the audit log.

#### `close`

```python
def close(self) -> None
```

- **Returns:** `None`

### `tokenpak.enterprise.compliance.ComplianceReport`

**Bases:** object

Full compliance report for a given standard.

#### `as_dict`

```python
def as_dict(self) -> dict
```

- **Returns:** `dict`

#### `as_json`

```python
def as_json(self, indent: int = 2) -> str
```

- **Returns:** `str`

#### `as_text`

```python
def as_text(self) -> str
```

- **Returns:** `str`

#### `save`

```python
def save(self, path: Union[str, Path], fmt: str = 'json') -> None
```

- **Returns:** `None`

### `tokenpak.enterprise.compliance.ComplianceReporter`

**Bases:** object

Generate compliance reports from audit log + config.

Parameters
----------
audit_db:
    Path to the audit SQLite database.
organization:
    Organization name to include in reports.

#### `__init__`

```python
def __init__(self, audit_db: Union[str, Path, None] = None, organization: str = 'Your Organization') -> None
```

- **Returns:** `None`

#### `generate`

```python
def generate(self, standard: str, since: Optional[str] = None, until: Optional[str] = None) -> ComplianceReport
```

- **Returns:** `ComplianceReport`
- **Description:** Generate a compliance report for *standard*.

### `tokenpak.evidence_pack.EvidenceItem`

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
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.evidence_pack.EvidencePack`

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
def add_from_memory(self, memory_chunks: List[dict], query: str, max_items: int = 10, max_tokens_each: int = 50) -> None
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

### `tokenpak.handlers.rate_limit.RateLimitBackoff`

**Bases:** object

Compute wait durations for retrying after a 429 rate-limit response.

Parameters
----------
base_wait:
    Initial wait in seconds (attempt 0).
max_wait:
    Hard ceiling on the returned wait time.
jitter_factor:
    Fraction of the computed wait to add as random jitter.
    0.0 = no jitter (deterministic, good for tests).
    0.1 = ±10 % jitter (default in production use).

#### `__init__`

```python
def __init__(self, base_wait: float = 1.0, max_wait: float = 60.0, jitter_factor: float = 0.1) -> None
```

- **Returns:** `None`

#### `wait_time`

```python
def wait_time(self, attempt: int, *, retry_after: float | None = None) -> float
```

- **Returns:** `float`
- **Description:** Return the number of seconds to wait before the next attempt.

### `tokenpak.integrations.litellm.middleware.TokenPakMiddleware`

**Bases:** object

LiteLLM Router middleware that compiles TokenPak packs before sending.

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

### `tokenpak.integrations.litellm.proxy.ProxyHandler`

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

### `tokenpak.intelligence.ab_optimizer.ABOptimizerStore`

**Bases:** object

Thread-safe SQLite-backed store for A/B experiments.

#### `__init__`

```python
def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> Any
```

- **Returns:** `Any`

#### `create_experiment`

```python
def create_experiment(self, name: str, description: str = '', control_name: str = 'control', treatment_name: str = 'treatment', tags: Optional[List[str]] = None) -> Experiment
```

- **Returns:** `Experiment`

#### `get_experiment`

```python
def get_experiment(self, exp_id: str) -> Optional[Experiment]
```

- **Returns:** `Optional[Experiment]`

#### `list_experiments`

```python
def list_experiments(self, status_filter: Optional[str] = None) -> List[Experiment]
```

- **Returns:** `List[Experiment]`

#### `record_observation`

```python
def record_observation(self, exp_id: str, variant: str, token_savings: float, quality_score: float, latency_ms: float) -> Optional[SignificanceResult]
```

- **Returns:** `Optional[SignificanceResult]`
- **Description:** Record one observation for a variant.

#### `force_winner`

```python
def force_winner(self, exp_id: str, variant: str) -> Experiment
```

- **Returns:** `Experiment`
- **Description:** Manual override: force a variant as the winner.

#### `cancel_experiment`

```python
def cancel_experiment(self, exp_id: str) -> Experiment
```

- **Returns:** `Experiment`
- **Description:** Cancel an active experiment.

#### `get_results`

```python
def get_results(self, exp_id: str) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Full results for an experiment including significance test.

### `tokenpak.intelligence.ab_optimizer.Experiment`

**Bases:** object

An A/B experiment comparing two recipe variants.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.intelligence.ab_optimizer.SignificanceResult`

**Bases:** object

Result of significance test across all metrics.

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.intelligence.ab_optimizer.VariantStats`

**Bases:** object

Running statistics for one variant.

#### `token_savings_mean`

```python
def token_savings_mean(self) -> float
```

- **Returns:** `float`

#### `quality_mean`

```python
def quality_mean(self) -> float
```

- **Returns:** `float`

#### `latency_mean`

```python
def latency_mean(self) -> float
```

- **Returns:** `float`

#### `token_savings_var`

```python
def token_savings_var(self) -> float
```

- **Returns:** `float`

#### `quality_var`

```python
def quality_var(self) -> float
```

- **Returns:** `float`

#### `latency_var`

```python
def latency_var(self) -> float
```

- **Returns:** `float`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.intelligence.ab_router.CreateExperimentRequest`

**Bases:** BaseModel

#### `no_spaces`

```python
def no_spaces(cls, v: str) -> str
```

- **Returns:** `str`

### `tokenpak.intelligence.auth.APIKeyValidator`

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

### `tokenpak.intelligence.auth.PIIScrubFilter`

**Bases:** logging.Filter

Remove API keys and bearer tokens from log records.

#### `filter`

```python
def filter(self, record: logging.LogRecord) -> bool
```

- **Returns:** `bool`

### `tokenpak.intelligence.auth.RateLimiter`

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

### `tokenpak.intelligence.auth.TokenPakAuthMiddleware`

**Bases:** BaseHTTPMiddleware

Middleware that:
1. Injects a unique ``X-Request-ID`` into every request.
2. Validates ``X-TokenPak-Key``.
3. Enforces per-tier rate limits.
4. Attaches ``request.state.tier`` and ``request.state.request_id``.
5. Sets rate-limit response headers on every reply.

#### `__init__`

```python
def __init__(self, app, validator: Optional[APIKeyValidator] = None, limiter: Optional[RateLimiter] = None) -> None
```

- **Returns:** `None`

#### `dispatch`

```python
async def dispatch(self, request: Request, call_next: Callable) -> Any
```

- **Returns:** `Any`

### `tokenpak.intelligence.cost_intelligence.CostIntelligence`

**Bases:** object

Stateless cost intelligence analysis engine.

#### `compute_trends`

```python
def compute_trends(metrics: List[DailyMetric]) -> Dict[str, Trend]
```

- **Returns:** `Dict[str, Trend]`
- **Description:** Compute daily, weekly, and monthly trends.

#### `compute_model_breakdown`

```python
def compute_model_breakdown(metrics: List[DailyMetric]) -> List[dict]
```

- **Returns:** `List[dict]`
- **Description:** Per-model cost and compression breakdown.

#### `detect_anomalies`

```python
def detect_anomalies(metrics: List[DailyMetric], threshold: float = 2.0) -> List[Anomaly]
```

- **Returns:** `List[Anomaly]`
- **Description:** Detect days where cost exceeds threshold × rolling 7-day baseline.

#### `compute_projections`

```python
def compute_projections(metrics: List[DailyMetric]) -> Dict[str, Projection]
```

- **Returns:** `Dict[str, Projection]`
- **Description:** Compute 7d and 30d cost projections.

#### `compute_recommendations`

```python
def compute_recommendations(model_breakdown: List[dict], monthly_budget_usd: Optional[float] = None) -> List[ModelRecommendation]
```

- **Returns:** `List[ModelRecommendation]`
- **Description:** Generate model-switch recommendations from usage patterns.

#### `check_budget_alert`

```python
def check_budget_alert(spent_usd: float, budget_usd: float) -> BudgetAlert
```

- **Returns:** `BudgetAlert`
- **Description:** Return alert level based on % of budget consumed.

#### `analyze`

```python
def analyze(cls, metrics: List[DailyMetric], monthly_budget_usd: Optional[float] = None, anomaly_threshold: float = 2.0) -> dict
```

- **Returns:** `dict`
- **Description:** Run full analysis pipeline and return a combined result dict.

### `tokenpak.intelligence.deep_health.CheckResult`

**Bases:** object

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.intelligence.deep_health.DeepHealthChecker`

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
def __init__(self, db_path: Optional[str] = None, index_path: Optional[str] = None, provider_timeout: float = 5.0, _check_anthropic = None, _check_openai = None, _check_database = None, _check_index = None, _check_memory = None, _check_disk = None) -> Any
```

- **Returns:** `Any`

#### `run`

```python
def run(self) -> DeepHealthResult
```

- **Returns:** `DeepHealthResult`
- **Description:** Run all checks synchronously (safe for sync and async contexts).

### `tokenpak.intelligence.deep_health.DeepHealthResult`

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

### `tokenpak.middleware.audit_trail.CompileAudit`

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

### `tokenpak.middleware.logger.AsyncLogger`

**Bases:** object

Asynchronous logger with buffering.

#### `__init__`

```python
def __init__(self, config: LoggingConfig) -> Any
```

- **Returns:** `Any`

#### `log`

```python
def log(self, record: LogRecord) -> Any
```

- **Returns:** `Any`
- **Description:** Enqueue log record.

#### `stop`

```python
def stop(self) -> Any
```

- **Returns:** `Any`
- **Description:** Stop async logging.

### `tokenpak.middleware.logger.LogRecord`

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

### `tokenpak.middleware.logger.LoggingConfig`

**Bases:** object

Logging configuration.

#### `resolve_log_dir`

```python
def resolve_log_dir(self) -> str
```

- **Returns:** `str`
- **Description:** Resolve log directory path.

### `tokenpak.middleware.logger.RequestLogger`

**Bases:** object

Structured request logger.

#### `__init__`

```python
def __init__(self, config: LoggingConfig) -> Any
```

- **Returns:** `Any`

#### `log_request`

```python
def log_request(self, endpoint: str, method: str = 'POST', client_ip: Optional[str] = None, request_size: int = 0, response_size: int = 0, status_code: int = 200, latency_ms: float = 0.0, compression_ratio: Optional[float] = None, message: str = '', context: Optional[Dict[str, Any]] = None, level: LogLevel = 'info', request_id: Optional[str] = None) -> Any
```

- **Returns:** `Any`
- **Description:** Log a request.

#### `stop`

```python
def stop(self) -> Any
```

- **Returns:** `Any`
- **Description:** Stop logging.

### `tokenpak.middleware.logging_middleware.LoggingMiddleware`

**Bases:** object

Request logging middleware for proxy.

#### `__init__`

```python
def __init__(self, logger: RequestLogger) -> Any
```

- **Returns:** `Any`

#### `wrap_request`

```python
def wrap_request(self, endpoint: str, method: str = 'POST') -> Callable
```

- **Returns:** `Callable`
- **Description:** Decorator to wrap a request handler with logging.

#### `log_compile_audit`

```python
def log_compile_audit(self, audit: CompileAudit) -> Any
```

- **Returns:** `Any`
- **Description:** Log compilation audit trail.

#### `log_cache_audit`

```python
def log_cache_audit(self, audit: CacheAudit) -> Any
```

- **Returns:** `Any`
- **Description:** Log cache audit trail.

#### `log_metrics_audit`

```python
def log_metrics_audit(self, audit: MetricsAudit) -> Any
```

- **Returns:** `Any`
- **Description:** Log metrics audit trail.

### `tokenpak.middleware.tests.test_audit_trail.TestBlockAudit`

**Bases:** object

Test BlockAudit data class.

#### `test_block_audit_creation`

```python
def test_block_audit_creation(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating a block audit.

### `tokenpak.middleware.tests.test_audit_trail.TestCacheAudit`

**Bases:** object

Test CacheAudit data class.

#### `test_cache_audit_get`

```python
def test_cache_audit_get(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test cache get audit.

#### `test_cache_audit_set`

```python
def test_cache_audit_set(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test cache set audit.

#### `test_cache_audit_invalidate`

```python
def test_cache_audit_invalidate(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test cache invalidate audit.

### `tokenpak.middleware.tests.test_audit_trail.TestCompileAudit`

**Bases:** object

Test CompileAudit data class.

#### `test_compile_audit_creation`

```python
def test_compile_audit_creation(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating a compile audit.

#### `test_compile_audit_to_dict`

```python
def test_compile_audit_to_dict(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test converting compile audit to dict.

#### `test_compile_audit_to_json`

```python
def test_compile_audit_to_json(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test converting compile audit to JSON.

#### `test_compile_audit_compression_ratio`

```python
def test_compile_audit_compression_ratio(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test compression ratio calculation.

### `tokenpak.middleware.tests.test_audit_trail.TestFactoryFunctions`

**Bases:** object

Test factory functions.

#### `test_create_compile_audit`

```python
def test_create_compile_audit(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating compile audit via factory.

#### `test_create_cache_audit`

```python
def test_create_cache_audit(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating cache audit via factory.

#### `test_create_metrics_audit`

```python
def test_create_metrics_audit(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating metrics audit via factory.

### `tokenpak.middleware.tests.test_audit_trail.TestMetricsAudit`

**Bases:** object

Test MetricsAudit data class.

#### `test_metrics_audit_creation`

```python
def test_metrics_audit_creation(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating a metrics audit.

### `tokenpak.middleware.tests.test_logger.TestAsyncLogger`

**Bases:** object

Test AsyncLogger.

#### `test_logger_creation`

```python
def test_logger_creation(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating an async logger.

#### `test_log_to_file`

```python
def test_log_to_file(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test logging to file.

#### `test_logger_disabled`

```python
def test_logger_disabled(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test disabled logger.

### `tokenpak.middleware.tests.test_logger.TestGlobalLogger`

**Bases:** object

Test global logger initialization.

#### `test_init_logger`

```python
def test_init_logger(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test initializing global logger.

#### `test_get_logger`

```python
def test_get_logger(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test getting initialized logger.

### `tokenpak.middleware.tests.test_logger.TestLogRecord`

**Bases:** object

Test LogRecord data class.

#### `test_log_record_creation`

```python
def test_log_record_creation(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating a log record.

#### `test_log_record_to_json`

```python
def test_log_record_to_json(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test converting log record to JSON.

#### `test_log_record_to_text`

```python
def test_log_record_to_text(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test converting log record to text.

### `tokenpak.middleware.tests.test_logger.TestLoggingConfig`

**Bases:** object

Test LoggingConfig.

#### `test_default_config`

```python
def test_default_config(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test default configuration.

#### `test_resolve_log_dir_default`

```python
def test_resolve_log_dir_default(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test default log directory resolution.

#### `test_resolve_log_dir_custom`

```python
def test_resolve_log_dir_custom(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test custom log directory.

### `tokenpak.middleware.tests.test_logger.TestRequestLogger`

**Bases:** object

Test RequestLogger.

#### `test_log_request`

```python
def test_log_request(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test logging a request.

#### `test_log_request_generates_request_id`

```python
def test_log_request_generates_request_id(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test that request ID is generated if not provided.

#### `test_log_with_context`

```python
def test_log_with_context(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test logging with context data.

### `tokenpak.middleware.tests.test_logging_middleware.TestLoggingMiddleware`

**Bases:** object

Test LoggingMiddleware.

#### `test_middleware_creation`

```python
def test_middleware_creation(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test creating middleware.

#### `test_wrap_request_success`

```python
def test_wrap_request_success(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test wrapping a successful request.

#### `test_wrap_request_error`

```python
def test_wrap_request_error(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test wrapping a request that raises an error.

#### `test_log_compile_audit`

```python
def test_log_compile_audit(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test logging a compile audit trail.

#### `test_log_cache_audit`

```python
def test_log_cache_audit(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test logging a cache audit trail.

#### `test_log_metrics_audit`

```python
def test_log_metrics_audit(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test logging a metrics audit trail.

### `tokenpak.middleware.tests.test_logging_middleware.TestPerformanceOverhead`

**Bases:** object

Test that logging doesn't add significant overhead.

#### `test_logging_latency_minimal`

```python
def test_logging_latency_minimal(self) -> Any
```

- **Returns:** `Any`
- **Description:** Test that logging adds minimal latency.

### `tokenpak.monitoring.audit_trail.AuditTrail`

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
def record_error(self, *, error_type: str, message: str, **extra) -> None
```

- **Returns:** `None`
- **Description:** Record an error that occurred during request processing.

#### `flush`

```python
def flush(self) -> None
```

- **Returns:** `None`
- **Description:** Enqueue all recorded events to the RequestLogger.

### `tokenpak.monitoring.health.HealthChecker`

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

### `tokenpak.monitoring.metrics.ProxyMetricsCollector`

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

### `tokenpak.monitoring.request_logger.RequestLogRecord`

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

### `tokenpak.monitoring.request_logger.RequestLogger`

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
def log_dict(self, level: str = LEVEL_INFO, **kwargs) -> None
```

- **Returns:** `None`
- **Description:** Convenience: log an arbitrary dict (debug/info/warn).

#### `stop`

```python
def stop(self, timeout: float = 5.0) -> None
```

- **Returns:** `None`
- **Description:** Flush remaining queue entries and stop the background thread.

### `tokenpak.pack.CompiledResult`

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

### `tokenpak.pack.ContextPack`

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

### `tokenpak.processors.code.CodeProcessor`

**Bases:** object

Extract code structure while dropping implementation details.

#### `process`

```python
def process(self, content: str, path: str = '') -> str
```

- **Returns:** `str`
- **Description:** Compress code by extracting structure.

### `tokenpak.processors.code_treesitter.TreeSitterProcessor`

**Bases:** object

Processor that uses tree-sitter to extract code structure.

Drop-in replacement for CodeProcessor for supported languages.
Falls back to CodeProcessor on parse failure or unsupported language.

#### `__init__`

```python
def __init__(self, fallback = None) -> Any
```

- **Returns:** `Any`

#### `process`

```python
def process(self, content: str, path: str = '') -> str
```

- **Returns:** `str`
- **Description:** Process a code file: extract API surface via tree-sitter, fall back

### `tokenpak.processors.data.DataProcessor`

**Bases:** object

Extract schema and samples from structured data files.

#### `process`

```python
def process(self, content: str, path: str = '') -> str
```

- **Returns:** `str`
- **Description:** Process structured data files into schema + sample.

### `tokenpak.processors.text.TextProcessor`

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

### `tokenpak.registry.BlockRegistry`

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
def __init__(self, db_path: str = '.tokenpak/registry.db') -> Any
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

### `tokenpak.report.Action`

**Bases:** Enum

#### `icon`

```python
def icon(self) -> str
```

- **Returns:** `str`

#### `label`

```python
def label(self) -> str
```

- **Returns:** `str`

### `tokenpak.report.CompileReport`

**Bases:** object

Full report of a single compile() call.

#### `tokens_saved`

```python
def tokens_saved(self) -> int
```

- **Returns:** `int`

#### `savings_percent`

```python
def savings_percent(self) -> float
```

- **Returns:** `float`

#### `budget_used_percent`

```python
def budget_used_percent(self) -> float
```

- **Returns:** `float`

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

### `tokenpak.report.Decision`

**Bases:** object

Record of what happened to a single block during compile.

#### `tokens_saved`

```python
def tokens_saved(self) -> int
```

- **Returns:** `int`

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

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

### `tokenpak.routing_ledger.RoutingLedger`

**Bases:** object

Thread-safe SQLite ledger for LLM transaction logging.
Uses WAL mode for concurrent readers + single writer.

#### `__init__`

```python
def __init__(self, db_path: str = DEFAULT_LEDGER_PATH) -> Any
```

- **Returns:** `Any`

#### `log_transaction`

```python
def log_transaction(self, model: str, query: str, context_blocks: List[str], response: str, accepted: Optional[bool] = None, rejection_reason: Optional[str] = None, latency_ms: float = 0.0, context_tokens: int = 0, response_tokens: int = 0, routing_action: str = 'passthrough') -> int
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
def get_transaction(self, transaction_id: int) -> Optional[dict]
```

- **Returns:** `Optional[dict]`
- **Description:** Fetch a single transaction by ID.

#### `get_recent`

```python
def get_recent(self, limit: int = 100) -> List[dict]
```

- **Returns:** `List[dict]`
- **Description:** Return the most recent N transactions.

#### `get_stats`

```python
def get_stats(self) -> dict
```

- **Returns:** `dict`
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

### `tokenpak.shadow_hook.ShadowHook`

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
def record_response(self, txn_key: Optional[int], response_text: str, response_tokens: int = 0, latency_ms: float = 0.0, context_blocks: Optional[list] = None) -> Optional[int]
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
def get_stats(self) -> dict
```

- **Returns:** `dict`
- **Description:** Return ledger stats, or empty dict on failure.

### `tokenpak.span_extractor.SpanExtractor`

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
def extract_span(self, chunk_text: str, query: str, max_tokens: int = 50) -> dict
```

- **Returns:** `dict`
- **Description:** Extract the most relevant span from a chunk.

#### `extract_spans_batch`

```python
def extract_spans_batch(self, chunks: List[dict], query: str, max_tokens_each: int = 50) -> List[dict]
```

- **Returns:** `List[dict]`
- **Description:** Extract spans from multiple chunks.

### `tokenpak.state_manager.StateManager`

**Bases:** object

Manages compact JSON session state for OCP protocol.

Persists to: .ocp/state/session_<id>.state.json
Wire format: compact JSON (no whitespace), prefixed with STATE_JSON:

#### `__init__`

```python
def __init__(self, session_id: str, base_dir: str = '.ocp') -> Any
```

- **Returns:** `Any`

#### `load`

```python
def load(self) -> dict
```

- **Returns:** `dict`
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

#### `set_current_task`

```python
def set_current_task(self, task: str) -> None
```

- **Returns:** `None`

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

#### `add_next`

```python
def add_next(self, item: str) -> None
```

- **Returns:** `None`

#### `add_constraint`

```python
def add_constraint(self, constraint: str) -> None
```

- **Returns:** `None`

#### `set_def`

```python
def set_def(self, key: str, value: Any) -> None
```

- **Returns:** `None`

#### `apply_patch`

```python
def apply_patch(self, patch: dict) -> None
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
def from_wire(cls, wire_text: str, session_id: str, base_dir: str = '.ocp') -> 'StateManager'
```

- **Returns:** `'StateManager'`
- **Description:** Parse a STATE_JSON wire section back into a StateManager.

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
def stats(self) -> dict
```

- **Returns:** `dict`

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
def start(self, blocking: bool = True) -> Any
```

- **Returns:** `Any`
- **Description:** Start the file watcher background thread.

#### `stop`

```python
def stop(self) -> Any
```

- **Returns:** `Any`
- **Description:** Stop the file watcher and clean up resources.

#### `backfill`

```python
def backfill(self, paths: Optional[list[Path]] = None) -> Any
```

- **Returns:** `Any`
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
def __init__(self, db_path: str = 'telemetry.db') -> Any
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
def list_pricing(self, version: Optional[str] = None) -> List[dict]
```

- **Returns:** `List[dict]`
- **Description:** List all pricing entries, optionally filtered by version.

#### `add_pricing`

```python
def add_pricing(self, provider: str, model: str, input_rate: float, output_rate: float, version: Optional[str] = None, effective_date: Optional[str] = None, source: str = 'official') -> int
```

- **Returns:** `int`
- **Description:** Insert a new pricing record. Returns the new row id.

#### `reprocess_costs`

```python
def reprocess_costs(self, from_date: str, to_date: str, pricing_version: Optional[str] = None) -> dict
```

- **Returns:** `dict`
- **Description:** Recalculate costs for events in a date range.

### `tokenpak.telemetry.cost.CostResult`

**Bases:** object

Result of a cost calculation for a single event.

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

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
def to_dict(self) -> dict
```

- **Returns:** `dict`

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
def __init__(self, db_path: str = 'telemetry.db', thresholds: Optional[dict] = None) -> Any
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

### `tokenpak.telemetry.integrity.anomalies.AnomalyDetector`

**Bases:** object

Detects anomalies in telemetry data.

#### `__init__`

```python
def __init__(self, db_path: str) -> Any
```

- **Returns:** `Any`

#### `detect_token_spikes`

```python
def detect_token_spikes(self, model: str, current_tokens: int, baseline_days: int = 7) -> Optional[Anomaly]
```

- **Returns:** `Optional[Anomaly]`
- **Description:** Detect >10× token usage vs baseline.

#### `detect_cost_spikes`

```python
def detect_cost_spikes(self, current_cost: float, baseline_days: int = 1) -> Optional[Anomaly]
```

- **Returns:** `Optional[Anomaly]`
- **Description:** Detect >10× daily cost spike.

#### `detect_retry_surge`

```python
def detect_retry_surge(self, time_window_minutes: int = 60, threshold_pct: float = 20.0) -> Optional[Anomaly]
```

- **Returns:** `Optional[Anomaly]`
- **Description:** Detect retry rate >20% in window.

#### `detect_error_surge`

```python
def detect_error_surge(self, time_window_minutes: int = 60, threshold_pct: float = 10.0) -> Optional[Anomaly]
```

- **Returns:** `Optional[Anomaly]`
- **Description:** Detect error rate >10% in window.

#### `record_anomaly`

```python
def record_anomaly(self, anomaly: Anomaly) -> int | None
```

- **Returns:** `int | None`
- **Description:** Record detected anomaly in database.

#### `get_recent_anomalies`

```python
def get_recent_anomalies(self, since: str | None = None, limit: int = 50) -> List[Dict]
```

- **Returns:** `List[Dict]`
- **Description:** Get recent anomalies.

#### `acknowledge_anomaly`

```python
def acknowledge_anomaly(self, anomaly_id: int) -> bool
```

- **Returns:** `bool`
- **Description:** Mark anomaly as acknowledged.

### `tokenpak.telemetry.integrity.reconciliation.ReconciliationManager`

**Bases:** object

Manages reconciliation of proxy vs billed tokens.

#### `__init__`

```python
def __init__(self, db_path: str) -> Any
```

- **Returns:** `Any`

#### `import_billing_data`

```python
def import_billing_data(self, records: List[Dict]) -> int
```

- **Returns:** `int`
- **Description:** Import billing data for reconciliation.

#### `get_reconciliation_status`

```python
def get_reconciliation_status(self) -> Dict
```

- **Returns:** `Dict`
- **Description:** Get reconciliation rate and summary stats.

#### `get_mismatches`

```python
def get_mismatches(self, limit: int = 10) -> List[Dict]
```

- **Returns:** `List[Dict]`
- **Description:** Get recent mismatches.

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

### `tokenpak.telemetry.operational.health.HealthChecker`

**Bases:** object

Performs health checks on components.

#### `__init__`

```python
def __init__(self, db_path: str, version: str = '0.1.0') -> Any
```

- **Returns:** `Any`

#### `check_database`

```python
def check_database(self) -> tuple[str, Optional[str]]
```

- **Returns:** `tuple[str, Optional[str]]`
- **Description:** Check database connectivity and health.

#### `check_pricing_catalog`

```python
def check_pricing_catalog(self) -> tuple[str, Optional[str]]
```

- **Returns:** `tuple[str, Optional[str]]`
- **Description:** Check pricing catalog (if exists).

#### `check_rollup_job`

```python
def check_rollup_job(self) -> tuple[str, Optional[str]]
```

- **Returns:** `tuple[str, Optional[str]]`
- **Description:** Check rollup job status.

#### `get_stats`

```python
def get_stats(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Get operational statistics.

#### `health_check`

```python
def health_check(self) -> HealthStatus
```

- **Returns:** `HealthStatus`
- **Description:** Run full health check.

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

### `tokenpak.telemetry.operational.pruning.PruneJob`

**Bases:** object

Handles retention and pruning.

#### `__init__`

```python
def __init__(self, db_path: str, config: RetentionConfig) -> Any
```

- **Returns:** `Any`

#### `prune_old_events`

```python
def prune_old_events(self, older_than_days: int) -> int
```

- **Returns:** `int`
- **Description:** Delete events older than N days.

#### `prune_old_rollups`

```python
def prune_old_rollups(self, older_than_days: int) -> int
```

- **Returns:** `int`
- **Description:** Delete rollups older than N days.

#### `vacuum_database`

```python
def vacuum_database(self) -> bool
```

- **Returns:** `bool`
- **Description:** Run VACUUM to reclaim disk space.

#### `run_prune`

```python
def run_prune(self) -> PruneResult
```

- **Returns:** `PruneResult`
- **Description:** Run full prune operation: delete old events/rollups, then vacuum.

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
def __init__(self, version: str, models: dict[str, ModelPricing]) -> None
```

- **Returns:** `None`

#### `load`

```python
def load(cls, path: Optional[os.PathLike] = None) -> 'PricingCatalog'
```

- **Returns:** `'PricingCatalog'`
- **Description:** Load and parse the pricing catalog from *path*.

#### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> 'PricingCatalog'
```

- **Returns:** `'PricingCatalog'`
- **Description:** Construct a catalog from an already-parsed dict (useful in tests).

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

### `tokenpak.telemetry.query.QueryFilter`

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
def compute_daily_rollups(self, date) -> int
```

- **Returns:** `int`
- **Description:** Compute rollups for a specific calendar date. Idempotent.

#### `compute_hourly_rollups`

```python
def compute_hourly_rollups(self, date) -> int
```

- **Returns:** `int`
- **Description:** Compute hourly rollups for a specific date. Idempotent.

#### `rebuild_all_rollups`

```python
def rebuild_all_rollups(self, from_date, to_date) -> dict
```

- **Returns:** `dict`
- **Description:** Rebuild daily rollups for a date range. Returns {dates_processed, total_rows}.

#### `check_consistency`

```python
def check_consistency(self, days: int = 7) -> dict
```

- **Returns:** `dict`
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
def to_dict(self) -> Any
```

- **Returns:** `Any`
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
def to_dict(self) -> Any
```

- **Returns:** `Any`
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
def get_last_with_session(self) -> dict
```

- **Returns:** `dict`
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
- **Description:** Close the underlying database connection.

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
- **Description:** Close the underlying database connection.

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

### `tokenpak.validation.request_validator.RequestValidationResult`

**Bases:** object

Result of a request validation check.

#### `__init__`

```python
def __init__(self, valid: bool, provider: str = 'unknown', errors: Optional[List[Dict[str, Any]]] = None, warnings: Optional[List[Dict[str, Any]]] = None) -> Any
```

- **Returns:** `Any`

#### `to_error_response`

```python
def to_error_response(self, docs_base: str = 'https://docs.tokenpak.dev/api') -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`
- **Description:** Build a structured 400 error body (matches OpenAI/Anthropic style).

#### `to_dict`

```python
def to_dict(self) -> Dict[str, Any]
```

- **Returns:** `Dict[str, Any]`

### `tokenpak.validation.request_validator.RequestValidator`

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

### `tokenpak.validation.validator.ResponseValidator`

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

### `tokenpak.validation.validator.ValidationResult`

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

### `tokenpak.validator.TokenPakValidator`

**Bases:** object

Validates TokenPak packs against the v1.0 protocol spec.

#### `validate`

```python
def validate(self, pack: dict, verbose: bool = False) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Validate a parsed pack dict. Returns a ValidationResult.

#### `validate_file`

```python
def validate_file(self, path: str | Path, verbose: bool = False) -> ValidationResult
```

- **Returns:** `ValidationResult`
- **Description:** Load and validate a JSON file.

### `tokenpak.validator.ValidationIssue`

**Bases:** object

A single validation error or warning.

#### `__init__`

```python
def __init__(self, level: str, field: str, message: str) -> Any
```

- **Returns:** `Any`

#### `to_dict`

```python
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.validator.ValidationResult`

**Bases:** object

Complete result of a pack validation.

#### `__init__`

```python
def __init__(self) -> Any
```

- **Returns:** `Any`

#### `error`

```python
def error(self, field: str, message: str) -> Any
```

- **Returns:** `Any`

#### `warning`

```python
def warning(self, field: str, message: str) -> Any
```

- **Returns:** `Any`

#### `info`

```python
def info(self, field: str, message: str) -> Any
```

- **Returns:** `Any`

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
def to_dict(self) -> dict
```

- **Returns:** `dict`

### `tokenpak.watchdog.CooldownManager`

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
def __init__(self, cooldowns_file: Path = COOLDOWNS_FILE) -> Any
```

- **Returns:** `Any`

#### `clear_expired`

```python
def clear_expired(self) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Clear cooldowns where cooldownUntil < now. Returns list of cleared keys.

#### `check_auth_profiles`

```python
def check_auth_profiles(self) -> List[str]
```

- **Returns:** `List[str]`
- **Description:** Check auth-profiles.json for profiles with cooldownUntil set. Returns warnings.

### `tokenpak.watchdog.ProxyWatchdog`

**Bases:** object

Monitor and auto-heal proxy process.

#### `__init__`

```python
def __init__(self) -> Any
```

- **Returns:** `Any`

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
def check_memory_usage(self) -> Any
```

- **Returns:** `Any`
- **Description:** Warn if proxy memory exceeds 500MB.

#### `check_error_rate`

```python
def check_error_rate(self) -> Any
```

- **Returns:** `Any`
- **Description:** Warn if proxy error rate in session is high.

#### `clear_cooldowns`

```python
def clear_cooldowns(self) -> Any
```

- **Returns:** `Any`
- **Description:** Clear any expired cooldowns from state files.

#### `log_stats`

```python
def log_stats(self) -> Any
```

- **Returns:** `Any`
- **Description:** Log summary stats every hour.

#### `run`

```python
def run(self) -> Any
```

- **Returns:** `Any`
- **Description:** Main watchdog loop.
