/**
 * compactor.ts — Token budget enforcement and compaction utilities.
 *
 * Provides lightweight compaction helpers that work without
 * external dependencies. For production semantic compaction,
 * plug in your own LLM-based compactor via the `compact` option.
 */

import { Block, Priority } from './block';
import { CompactionMode } from './policy';

export interface CompactionResult {
  blocks: Block[];
  tokensRemoved: number;
  blocksDropped: number;
  mode: CompactionMode;
}

const PRIORITY_RANK: Record<Priority, number> = {
  critical: 5,
  high: 4,
  medium: 3,
  low: 2,
  internal: 1,
};

/**
 * Apply a token budget to a list of blocks.
 *
 * Strategy varies by mode:
 * - lossless: Drop only 'internal' priority blocks
 * - balanced: Drop internal → low priority blocks
 * - aggressive: Drop internal → low → medium priority blocks
 * - semantic: Same as aggressive (semantic requires external LLM)
 *
 * Within each priority tier, longer blocks are dropped first.
 */
export function applyBudget(
  blocks: Block[],
  maxTokens: number,
  mode: CompactionMode = 'balanced',
): CompactionResult {
  const total = blocks.reduce((s, b) => s + b.tokens, 0);

  if (total <= maxTokens) {
    return { blocks: [...blocks], tokensRemoved: 0, blocksDropped: 0, mode };
  }

  const dropOrder: Priority[][] = ({
    lossless:   [['internal']],
    balanced:   [['internal'], ['low']],
    aggressive: [['internal'], ['low'], ['medium']],
    semantic:   [['internal'], ['low'], ['medium']],
  } as Record<string, Priority[][]>)[mode] ?? [['internal'], ['low']];

  const result = [...blocks];
  let tokensRemoved = 0;
  let blocksDropped = 0;

  for (const tier of dropOrder) {
    let current = result.reduce((s, b) => s + b.tokens, 0);
    if (current <= maxTokens) break;

    // Sort candidates (this tier, longest first) so big blocks go first
    const candidates = result
      .map((b, i) => ({ block: b, index: i }))
      .filter(({ block }) => tier.includes(block.priority))
      .sort((a, b) => b.block.tokens - a.block.tokens);

    for (const { block, index: _ } of candidates) {
      if (current <= maxTokens) break;
      const idx = result.indexOf(block);
      if (idx !== -1) {
        current -= block.tokens;
        tokensRemoved += block.tokens;
        blocksDropped++;
        result.splice(idx, 1);
      }
    }
  }

  return { blocks: result, tokensRemoved, blocksDropped, mode };
}

/**
 * Truncate a single block's content to fit within a token budget.
 * Uses a character-based approximation.
 */
export function truncateBlock(block: Block, maxTokens: number): Block {
  const targetChars = maxTokens * 4; // ~4 chars per token
  if (block.content.length <= targetChars) return block;

  const truncated = new Block({
    type: block.type,
    id: block.id,
    content: block.content.slice(0, targetChars) + '\n[... truncated]',
    priority: block.priority,
    quality: block.quality,
    compacted: true,
    provenance: block.provenance,
  });

  return truncated;
}

/**
 * Sort blocks by priority (highest first), then by quality.
 */
export function sortByPriority(blocks: Block[]): Block[] {
  return [...blocks].sort((a, b) => {
    const rankDiff = PRIORITY_RANK[b.priority] - PRIORITY_RANK[a.priority];
    if (rankDiff !== 0) return rankDiff;
    return (b.quality ?? 1) - (a.quality ?? 1);
  });
}
