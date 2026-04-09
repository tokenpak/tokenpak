# TokenPak Pro — Routing Layer

Multi-provider request routing with automatic failover, cost tracking, and provider detection.

## Features

- **Provider Detection** — Auto-detect provider from API keys, model names, or request headers
- **Adapter Registry** — Load and manage adapters for 5+ providers
- **Failover Handling** — Automatic retry with exponential backoff across providers
- **Cost Tracking** — Per-provider cost tracking with detailed summaries
- **Configuration** — JSON-based routing config with priority-based provider selection

## Quick Start

### Initialize Router

```python
from tokenpak.pro.routing import ProviderRouter, RoutingConfig
from tokenpak.pro.routing.detector import Provider

config = RoutingConfig(
    primary_provider=Provider.ANTHROPIC,
    fallback_providers=[Provider.OPENAI, Provider.GOOGLE],
    cost_tracking=True,
    auto_detect=True,
)

router = ProviderRouter(config)
router.initialize_adapters({
    Provider.ANTHROPIC: anthropic_adapter,
    Provider.OPENAI: openai_adapter,
    Provider.GOOGLE: google_adapter,
})
```

### Route Requests

```python
# Synchronous
result = router.route_request_sync(
    request_func=my_request_function,
    provider=Provider.ANTHROPIC,
    model="claude-3-opus",
)

# Asynchronous
result = await router.route_request(
    request_func=my_async_request_function,
    provider=Provider.ANTHROPIC,
    model="claude-3-opus",
)

# With auto-detection
result = router.route_request_sync(
    request_func=my_request_function,
    api_key="sk-ant-...",  # Auto-detect Anthropic
)
```

### Track Costs

```python
summary = router.get_cost_summary()
print(summary)
# {
#   "total_cost": 1.234,
#   "by_provider": {
#     "anthropic": {"total_cost": 0.5, "request_count": 10, ...},
#     "openai": {"total_cost": 0.734, "request_count": 8, ...}
#   }
# }
```

## Configuration

### RoutingConfig Schema

```python
RoutingConfig(
    primary_provider: Optional[Provider] = None,        # Primary provider to try first
    fallback_providers: Optional[List[Provider]] = None, # Fallback order
    adapter_configs: Optional[Dict[str, str]] = None,   # Custom adapter paths
    cost_tracking: bool = True,                         # Enable cost tracking
    auto_detect: bool = True,                           # Auto-detect provider from request
    max_retries: int = 3,                               # Retries per adapter
    timeout: float = 30.0,                              # Request timeout (seconds)
)
```

### Example: JSON Config

```json
{
  "primary_provider": "anthropic",
  "fallback_providers": ["openai", "google"],
  "cost_tracking": true,
  "auto_detect": true,
  "max_retries": 3,
  "timeout": 30.0,
  "adapter_configs": {
    "anthropic": "tokenpak.adapters.anthropic.AnthropicAdapter",
    "openai": "tokenpak.adapters.openai.OpenAIAdapter"
  }
}
```

## Provider Detection

### Detection Methods (in priority order)

1. **API Key Format**
   - Anthropic: `sk-ant-*`
   - OpenAI: `sk-*`
   - Google: `AIzaSy*`
   - Bedrock: `*@bedrock`
   - LiteLLM: `litellm-*`

2. **Model Name Pattern**
   - Anthropic: `claude-*`
   - OpenAI: `gpt-*`, `text-*`
   - Google: `gemini-*`, `palm-*`
   - Bedrock: `anthropic.*`, `amazon.*`

3. **Request Headers**
   - `Authorization: Bearer <key>`
   - `X-API-Key: <key>`
   - Provider-specific headers (`anthropic-version`, `openai-organization`, etc.)

### Example: Auto-Detection

```python
# Detect from API key
result = router.route_request_sync(
    request_func,
    api_key="sk-ant-...",
)

# Detect from model name
result = router.route_request_sync(
    request_func,
    model="gpt-4-turbo",
)

# Detect from headers
result = router.route_request_sync(
    request_func,
    headers={"anthropic-version": "2023-06-01"},
)
```

## Failover Strategy

When a request fails:

1. **Retry** the same adapter up to `max_retries` times with exponential backoff
2. **Fallover** to next provider in list
3. **Repeat** retry sequence for each fallback provider
4. **Raise** RuntimeError if all providers exhausted

### Backoff Calculation

```
delay = backoff_factor ^ retry_count

Examples (backoff_factor=1.5):
- Retry 1: 1.5^1 = 1.5s
- Retry 2: 1.5^2 = 2.25s
- Retry 3: 1.5^3 = 3.375s
```

### Example: Custom Failover

```python
from tokenpak.pro.routing import FailoverHandler
from tokenpak.pro.routing.detector import Provider

handler = FailoverHandler(
    adapters=[Provider.ANTHROPIC, Provider.OPENAI, Provider.GOOGLE],
    max_retries=3,
    timeout=30.0,
    backoff_factor=2.0,  # Exponential backoff
)

result = handler.execute_with_failover_sync(
    request_func,
    adapter_map={...},
)
```

## Cost Tracking

### Tracking Requests

```python
tracker = router.cost_tracker

entry = tracker.track_request(
    provider="anthropic",
    model="claude-3-opus",
    input_tokens=1000,
    output_tokens=500,
    input_cost=0.001,
    output_cost=0.0015,
    request_cost=0.0,
    status="success",
    metadata={"request_id": "req-123"},
)
```

### Cost Summary

```python
summary = tracker.get_all_summaries()
for provider, stats in summary.items():
    print(f"{provider}:")
    print(f"  Total: ${stats.total_cost:.2f}")
    print(f"  Requests: {stats.request_count}")
    print(f"  Avg/Request: ${stats.avg_cost_per_request:.4f}")
```

### Filtering & Export

```python
# Filter by status
errors = tracker.get_entries_by_status("error")

# Filter by model
claude_costs = tracker.get_entries_by_model("claude-3-opus")

# Export as JSON
json_data = tracker.export_summaries()
```

## Classes

### ProviderRouter

Main orchestrator for multi-provider routing.

**Methods:**
- `__init__(config: RoutingConfig)`
- `initialize_adapters(adapters: Dict[Provider, Any])`
- `route_request(request_func, provider=None, ...)`
- `route_request_sync(request_func, provider=None, ...)`
- `get_cost_summary() -> Optional[dict]`
- `get_config() -> dict`

### ProviderDetector

Auto-detects provider from API keys, model names, or headers.

**Methods:**
- `detect_from_key(api_key: str) -> Optional[Provider]`
- `detect_from_model(model: str) -> Optional[Provider]`
- `detect_from_headers(headers: dict) -> Optional[Provider]`
- `detect(api_key=None, model=None, headers=None) -> Tuple[Optional[Provider], str]`

### AdapterRegistry

Loads and manages adapters for different providers.

**Methods:**
- `register_adapter(provider: Provider, module_path: str)`
- `load_adapter_class(provider: Provider) -> Type`
- `create_adapter(provider: Provider, config=None) -> Any`
- `get_adapter(provider: Provider) -> Any`
- `has_adapter(provider: Provider) -> bool`
- `list_providers() -> list`
- `clear_cache()`

### FailoverHandler

Manages failover and retry logic.

**Methods:**
- `__init__(adapters, max_retries=3, timeout=30.0, backoff_factor=1.5)`
- `execute_with_failover(request_func, adapter_map, ...)`
- `execute_with_failover_sync(request_func, adapter_map, ...)`
- `get_backoff_delay(provider) -> float`
- `should_retry(provider) -> bool`

### CostTracker

Tracks costs per provider and request.

**Methods:**
- `track_request(provider, input_tokens=0, output_tokens=0, ...) -> CostEntry`
- `get_provider_summary(provider) -> Optional[ProviderCostSummary]`
- `get_all_summaries() -> Dict[str, ProviderCostSummary]`
- `get_total_cost() -> float`
- `get_entries_by_provider(provider) -> list`
- `get_entries_by_status(status) -> list`
- `get_entries_by_model(model) -> list`
- `get_cost_by_period(start_time, end_time) -> Dict[str, float]`
- `export_entries() -> str`
- `export_summaries() -> str`

## Integration (Phase 2)

This routing layer will be integrated with:

1. **API Gateway** — Route requests based on request context
2. **Model Router** — Select provider based on model availability
3. **Load Balancer** — Distribute load across providers
4. **Cost Optimizer** — Dynamically adjust routing for cost efficiency

## Testing

Run full test suite:

```bash
pytest tests/test_routing_*.py -v --cov=tokenpak.pro.routing
```

Test coverage report:

```bash
pytest tests/test_routing_*.py --cov=tokenpak.pro.routing --cov-report=html
```

Expected coverage: **≥85%**

## Files

```
tokenpak/pro/
├── __init__.py
└── routing/
    ├── __init__.py                 # Main exports
    ├── detector.py                 # Provider detection
    ├── registry.py                 # Adapter registry
    ├── failover.py                 # Failover handler
    ├── costs.py                    # Cost tracking
    ├── router.py                   # Main router orchestrator
    └── README.md                   # This file

tests/
├── test_routing_detector.py        # 20+ tests
├── test_routing_registry.py        # 15+ tests
├── test_routing_failover.py        # 20+ tests
├── test_routing_costs.py           # 30+ tests
└── test_routing_router.py          # 20+ tests
```

## Roadmap

- [ ] Phase 2: API Gateway integration
- [ ] Phase 3: Model availability matrix
- [ ] Phase 4: Advanced load balancing
- [ ] Phase 5: Cost optimization strategies
