# TokenPak Configuration Reference

All TokenPak proxy settings are controlled via environment variables. This is the single authoritative reference.

Set variables in your shell, `.env` file, or `~/.openclaw/.env` (OpenClaw fleet standard).

---

## API Keys

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ Yes (if using Anthropic) | Anthropic API key |
| `OPENAI_API_KEY` | ✅ Yes (if using OpenAI) | OpenAI API key |
| `GOOGLE_API_KEY` or `GEMINI_API_KEY` | ✅ Yes (if using Google) | Google / Gemini API key |

At least one API key is required. The proxy auto-detects which provider to use based on the model name.

---

## Server

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_PORT` | `8766` | int | Port the proxy listens on |
| `TOKENPAK_LISTEN_ADDRESS` | `0.0.0.0` | str | Bind address |
| `TOKENPAK_UPSTREAM_TIMEOUT` | `300` | int | Upstream request timeout (seconds) |
| `TOKENPAK_MAX_REQUEST_SIZE` | `10485760` | int | Max request body size (bytes, default 10MB) |
| `TOKENPAK_RATE_LIMIT_RPM` | `60` | int | Rate limit (requests per minute per IP) |
| `TOKENPAK_DB` | `<proxy_dir>/monitor.db` | str | Path to SQLite monitoring database |
| `TOKENPAK_WS_PORT` | `8767` | int | WebSocket telemetry port |
| `TOKENPAK_WS_MAX_CONNECTIONS` | `50` | int | Max WebSocket connections |

---

## Compression & Compaction

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_COMPACT` | `1` (true) | bool | Enable request compaction (token savings) |
| `TOKENPAK_COMPACT_MAX_CHARS` | `120` | int | Max chars for compressed text snippets |
| `TOKENPAK_COMPACT_THRESHOLD_TOKENS` | `4500` | int | Skip compaction below this token count |
| `TOKENPAK_COMPACT_MAX_TOKENS` | `50000` | int | Skip compaction above this token count (set 0 to disable cap) |
| `TOKENPAK_COMPACT_CACHE_SIZE` | `2000` | int | LRU cache size for compaction results |
| `TOKENPAK_MODE` | `hybrid` | str | Compilation mode: `hybrid`, `strict`, `passthrough` |
| `TOKENPAK_COMPRESSION_DICT` | `0` (false) | bool | Enable post-compaction dictionary compression |

---

## Vault Injection

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_INJECT_BUDGET` | `4000` | int | Max tokens to inject from vault per request |
| `TOKENPAK_INJECT_TOP_K` | `5` | int | Max vault blocks to inject |
| `TOKENPAK_INJECT_MIN_SCORE` | `2.0` | float | Minimum relevance score for injection |
| `TOKENPAK_INJECT_SKIP_MODELS` | `haiku` | str | Comma-separated models to skip vault injection |
| `TOKENPAK_INJECT_MIN_PROMPT` | `1000` | int | Skip injection if prompt is shorter than this (chars) |
| `VAULT_INDEX_PATH` | `~/vault/.tokenpak/index.json` | str | Path to vault index JSON |

---

## Caching

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_SEMANTIC_CACHE` | `1` (true) | bool | Enable semantic (prompt-hash) cache |
| `TOKENPAK_CACHE_REGISTRY` | `1` (true) | bool | Enable cache registry |

---

## Routing

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_ROUTER_ENABLED` | `1` (true) | bool | Enable DeterministicRouter (intent classification) |
| `TOKENPAK_SALIENCE_ROUTER` | `0` (false) | bool | Enable salience-based content extraction before compaction |
| `TOKENPAK_RETRIEVAL_BACKEND` | `bm25` | str | Vault retrieval backend: `bm25`, `exact` |

---

## Features (Feature Flags)

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_SKELETON_ENABLED` | `1` (true) | bool | Enable skeleton extraction |
| `TOKENPAK_SHADOW_ENABLED` | `1` (true) | bool | Enable shadow reader validation |
| `TOKENPAK_BUDGET_TOTAL` | `12000` | int | Total token budget per request |
| `TOKENPAK_CHAT_FOOTER` | `0` (false) | bool | Inject usage stats footer into assistant responses |
| `TOKENPAK_TRACE` | `0` (false) | bool | Enable per-request trace side-channel |
| `TOKENPAK_STRICT_MODE` | `0` (false) | bool | Enable strict validation mode |
| `TOKENPAK_CAPSULE_BUILDER` | `0` (false) | bool | Enable capsule builder (session compression) |
| `TOKENPAK_SESSION_CAPSULES` | `0` (false) | bool | Enable session capsule storage |

---

## Advanced Features

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_ERROR_NORMALIZER` | `0` (false) | bool | Normalize upstream error messages |
| `TOKENPAK_BUDGET_CONTROLLER` | `0` (false) | bool | Enable budget controller stage |
| `TOKENPAK_REQUEST_LOGGER` | `0` (false) | bool | Enable detailed request logging |
| `TOKENPAK_RETRIEVAL_WATCHDOG` | `0` (false) | bool | Enable retrieval watchdog |
| `TOKENPAK_FAILURE_MEMORY` | `0` (false) | bool | Enable failure memory (avoid known bad patterns) |
| `TOKENPAK_FIDELITY_TIERS` | `0` (false) | bool | Enable fidelity tier scoring |
| `TOKENPAK_PRECONDITION_GATES` | `0` (false) | bool | Enable precondition gates |
| `TOKENPAK_QUERY_REWRITER` | `0` (false) | bool | Enable query rewriting |
| `TOKENPAK_STABILITY_SCORER` | `0` (false) | bool | Enable stability scoring |
| `TOKENPAK_PREFIX_REGISTRY` | `0` (false) | bool | Enable prefix registry |
| `TOKENPAK_TERM_RESOLVER` | `1` (true) | bool | Enable term resolver (concept card injection) |
| `TOKENPAK_TERM_RESOLVER_TOP_K` | `3` | int | Max term cards to inject |
| `TOKENPAK_TERM_RESOLVER_MAX_BYTES` | `200` | int | Max bytes per term card |

---

## Validation Gate

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_VALIDATION_GATE` | `1` (true) | bool | Enable pre-forward validation gate |
| `TOKENPAK_VALIDATION_BUDGET_CAP` | `20000` | int | Validation gate token budget cap |
| `TOKENPAK_VALIDATION_SOFT` | `0` (false) | bool | Soft mode — warn instead of block on validation failure |
| `TOKENPAK_SKIP_GATE` | _(unset)_ | str | Set to `1` to bypass validation gate entirely (testing) |

---

## Dashboard

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_DASHBOARD_AUTH` | `1` (true) | bool | Require auth token for dashboard access |

---

## Capsule Builder

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_CAPSULE_MIN_CHARS` | `400` | int | Minimum chars to trigger capsule builder |
| `TOKENPAK_CAPSULE_HOT_WINDOW` | `2` | int | Trailing messages excluded from capsule compression |

---

## Ollama

| Variable | Default | Type | Description |
|---|---|---|---|
| `TOKENPAK_OLLAMA_UPSTREAM` | `http://localhost:11434` | str | Ollama server URL |
| `TOKENPAK_OLLAMA_TIMEOUT` | `20` | int | Ollama connect timeout (seconds) |

---

## Tips

**Minimal production config:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export TOKENPAK_PORT=8766
python3 proxy.py
```

**Disable compaction (pure proxy mode):**
```bash
export TOKENPAK_COMPACT=0
export TOKENPAK_MODE=passthrough
```

**Debug mode (verbose tracing):**
```bash
export TOKENPAK_TRACE=1
export TOKENPAK_REQUEST_LOGGER=1
```

---

*See [Getting Started](getting-started.md) for setup. See [proxy.py](https://github.com/tokenpak/tokenpak/blob/main/packages/core/tokenpak/proxy.py) for the source of truth (search for `_cfg(`).*
