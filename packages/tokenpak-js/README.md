# tokenpak

**The Context Standard for AI — JavaScript/TypeScript SDK**

[![npm version](https://badge.fury.io/js/tokenpak.svg)](https://www.npmjs.com/package/tokenpak)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-blue)](https://www.typescriptlang.org/)
[![Zero dependencies](https://img.shields.io/badge/dependencies-zero-green)](package.json)

Build, bundle, validate, and transmit structured AI context as [TokenPak](https://tokenpak.dev) — the open protocol for context handoffs between AI systems.

## Features

- ✅ **Full TypeScript** — complete type definitions, zero-runtime overhead
- ✅ **Zero dependencies** — no external packages required at runtime
- ✅ **Browser + Node.js** — works in Cloudflare Workers, Vercel Edge, Electron, React
- ✅ **Protocol-compliant** — implements [TokenPak v1.0 schema](https://tokenpak.dev/schema/v1.0.json)
- ✅ **Budget enforcement** — automatic block dropping by priority when over budget
- ✅ **Round-trip safe** — serialize/deserialize without data loss

## Install

```bash
npm install tokenpak
```

## Quick Start

```typescript
import { TokenPak, Block, Policy, validate } from 'tokenpak';

// 1. Create a pack with a token budget
const pack = new TokenPak({ budget: 8000 });

// 2. Add blocks
pack.add(new Block({
  type: 'instructions',
  id: 'system_prompt',
  content: 'You are a helpful assistant.',
  priority: 'critical',   // Never dropped, even over budget
}));

pack.add(new Block({
  type: 'knowledge',
  id: 'api_docs',
  content: apiDocs,
  priority: 'high',
}));

pack.add(new Block({
  type: 'conversation',
  id: 'recent_history',
  content: conversationHistory,
  priority: 'medium',     // Dropped first if over budget
}));

// 3. Compile (applies budget enforcement)
const compiled = pack.compile();

// 4. Use it
const prompt  = compiled.toPrompt();   // Plain text for LLM injection
const wire    = compiled.toWire();     // JSON string for transport/storage
const json    = compiled.toJSON();     // Raw object

// 5. Validate
const { valid, errors } = validate(json);

// 6. Round-trip
const pack2 = TokenPak.fromWire(wire);
```

## API Reference

### `Block`

The fundamental content unit.

```typescript
const block = new Block({
  type: 'instructions' | 'code' | 'knowledge' | 'memory' |
        'conversation' | 'evidence' | 'system',
  id?: string,          // Auto-generated if omitted
  content: string,
  priority?: 'critical' | 'high' | 'medium' | 'low' | 'internal',
  quality?: number,     // 0.0–1.0 relevance score
  tokens?: number,      // Estimated if omitted
  compacted?: boolean,
  provenance?: {        // For evidence blocks
    source_type?: string,
    url?: string,
    score?: number,
  },
});
```

### `TokenPak`

The main context container.

```typescript
const pack = new TokenPak({
  budget?: number,                // Token budget
  metadata?: { task, source, target?, tags?, expires? },
  policy?: Policy | PolicyOptions,
  id?: string,
});

pack.add(block)                   // Add block (chainable)
pack.remove(id)                   // Remove block by id
pack.get(id)                      // Get block by id
pack.getBlocks(type?)             // Get all/filtered blocks
pack.compile()                    // → CompiledPack
pack.toPrompt()                   // Shortcut: compile().toPrompt()
pack.toWire()                     // Shortcut: compile().toWire()
pack.toJSON()                     // Shortcut: compile().toJSON()
pack.blockCount                   // Number of blocks
pack.totalTokens                  // Sum of all block tokens
pack.remainingBudget              // budget - totalTokens

TokenPak.fromJSON(obj)            // Deserialize from object
TokenPak.fromWire(str)            // Deserialize from JSON string
TokenPak.merge([p1, p2], opts?)   // Merge multiple packs
```

### `CompiledPack`

Output of `pack.compile()`.

```typescript
compiled.toPrompt(opts?)           // Plain text for LLM
compiled.toWire()                  // Compact JSON string
compiled.toWirePretty()            // Pretty-printed JSON string
compiled.toJSON()                  // Raw wire object
compiled.id                        // Pack ID
compiled.blockCount                // Number of blocks
compiled.totalTokens               // Total tokens

CompiledPack.fromWire(str)         // Deserialize
CompiledPack.fromObject(obj)       // Deserialize from object
```

### `Policy`

Compaction and budget policy.

```typescript
const policy = new Policy({
  mode?: 'lossless' | 'balanced' | 'aggressive' | 'semantic',
  maxTokens?: number,
  priorityOrder?: string[],
  perTypeLimits?: Record<string, { max_tokens?, mode?, hot_window? }>,
  budget?: { total?, per_block_max?, reserve_for_output? },
});
```

### `validate(wire)`

Validate against the TokenPak v1.0 schema.

```typescript
const { valid, errors } = validate(compiled.toJSON());
// errors: [{ path: string, message: string }]
```

### Compaction utilities

```typescript
import { applyBudget, sortByPriority, truncateBlock } from 'tokenpak';

const result = applyBudget(blocks, maxTokens, 'balanced');
// → { blocks, tokensRemoved, blocksDropped, mode }

const sorted = sortByPriority(blocks);   // critical first
const trimmed = truncateBlock(block, 500); // truncate to ~500 tokens
```

## Budget Enforcement

When a pack is compiled over budget, blocks are dropped by priority:

| Priority   | Lossless | Balanced | Aggressive |
|------------|----------|----------|------------|
| `critical` | ✅ kept  | ✅ kept  | ✅ kept    |
| `high`     | ✅ kept  | ✅ kept  | ✅ kept    |
| `medium`   | ✅ kept  | ✅ kept  | ❌ dropped |
| `low`      | ✅ kept  | ❌ dropped | ❌ dropped |
| `internal` | ❌ dropped | ❌ dropped | ❌ dropped |

Within a priority tier, **larger blocks are dropped first**.

## Framework Integration

### Vercel AI SDK

```typescript
import { TokenPak, Block } from 'tokenpak';
import { generateText } from 'ai';
import { openai } from '@ai-sdk/openai';

const pack = new TokenPak({ budget: 4000 });
pack.add(new Block({ type: 'instructions', content: systemPrompt, priority: 'critical' }));
pack.add(new Block({ type: 'knowledge', content: retrievedDocs, priority: 'high' }));

const { text } = await generateText({
  model: openai('gpt-4o'),
  system: pack.toPrompt({ includeMetadata: false }),
  prompt: userMessage,
});
```

### LangChain.js

```typescript
import { ChatOpenAI } from '@langchain/openai';
import { TokenPak, Block } from 'tokenpak';

const pack = new TokenPak({ budget: 4000 });
pack.add(new Block({ type: 'instructions', content: systemPrompt }));

const llm = new ChatOpenAI({ model: 'gpt-4o' });
const response = await llm.invoke([
  ['system', pack.toPrompt({ includeMetadata: false })],
  ['user', question],
]);
```

### Cloudflare Workers / Vercel Edge

```typescript
// Works with zero Node.js-specific APIs
import { TokenPak, Block } from 'tokenpak';

export default {
  async fetch(request: Request): Promise<Response> {
    const pack = new TokenPak({ budget: 3000 });
    pack.add(new Block({ type: 'instructions', content: 'Be concise.', priority: 'critical' }));
    // ...
    return new Response(pack.toPrompt());
  }
};
```

## Token Counting

The SDK uses a character-based approximation (`chars / 4`) for token counting.
For production accuracy, integrate `tiktoken`:

```typescript
import { encoding_for_model } from 'tiktoken';
import { Block } from 'tokenpak';

const enc = encoding_for_model('gpt-4o');

// Override token count with precise value
const block = new Block({
  type: 'knowledge',
  content: myContent,
  tokens: enc.encode(myContent).length,
});
```

## Build Targets

- **CommonJS**: `dist/index.js` — Node.js `require()`
- **ESM**: `dist/index.mjs` — `import from`
- **Types**: `dist/index.d.ts` — TypeScript

## License

Apache-2.0
