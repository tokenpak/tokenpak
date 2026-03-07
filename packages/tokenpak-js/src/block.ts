/**
 * block.ts — TokenPak Block
 *
 * A Block is the fundamental content unit in a TokenPak.
 * Each block has a type, unique ID, content, and optional metadata.
 */

import { estimateTokens, generateId, nowISO } from './utils';

/** Valid block types per TokenPak v1.0 schema */
export type BlockType =
  | 'instructions'
  | 'code'
  | 'knowledge'
  | 'memory'
  | 'conversation'
  | 'evidence'
  | 'system';

/** Compaction priority levels */
export type Priority = 'critical' | 'high' | 'medium' | 'low' | 'internal';

/** Block provenance metadata (for evidence blocks) */
export interface BlockProvenance {
  source_type?: string;
  source_id?: string;
  retrieved_at?: string;
  url?: string;
  score?: number;
}

/** Block constructor options */
export interface BlockOptions {
  /** Block content type */
  type: BlockType;
  /** Unique ID within a pack. Auto-generated if omitted */
  id?: string;
  /** Text content */
  content: string;
  /** Compaction priority. Defaults to 'medium' */
  priority?: Priority;
  /** Relevance score 0.0–1.0 */
  quality?: number;
  /** Token count. Estimated if omitted */
  tokens?: number;
  /** Whether content has already been compacted */
  compacted?: boolean;
  /** Source provenance metadata */
  provenance?: BlockProvenance;
}

/** Raw block object as stored in JSON wire format */
export interface RawBlock {
  type: BlockType;
  id: string;
  content: string;
  tokens?: number;
  priority?: Priority;
  quality?: number;
  compacted?: boolean;
  provenance?: BlockProvenance;
}

/**
 * A single content block within a TokenPak.
 *
 * @example
 * const block = new Block({
 *   type: 'instructions',
 *   content: 'You are a helpful assistant.',
 *   priority: 'critical',
 * });
 */
export class Block {
  readonly type: BlockType;
  readonly id: string;
  content: string;
  priority: Priority;
  quality: number;
  tokens: number;
  compacted: boolean;
  provenance?: BlockProvenance;

  constructor(options: BlockOptions) {
    this.type = options.type;
    this.id = options.id ?? generateId(options.type.slice(0, 4));
    this.content = options.content;
    this.priority = options.priority ?? 'medium';
    this.quality = options.quality ?? 1.0;
    this.tokens = options.tokens ?? estimateTokens(options.content);
    this.compacted = options.compacted ?? false;
    this.provenance = options.provenance;
  }

  /** Re-estimate token count from current content */
  recount(): void {
    this.tokens = estimateTokens(this.content);
  }

  /** Serialize to plain JSON object */
  toJSON(): RawBlock {
    const raw: RawBlock = {
      type: this.type,
      id: this.id,
      content: this.content,
      tokens: this.tokens,
      priority: this.priority,
    };
    if (this.quality !== 1.0) raw.quality = this.quality;
    if (this.compacted) raw.compacted = this.compacted;
    if (this.provenance) raw.provenance = this.provenance;
    return raw;
  }

  /** Deserialize from plain JSON object */
  static fromJSON(raw: RawBlock): Block {
    return new Block({
      type: raw.type,
      id: raw.id,
      content: raw.content,
      tokens: raw.tokens,
      priority: raw.priority,
      quality: raw.quality,
      compacted: raw.compacted,
      provenance: raw.provenance,
    });
  }

  /** Returns a human-readable summary for debug output */
  toString(): string {
    return `Block(${this.type}:${this.id} ~${this.tokens}t priority=${this.priority})`;
  }
}
