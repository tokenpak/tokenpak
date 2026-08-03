/**
 * policy.ts — TokenPak Policy
 *
 * Defines compaction and budget policies for a TokenPak.
 */

/** Compaction modes per TokenPak schema */
export type CompactionMode = 'lossless' | 'balanced' | 'aggressive' | 'semantic';

/** Per-block-type compaction override */
export interface PerTypeLimit {
  max_tokens?: number;
  mode?: CompactionMode;
  hot_window?: number;
}

/** Budget policy */
export interface BudgetPolicy {
  /** Total token budget */
  total?: number;
  /** Max tokens for any single block */
  per_block_max?: number;
  /** Tokens reserved for model output */
  reserve_for_output?: number;
}

/** Policy constructor options */
export interface PolicyOptions {
  /** Compaction mode. Defaults to 'balanced' */
  mode?: CompactionMode;
  /** Max total tokens after compaction */
  maxTokens?: number;
  /** Block type priority order for compaction */
  priorityOrder?: string[];
  /** Per-type compaction overrides */
  perTypeLimits?: Record<string, PerTypeLimit>;
  /** Token budget settings */
  budget?: BudgetPolicy;
}

/**
 * Compaction and budget policy for a TokenPak.
 *
 * @example
 * const policy = new Policy({
 *   mode: 'balanced',
 *   maxTokens: 8000,
 *   priorityOrder: ['instructions', 'code', 'knowledge'],
 * });
 */
export class Policy {
  mode: CompactionMode;
  maxTokens?: number;
  priorityOrder?: string[];
  perTypeLimits?: Record<string, PerTypeLimit>;
  budget?: BudgetPolicy;

  constructor(options: PolicyOptions = {}) {
    this.mode = options.mode ?? 'balanced';
    this.maxTokens = options.maxTokens;
    this.priorityOrder = options.priorityOrder;
    this.perTypeLimits = options.perTypeLimits;
    this.budget = options.budget;
  }

  /** Serialize to JSON wire format */
  toJSON(): Record<string, unknown> {
    const out: Record<string, unknown> = {};

    const compaction: Record<string, unknown> = { mode: this.mode };
    if (this.maxTokens !== undefined) compaction.max_tokens = this.maxTokens;
    if (this.priorityOrder) compaction.priority_order = this.priorityOrder;
    if (this.perTypeLimits) compaction.per_type_limits = this.perTypeLimits;
    out.compaction = compaction;

    if (this.budget) {
      out.budget = this.budget;
    } else if (this.maxTokens !== undefined) {
      out.budget = { total: this.maxTokens };
    }

    return out;
  }

  /** Deserialize from JSON wire policies object */
  static fromJSON(raw: Record<string, unknown>): Policy {
    const compaction = (raw.compaction as Record<string, unknown>) ?? {};
    const budget = raw.budget as BudgetPolicy | undefined;
    return new Policy({
      mode: (compaction.mode as CompactionMode) ?? 'balanced',
      maxTokens:
        (compaction.max_tokens as number | undefined) ??
        (budget?.total as number | undefined),
      priorityOrder: compaction.priority_order as string[] | undefined,
      perTypeLimits: compaction.per_type_limits as Record<string, PerTypeLimit> | undefined,
      budget,
    });
  }
}
