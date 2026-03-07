/**
 * tokenpak — JavaScript/TypeScript SDK
 *
 * The Context Standard for AI systems.
 *
 * @example
 * import { TokenPak, Block, Policy } from 'tokenpak';
 *
 * const pack = new TokenPak({ budget: 8000 });
 *
 * pack.add(new Block({
 *   type: 'instructions',
 *   id: 'system_prompt',
 *   content: 'You are a helpful assistant.',
 *   priority: 'critical',
 * }));
 *
 * pack.add(new Block({
 *   type: 'knowledge',
 *   id: 'docs',
 *   content: apiDocs,
 *   priority: 'high',
 * }));
 *
 * const compiled = pack.compile();
 * const prompt = compiled.toPrompt();     // → plain text for LLM
 * const wire = compiled.toWire();         // → JSON string for transport
 * const json = compiled.toJSON();         // → raw object
 *
 * // Round-trip
 * const pack2 = TokenPak.fromWire(wire);
 */

// Core types
export { Block } from './block';
export type { BlockOptions, BlockProvenance, BlockType, Priority, RawBlock } from './block';

export { Policy } from './policy';
export type { BudgetPolicy, CompactionMode, PerTypeLimit, PolicyOptions } from './policy';

export { TokenPak } from './pack';
export type { PackMetadata, TokenPakOptions } from './pack';

export { CompiledPack } from './compiled';
export type { WireObject } from './compiled';

// Validation
export { validate } from './schema';
export type { ValidationError, ValidationResult } from './schema';

// Compaction utilities
export { applyBudget, sortByPriority, truncateBlock } from './compactor';
export type { CompactionResult } from './compactor';

// Utilities
export { estimateTokens, generateId, nowISO, sanitizeId } from './utils';

/** SDK version */
export const VERSION = '1.0.0';

/** TokenPak protocol version */
export const PROTOCOL_VERSION = '1.0';
