/**
 * pack.ts — TokenPak
 *
 * The main container class. Build packs with .add(), then .compile()
 * to get a serializable CompiledPack.
 */

import { Block, BlockOptions, BlockType, RawBlock } from './block';
import { CompiledPack, WireObject } from './compiled';
import { Policy, PolicyOptions } from './policy';
import { generateId, nowISO } from './utils';

/** TokenPak metadata */
export interface PackMetadata {
  task: string;
  source: string;
  target?: string;
  tags?: string[];
  expires?: string;
}

/** TokenPak constructor options */
export interface TokenPakOptions {
  /** Token budget. Used as policy.maxTokens if no policy provided */
  budget?: number;
  /** Initial metadata */
  metadata?: Partial<PackMetadata>;
  /** Compaction/budget policy */
  policy?: Policy | PolicyOptions;
  /** Pack ID. Auto-generated if omitted */
  id?: string;
}

/**
 * A TokenPak — the main context container.
 *
 * @example
 * const pack = new TokenPak({ budget: 8000 });
 *
 * pack.add(new Block({
 *   type: 'instructions',
 *   content: 'You are a helpful assistant.',
 *   priority: 'critical',
 * }));
 *
 * pack.add(new Block({
 *   type: 'knowledge',
 *   content: apiDocs,
 *   priority: 'high',
 * }));
 *
 * const compiled = pack.compile();
 * const prompt = compiled.toPrompt();
 */
export class TokenPak {
  readonly id: string;
  private _blocks: Block[] = [];
  policy?: Policy;
  metadata: Partial<PackMetadata>;
  private readonly _budget?: number;

  constructor(options: TokenPakOptions = {}) {
    this.id = options.id ?? generateId('pak');
    this._budget = options.budget;
    this.metadata = options.metadata ?? {};

    if (options.policy) {
      if (options.policy instanceof Policy) {
        this.policy = options.policy;
      } else {
        this.policy = new Policy(options.policy);
      }
    } else if (options.budget !== undefined) {
      this.policy = new Policy({ maxTokens: options.budget });
    }
  }

  // ── Block management ────────────────────────────────────────────────────

  /**
   * Add a Block or BlockOptions to this pack.
   * Returns `this` for chaining.
   */
  add(blockOrOptions: Block | BlockOptions): this {
    const block =
      blockOrOptions instanceof Block
        ? blockOrOptions
        : new Block(blockOrOptions);
    this._blocks.push(block);
    return this;
  }

  /**
   * Remove a block by ID.
   * Returns true if a block was removed.
   */
  remove(id: string): boolean {
    const before = this._blocks.length;
    this._blocks = this._blocks.filter(b => b.id !== id);
    return this._blocks.length < before;
  }

  /**
   * Get a block by ID.
   */
  get(id: string): Block | undefined {
    return this._blocks.find(b => b.id === id);
  }

  /**
   * Get all blocks, optionally filtered by type.
   */
  getBlocks(type?: BlockType): Block[] {
    if (type) return this._blocks.filter(b => b.type === type);
    return [...this._blocks];
  }

  /** Number of blocks in this pack */
  get blockCount(): number {
    return this._blocks.length;
  }

  /** Total estimated token count */
  get totalTokens(): number {
    return this._blocks.reduce((sum, b) => sum + b.tokens, 0);
  }

  /** Budget from policy or constructor option */
  get budget(): number | undefined {
    return this.policy?.maxTokens ?? this._budget;
  }

  /** Remaining token budget */
  get remainingBudget(): number | undefined {
    if (this.budget === undefined) return undefined;
    return this.budget - this.totalTokens;
  }

  // ── Compilation ─────────────────────────────────────────────────────────

  /**
   * Compile this pack to a serializable CompiledPack.
   * Applies simple budget enforcement if a budget is set.
   */
  compile(): CompiledPack {
    let blocks = [...this._blocks];

    // Apply budget: drop lowest priority blocks if over budget
    const maxTokens = this.budget;
    if (maxTokens !== undefined) {
      blocks = this._applyBudget(blocks, maxTokens);
    }

    const wire: WireObject = {
      header: {
        version: '1.0',
        id: this.id,
        created: nowISO(),
        schema: 'https://tokenpak.dev/schema/v1.0.json',
      },
      metadata: {
        task: this.metadata.task ?? 'tokenpak-js',
        source: this.metadata.source ?? 'sdk:tokenpak-js',
        ...(this.metadata.target && { target: this.metadata.target }),
        ...(this.metadata.tags?.length && { tags: this.metadata.tags }),
        ...(this.metadata.expires && { expires: this.metadata.expires }),
      },
      blocks: blocks.map(b => b.toJSON()),
    };

    if (this.policy) {
      wire.policies = this.policy.toJSON();
    }

    return new CompiledPack(wire);
  }

  /**
   * Drop blocks by ascending priority until under budget.
   * Never drops 'critical' blocks.
   */
  private _applyBudget(blocks: Block[], maxTokens: number): Block[] {
    const total = blocks.reduce((s, b) => s + b.tokens, 0);
    if (total <= maxTokens) return blocks;

    const rankOrder = ['internal', 'low', 'medium', 'high', 'critical'];
    const result = [...blocks];

    for (const priority of rankOrder) {
      if (priority === 'critical') break; // never drop critical

      let current = result.reduce((s, b) => s + b.tokens, 0);
      if (current <= maxTokens) break;

      // Find removable blocks of this priority (last to first)
      for (let i = result.length - 1; i >= 0 && current > maxTokens; i--) {
        if (result[i].priority === priority) {
          current -= result[i].tokens;
          result.splice(i, 1);
        }
      }
    }

    return result;
  }

  // ── Serialization ────────────────────────────────────────────────────────

  /** Serialize to plain JSON object */
  toJSON(): WireObject {
    return this.compile().toJSON();
  }

  /** Serialize to JSON wire string */
  toWire(): string {
    return this.compile().toWire();
  }

  /** Convert to prompt string */
  toPrompt(opts?: { includeMetadata?: boolean }): string {
    return this.compile().toPrompt(opts);
  }

  // ── Deserialization ───────────────────────────────────────────────────────

  /** Create a TokenPak from a JSON wire object */
  static fromJSON(raw: WireObject): TokenPak {
    const pack = new TokenPak({
      id: raw.header?.id,
      metadata: {
        task: raw.metadata?.task,
        source: raw.metadata?.source,
        target: raw.metadata?.target,
        tags: raw.metadata?.tags,
        expires: raw.metadata?.expires,
      },
      policy: raw.policies
        ? Policy.fromJSON(raw.policies as Record<string, unknown>)
        : undefined,
    });

    for (const rawBlock of raw.blocks ?? []) {
      pack.add(Block.fromJSON(rawBlock as RawBlock));
    }

    return pack;
  }

  /** Create a TokenPak from a JSON wire string */
  static fromWire(wireStr: string): TokenPak {
    return TokenPak.fromJSON(JSON.parse(wireStr) as WireObject);
  }

  /** Merge multiple packs into one */
  static merge(packs: TokenPak[], opts: TokenPakOptions = {}): TokenPak {
    const merged = new TokenPak(opts);
    for (const pack of packs) {
      for (const block of pack.getBlocks()) {
        merged.add(block);
      }
    }
    return merged;
  }

  toString(): string {
    return `TokenPak(id=${this.id} blocks=${this.blockCount} tokens=${this.totalTokens})`;
  }
}
