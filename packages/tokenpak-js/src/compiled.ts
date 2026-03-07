/**
 * compiled.ts — CompiledPack
 *
 * The result of TokenPak.compile(). Provides serialization to JSON,
 * wire format (compact JSON string), and prompt format (plain text).
 */

import { Block } from './block';

/** Raw wire format object */
export interface WireObject {
  header: {
    version: string;
    id: string;
    created: string;
    schema: string;
  };
  metadata: {
    task: string;
    source: string;
    target?: string;
    tags?: string[];
    expires?: string;
  };
  blocks: ReturnType<Block['toJSON']>[];
  policies?: Record<string, unknown>;
  [key: string]: unknown;
}

/**
 * A compiled, ready-to-transmit TokenPak.
 *
 * Returned by `TokenPak.compile()`.
 */
export class CompiledPack {
  private readonly _wire: WireObject;

  constructor(wire: WireObject) {
    this._wire = wire;
  }

  /**
   * Return the raw wire object (plain JSON-serializable).
   */
  toJSON(): WireObject {
    return this._wire;
  }

  /**
   * Serialize to compact JSON wire format string.
   */
  toWire(): string {
    return JSON.stringify(this._wire);
  }

  /**
   * Serialize to pretty-printed JSON wire format string.
   */
  toWirePretty(): string {
    return JSON.stringify(this._wire, null, 2);
  }

  /**
   * Convert to a plain-text prompt string.
   *
   * Renders blocks in priority order, tagged by type. Suitable for
   * injecting directly into an LLM system prompt.
   *
   * @param opts.includeMetadata - Include task/source header (default: true)
   */
  toPrompt(opts: { includeMetadata?: boolean } = {}): string {
    const includeMetadata = opts.includeMetadata ?? true;
    const parts: string[] = [];

    if (includeMetadata) {
      const meta = this._wire.metadata;
      parts.push(`[TokenPak: ${meta.task}]`);
      if (meta.source) parts.push(`Source: ${meta.source}`);
      parts.push('');
    }

    // Sort blocks by priority for prompt ordering
    const priorityRank: Record<string, number> = {
      critical: 0,
      high: 1,
      medium: 2,
      low: 3,
      internal: 4,
    };

    const sorted = [...this._wire.blocks].sort((a, b) => {
      const ra = priorityRank[a.priority ?? 'medium'] ?? 2;
      const rb = priorityRank[b.priority ?? 'medium'] ?? 2;
      return ra - rb;
    });

    for (const block of sorted) {
      const header = `## [${block.type.toUpperCase()}] ${block.id}`;
      parts.push(header);
      parts.push(block.content);
      parts.push('');
    }

    return parts.join('\n').trimEnd();
  }

  /** Total token count across all blocks */
  get totalTokens(): number {
    return this._wire.blocks.reduce((sum, b) => sum + (b.tokens ?? 0), 0);
  }

  /** Pack ID */
  get id(): string {
    return this._wire.header.id;
  }

  /** Number of blocks */
  get blockCount(): number {
    return this._wire.blocks.length;
  }

  /**
   * Deserialize from a wire format JSON string.
   */
  static fromWire(wireStr: string): CompiledPack {
    const obj = JSON.parse(wireStr) as WireObject;
    return new CompiledPack(obj);
  }

  /**
   * Deserialize from a wire format object.
   */
  static fromObject(obj: WireObject): CompiledPack {
    return new CompiledPack(obj);
  }
}
