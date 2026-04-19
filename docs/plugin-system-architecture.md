# TokenPak Plugin System Architecture — v1 Proposal

**Status:** Draft for Sue review  
**Author:** Trix  
**Date:** 2026-03-26  
**Task:** p2-tokenpak-plugin-system-foundations-2026-03-26

---

## Overview

TokenPak already has a narrow `CompressorPlugin` system (text→text transforms). This proposal expands that into a full extensibility layer covering adapters, compression, metrics collection, and lifecycle hooks — without touching the existing `CompressorPlugin` contract.

The goal: users can drop plugins into `~/.tokenpak/plugins/` (or declare them in `config.yaml`) and extend TokenPak behavior without modifying core.

---

## Plugin Directory Structure

```
~/.tokenpak/
└── plugins/
    ├── my-adapter/
    │   ├── plugin.yaml          # Manifest (required)
    │   └── my_adapter.py        # Entry point
    ├── my-compressor/
    │   ├── plugin.yaml
    │   └── my_compressor.py
    └── my-metrics/
        ├── plugin.yaml
        └── my_metrics.py
```

**Discovery order (highest priority first):**
1. `TOKENPAK_PLUGINS` env var (comma-separated `module.ClassName` paths) — dev/testing
2. `config.yaml` → `plugins.enabled` list — canonical prod config
3. `~/.tokenpak/plugins/*/plugin.yaml` filesystem scan — drop-in install

---

## Plugin Manifest Spec (`plugin.yaml`)

```yaml
# plugin.yaml — required fields
name: my-custom-adapter          # Unique slug (lowercase, hyphens ok)
version: "1.0.0"                 # SemVer string
kind: adapter                    # adapter | compressor | metrics

# optional
description: "Translates MyAPI format to canonical"
author: "Kevin Yang"
min_tokenpak_version: "0.9.0"   # Semver range, defaults to any

# Hook declarations (what lifecycle events does this plugin use?)
hooks:
  - on_startup
  - on_request
  - on_response
  - on_error
  - on_shutdown

# Config schema (jsonschema subset)
config_schema:
  type: object
  properties:
    timeout_ms:
      type: integer
      default: 5000
    upstream_url:
      type: string
  required: []

# Permission declarations (explicit capabilities)
permissions:
  - network_outbound    # may make HTTP calls
  - filesystem_read     # may read local files
  # filesystem_write    # may write files (not declared = denied)
  # subprocess          # may spawn processes
```

**Validation rules:**
- `name`, `version`, `kind` are required
- `kind` must be one of: `adapter`, `compressor`, `metrics`
- `hooks` must be a subset of the defined hook points
- `permissions` must be from the allowed set — unlisted perms are denied at runtime

---

## Plugin Types & Base Classes

### 1. Adapter Plugin (`kind: adapter`)

Extends `FormatAdapter` (the existing proxy adapter contract). Allows third-party providers (e.g., Bedrock, Groq, custom APIs) without PRs to core.

```python
# tokenpak/plugins/base.py (extended)
class AdapterPlugin(FormatAdapter):
    """Base for format adapter plugins.
    
    Lifecycle: on_startup → on_request → on_response → on_shutdown
                                       ↘ on_error (on exception)
    """
    name: str = ""          # Must match plugin.yaml name
    version: str = "0.0.0"

    # Inherited from FormatAdapter:
    # detect(path, headers, body) → bool
    # normalize(body) → CanonicalRequest
    # denormalize(canonical) → bytes
    # get_default_upstream() → str
```

### 2. Compressor Plugin (`kind: compressor`)

Extends the existing `CompressorPlugin`. No changes needed — existing API is the spec.

```python
class CompressorPlugin(ABC):
    name: str = ""
    def compress(self, text: str, context: dict) -> dict: ...
    def priority(self) -> int: return 50
```

### 3. Metrics Plugin (`kind: metrics`)

Receives telemetry events and can forward them to external systems (Prometheus, Datadog, custom).

```python
class MetricsPlugin(ABC):
    name: str = ""
    
    def on_request_complete(self, event: MetricsEvent) -> None: ...
    def on_cache_hit(self, event: MetricsEvent) -> None: ...
    def on_error(self, event: MetricsEvent) -> None: ...
    def flush(self) -> None: ...  # Called on shutdown
```

---

## Lifecycle Hook Points

All plugin kinds share a common lifecycle. Hook names match `plugin.yaml` declarations.

```
Proxy startup
    │
    └─► on_startup(config: dict) → None
            Initialize resources (connections, caches, counters)

Incoming request
    │
    └─► on_request(ctx: RequestContext) → RequestContext | None
            Inspect/modify request before routing
            Return None to pass-through unchanged

Upstream response received
    │
    └─► on_response(ctx: ResponseContext) → ResponseContext | None
            Inspect/modify response before returning to client

Any exception during request/response
    │
    └─► on_error(ctx: ErrorContext) → None
            Log, alert, or increment counters (cannot recover)

Proxy shutdown
    │
    └─► on_shutdown() → None
            Flush buffers, close connections, persist state
```

### Context Types

```python
@dataclass
class RequestContext:
    request_id: str
    path: str
    headers: dict
    body: bytes
    provider: str         # detected provider name
    model: str
    metadata: dict        # mutable — plugins can add keys

@dataclass  
class ResponseContext:
    request_id: str
    status_code: int
    body: bytes
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    metadata: dict        # carries plugin data from on_request phase

@dataclass
class ErrorContext:
    request_id: str
    exception: Exception
    phase: str            # "request" | "response" | "routing"
    metadata: dict

@dataclass
class MetricsEvent:
    event_type: str
    timestamp: float
    provider: str
    model: str
    data: dict
```

---

## Safety Boundaries

### 1. Error Isolation

Plugin failures must never crash the proxy. Each hook call is wrapped:

```python
# Pseudocode — actual impl in plugin runner
try:
    result = plugin.on_request(ctx)
except Exception as e:
    logger.warning("Plugin '%s' on_request failed: %s — skipping", plugin.name, e)
    result = None  # fall through, unmodified
```

No plugin exception propagates to the request path.

### 2. Permission Model

Declared in `plugin.yaml` under `permissions`. At load time, the registry:

1. Reads declared permissions
2. Checks against host policy (configurable in `config.yaml` under `plugins.allowed_permissions`)
3. **Logs a warning** if plugin requests a perm not in allowed list
4. **Refuses to load** if plugin requests `subprocess` or `filesystem_write` and those aren't explicitly allowed

Default allowed permissions: `network_outbound`, `filesystem_read`  
Default denied: `filesystem_write`, `subprocess`

### 3. No Subprocess / Exec

Plugin base classes do not provide subprocess utilities. The manifest `subprocess` permission gates the ability to declare intent, but there's no enforcement at runtime in v1 — this is documentation + audit trail. (v2: use `restrictedpython` or subprocess patching.)

### 4. Plugin Sandboxing (v1 scope)

v1: **trust-based** — plugins run in the same process, same Python interpreter. No memory/CPU isolation. This is acceptable for self-hosted use (personal/team deployments). OSS launch doc will state: "Only install plugins you trust."

v2 (post-launch): subprocess isolation via `multiprocessing` workers with timeout + memory cap.

### 5. Name Collision

Registry raises `ValueError` on duplicate `name`. Load fails loudly — no silent shadowing.

### 6. Config Schema Validation

At load time, the registry validates plugin config against `config_schema` using jsonschema. Plugin won't load if config is invalid.

---

## Plugin Architecture Flowchart

```
                    ┌──────────────────────────────────┐
                    │         Plugin Registry           │
                    │                                   │
  startup ─────────►│  1. Scan ~/.tokenpak/plugins/     │
                    │  2. Load plugin.yaml manifests    │
                    │  3. Validate manifests + perms    │
                    │  4. Import entry point module     │
                    │  5. Instantiate + call on_startup │
                    │  6. Register by kind + name       │
                    └──────────┬────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼────┐  ┌───────▼──────┐  ┌──────▼───────┐
    │ AdapterPlugin│  │Compressor    │  │MetricsPlugin │
    │ Registry     │  │Plugin        │  │Registry      │
    │              │  │Registry      │  │              │
    │ detect()     │  │              │  │on_request_   │
    │ normalize()  │  │compress()    │  │  complete()  │
    │ denormalize()│  │priority()    │  │on_cache_hit()│
    │ on_request() │  │              │  │on_error()    │
    │ on_response()│  │              │  │flush()       │
    └──────────────┘  └──────────────┘  └──────────────┘
```

---

## Example Plugin Templates

### Template 1: Custom Adapter (stub)

```python
# ~/.tokenpak/plugins/my-api/my_adapter.py
from tokenpak.plugins.base import AdapterPlugin
from tokenpak.proxy.adapters.canonical import CanonicalRequest

class MyAPIAdapter(AdapterPlugin):
    name = "my-api"
    version = "0.1.0"

    def detect(self, path, headers, body):
        return path.startswith("/v1/my-api/")

    def normalize(self, body: bytes) -> CanonicalRequest:
        # TODO: parse my-api format → CanonicalRequest
        raise NotImplementedError

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        # TODO: CanonicalRequest → my-api format
        raise NotImplementedError

    def get_default_upstream(self) -> str:
        return "https://api.my-service.com"

    def on_request(self, ctx):
        ctx.metadata["my_api_trace"] = ctx.request_id
        return ctx

    def on_response(self, ctx):
        # log latency
        return ctx
```

### Template 2: Custom Compressor (stub)

```python
# ~/.tokenpak/plugins/my-compressor/my_compressor.py
from tokenpak.plugins.base import CompressorPlugin

class MyCompressor(CompressorPlugin):
    name = "my-compressor"

    def compress(self, text: str, context: dict) -> dict:
        # TODO: implement compression logic
        compressed = text  # no-op stub
        return {
            "text": compressed,
            "metadata": {
                "plugin": self.name,
                "original_len": len(text),
                "compressed_len": len(compressed),
            }
        }

    def priority(self) -> int:
        return 75  # runs before default (50), after ultra-high (100)
```

### Template 3: Custom Metrics Forwarder (stub)

```python
# ~/.tokenpak/plugins/my-metrics/my_metrics.py
from tokenpak.plugins.base import MetricsPlugin

class MyMetricsForwarder(MetricsPlugin):
    name = "my-metrics"

    def __init__(self):
        self._buffer = []

    def on_request_complete(self, event):
        self._buffer.append(event)
        if len(self._buffer) >= 100:
            self.flush()

    def on_cache_hit(self, event):
        pass  # not tracked

    def on_error(self, event):
        # forward errors immediately
        self._send([event])

    def flush(self):
        if self._buffer:
            self._send(self._buffer)
            self._buffer = []

    def _send(self, events):
        # TODO: HTTP POST to external system
        pass
```

---

## Integration Test Skeleton

File: `packages/core/tests/test_plugin_system_lifecycle.py`

```python
"""Integration test skeleton for plugin lifecycle.

No assertions yet — structure only. Tests will be completed post-architecture review.
"""

import pytest
from tokenpak.plugins.base import CompressorPlugin, AdapterPlugin, MetricsPlugin
from tokenpak.plugins.registry import PluginRegistry


# ── Stub plugins for lifecycle testing ─────────────────────────────────────

class LifecycleTracker:
    events = []

class TrackedCompressor(CompressorPlugin):
    name = "tracked-compressor"
    def compress(self, text, context):
        LifecycleTracker.events.append("compress")
        return {"text": text, "metadata": {}}


@pytest.fixture(autouse=True)
def clear_tracker():
    LifecycleTracker.events.clear()
    yield


# ── Test stubs ──────────────────────────────────────────────────────────────

def test_plugin_loads_from_manifest(tmp_path):
    """Plugin discovery reads plugin.yaml and instantiates correctly."""
    # TODO: write plugin.yaml + entry point to tmp_path, call registry.discover()
    pass


def test_on_startup_called_for_all_plugins():
    """on_startup() is invoked for each plugin during proxy init."""
    # TODO: mock proxy startup, verify on_startup called on registered plugins
    pass


def test_on_request_hook_receives_context():
    """on_request() receives RequestContext with correct fields."""
    # TODO: build synthetic RequestContext, pass through plugin chain
    pass


def test_on_response_hook_receives_context():
    """on_response() receives ResponseContext with tokens and latency."""
    pass


def test_on_error_hook_called_on_upstream_failure():
    """on_error() is called when upstream returns 500."""
    pass


def test_on_shutdown_called_on_proxy_stop():
    """on_shutdown() is invoked on graceful shutdown."""
    pass


def test_plugin_failure_does_not_crash_request():
    """A plugin that raises in on_request does not propagate the exception."""
    # TODO: register ExplodingPlugin, verify request still succeeds
    pass


def test_name_collision_raises_on_register():
    """Registering two plugins with same name raises ValueError."""
    registry = PluginRegistry()
    registry.register(TrackedCompressor)
    with pytest.raises(ValueError, match="collision"):
        registry.register(TrackedCompressor)


def test_manifest_with_unknown_permission_logs_warning(caplog, tmp_path):
    """Plugin declaring unknown permission logs a warning and still loads."""
    pass


def test_manifest_with_denied_permission_refuses_load(tmp_path):
    """Plugin declaring 'subprocess' without host allowance is rejected."""
    pass


def test_config_schema_validation_rejects_bad_config(tmp_path):
    """Plugin config that fails jsonschema validation prevents load."""
    pass


def test_compressor_plugin_priority_ordering():
    """Plugins are returned highest-priority first."""
    registry = PluginRegistry()
    registry.register(TrackedCompressor)
    plugins = registry.get_plugins()
    priorities = [p.priority() for p in plugins]
    assert priorities == sorted(priorities, reverse=True)


def test_metrics_plugin_flush_called_on_shutdown():
    """MetricsPlugin.flush() is called during proxy shutdown."""
    pass
```

---

## Open Questions for Sue

1. **Sandboxing timeline** — Should v1 doc say "no isolation, trust-based" explicitly, or defer to v1.1?
2. **AdapterPlugin vs FormatAdapter** — Should `AdapterPlugin` extend `FormatAdapter` directly, or compose it? Composition gives cleaner separation but more boilerplate.
3. **Permission enforcement** — v1 is documentation-only (manifest declares intent, no runtime enforcement). Acceptable for OSS launch?
4. **Manifest format** — YAML vs JSON? YAML is friendlier for humans; JSON is easier to validate. Current preference: YAML with jsonschema validation.
5. **`~/.tokenpak/plugins/` default dir** — Confirm this path is correct for the OSS install convention.

---

## Files Touched by This Design

| File | Action |
|------|--------|
| `packages/core/tokenpak/plugins/base.py` | Extend with `AdapterPlugin`, `MetricsPlugin`, context dataclasses |
| `packages/core/tokenpak/plugins/registry.py` | Extend with manifest discovery, permission validation, per-kind registries |
| `packages/core/tokenpak/plugins/examples/passthrough.py` | Existing — no changes |
| `packages/core/tokenpak/plugins/examples/my_adapter.py` | New template |
| `packages/core/tokenpak/plugins/examples/my_compressor.py` | New template |
| `packages/core/tokenpak/plugins/examples/my_metrics.py` | New template |
| `packages/core/tests/test_plugin_system_lifecycle.py` | New integration test skeleton |
| `docs/plugin-system-architecture.md` | This file |

---

*Submitted for architecture review. No implementation yet — pending Sue sign-off on hook signatures, permission model, and sandboxing stance.*
