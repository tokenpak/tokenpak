# TokenPak — npm SDK

> Deterministic context compression for LLMs. Save 30–60% on token costs automatically.

[![npm version](https://badge.fury.io/js/tokenpak.svg)](https://www.npmjs.com/package/tokenpak)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## Installation

```bash
npm install tokenpak
# or
yarn add tokenpak
# or
pnpm add tokenpak
```

## Requirements

- Node.js ≥ 18

Works with any LLM client (OpenAI SDK, Anthropic SDK, LangChain, etc.) — no proxy required.

**Optional:** Run a local [TokenPak server](https://github.com/tokenpak/tokenpak) for advanced caching and analytics:
```bash
pip install tokenpak
tokenpak serve --port 8000
```

---

## Development Setup

```bash
cd sdk/
npm install --include=dev
npm run build
npm test
```

> **Note:** Binaries (jest, tsc) install to `~/.npm-global/bin/` if your `~/.npmrc` sets a global prefix.
> The `npm run build` script uses `node_modules/.bin/tsc` (always available after `npm ci`).
> The `npm test` script is pre-configured to find jest correctly.

---

## Quick Start

### Compress a single prompt

```typescript
import { CompressionEngine } from 'tokenpak';

const engine = new CompressionEngine(); // connects to http://localhost:8000

const result = await engine.compress(`
  Here is a very long document that needs to be summarized...
  [thousands of tokens of context]
`);

console.log(`Original: ${result.originalTokens} tokens`);
console.log(`Compressed: ${result.compressedTokens} tokens`);
console.log(`Saved: ${result.savingsPct.toFixed(1)}%`);

// Use the compressed text with any LLM
const response = await openai.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: result.compressedText }],
});
```

### Compress a conversation history

```typescript
import { CompressionEngine } from 'tokenpak';

const engine = new CompressionEngine();

const messages = [
  { role: 'system', content: 'You are a helpful assistant.' },
  { role: 'user', content: 'Earlier in our conversation we discussed...' },
  // ... many more messages
  { role: 'user', content: 'What was the conclusion?' }, // latest — keep intact
];

const { messages: compressed } = await engine.compressConversation(messages, {
  keepRecent: 3,       // preserve last 3 messages
  targetTokens: 4000,  // target budget
});

const response = await openai.chat.completions.create({
  model: 'gpt-4o',
  messages: compressed,
});
```

---

## API Reference

### `CompressionEngine`

```typescript
const engine = new CompressionEngine(config?: TokenPakConfig);

// Compress text
const result = await engine.compress(text: string, options?: CompressOptions): Promise<CompressResult>

// Compress conversation history
const result = await engine.compressConversation(
  messages: ConversationMessage[],
  options?: ConversationCompressOptions
): Promise<ConversationCompressResult>
```

**`CompressOptions`**:
| Field | Type | Default | Description |
|---|---|---|---|
| `targetTokens` | `number` | — | Target token count |
| `strategy` | `'heuristic' \| 'semantic' \| 'aggressive' \| 'conservative'` | `'heuristic'` | Compression strategy |
| `cache` | `boolean` | `true` | Use cache for repeated inputs |
| `preserveCode` | `boolean` | `true` | Preserve code blocks intact |
| `preserveStructure` | `boolean` | `true` | Preserve headers and structure |

**`CompressResult`**:
```typescript
{
  originalText: string;
  compressedText: string;
  originalTokens: number;
  compressedTokens: number;
  savingsPct: number;      // 0–100
  cacheHit: boolean;
  elapsedMs: number;
}
```

---

### `CacheManager`

```typescript
const cache = new CacheManager(config?: TokenPakConfig);

await cache.set('my-key', 'my-value', ttl: 300); // 5 min TTL
const value = await cache.get('my-key');          // string | null
await cache.delete('my-key');
await cache.clear();
const stats = await cache.stats(); // CacheStats
```

---

### `BlockRegistry`

```typescript
const registry = new BlockRegistry(config?: TokenPakConfig);

// Register a named content block
await registry.register({
  id: 'system-v1',
  type: 'system',
  content: 'You are a helpful assistant...',
  metadata: { version: 1 },
});

const block = await registry.get('system-v1');
const blocks = await registry.list(type?: string);
await registry.delete('system-v1');
const stats = await registry.stats();
```

---

### `TelemetryCollector`

```typescript
const telemetry = new TelemetryCollector(config?: TokenPakConfig);

// Record an event
await telemetry.record({
  eventType: 'completion',
  data: { tokensIn: 1200, tokensOut: 350, costUsd: 0.0042 },
  model: 'gpt-4o',
});

const events = await telemetry.list(limit: 100, model?: string);
const stats = await telemetry.stats(); // TelemetryStats
await telemetry.reset();
```

---

## Configuration

```typescript
const config: TokenPakConfig = {
  baseUrl: 'http://localhost:8000',  // TokenPak server URL
  timeout: 30_000,                   // Request timeout (ms)
  apiKey: 'your-api-key',            // Optional auth
  headers: { 'X-Custom': 'value' }, // Extra headers
};

const engine = new CompressionEngine(config);
```

---

## Error Handling

```typescript
import {
  CompressionEngine,
  TokenPakError,
  TokenPakConnectionError,
  TokenPakTimeoutError,
} from 'tokenpak';

const engine = new CompressionEngine();

try {
  const result = await engine.compress(text);
} catch (err) {
  if (err instanceof TokenPakConnectionError) {
    console.error('TokenPak server is not running:', err.message);
    // Fall back to uncompressed text
  } else if (err instanceof TokenPakTimeoutError) {
    console.error('Request timed out');
  } else if (err instanceof TokenPakError) {
    console.error(`TokenPak error (${err.statusCode}):`, err.message);
  }
}
```

---

## TypeScript Support

Full TypeScript support included — no `@types/` package needed.

```typescript
import type {
  CompressResult,
  ConversationMessage,
  TelemetryStats,
  TokenPakConfig,
} from 'tokenpak';
```

---

## Links

- [Full documentation](https://github.com/tokenpak/tokenpak/blob/main/README.md)
- [TPK Protocol spec](https://github.com/tokenpak/tokenpak)
- [Python package (PyPI)](https://pypi.org/project/tokenpak/)
- [Issue tracker](https://github.com/tokenpak/tokenpak/issues)

## License

Apache-2.0 — see [LICENSE](../LICENSE)
