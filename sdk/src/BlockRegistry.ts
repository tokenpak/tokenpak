/**
 * TokenPak BlockRegistry
 * Wraps the /blocks/* HTTP endpoints.
 * Blocks are reusable named content fragments that can be injected into prompts.
 */

import { TokenPakHttpClient } from './client';
import { Block, BlockRegistryStats, TokenPakConfig } from './types';

interface RawBlock {
  id: string;
  type: string;
  content: string;
  token_count: number;
  metadata?: Record<string, unknown>;
}

interface RawBlockRegistryStats {
  total_blocks: number;
  total_tokens: number;
  blocks_by_type: Record<string, number>;
}

function toBlock(raw: RawBlock): Block {
  return {
    id: raw.id,
    type: raw.type,
    content: raw.content,
    tokenCount: raw.token_count,
    metadata: raw.metadata,
  };
}

export class BlockRegistry {
  private readonly client: TokenPakHttpClient;

  constructor(config?: TokenPakConfig) {
    this.client = new TokenPakHttpClient(config);
  }

  /**
   * Register a new named block.
   *
   * @example
   * await registry.register({
   *   id: 'system-prompt-v1',
   *   type: 'system',
   *   content: 'You are a helpful assistant...',
   * });
   */
  async register(block: Omit<Block, 'tokenCount'>): Promise<Block> {
    const raw = await this.client.post<RawBlock>('/blocks', block);
    return toBlock(raw);
  }

  /**
   * Retrieve a block by id.
   */
  async get(id: string): Promise<Block | null> {
    try {
      const raw = await this.client.get<RawBlock>(`/blocks/${encodeURIComponent(id)}`);
      return toBlock(raw);
    } catch {
      return null;
    }
  }

  /**
   * List all registered blocks, optionally filtered by type.
   */
  async list(type?: string): Promise<Block[]> {
    const path = type ? `/blocks?type=${encodeURIComponent(type)}` : '/blocks';
    const raws = await this.client.get<RawBlock[]>(path);
    return raws.map(toBlock);
  }

  /**
   * Delete a block by id.
   */
  async delete(id: string): Promise<boolean> {
    try {
      await this.client.post(`/blocks/${encodeURIComponent(id)}/delete`, {});
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Registry statistics (block counts, token totals).
   */
  async stats(): Promise<BlockRegistryStats> {
    const raw = await this.client.get<RawBlockRegistryStats>('/blocks/stats');
    return {
      totalBlocks: raw.total_blocks,
      totalTokens: raw.total_tokens,
      blocksByType: raw.blocks_by_type,
    };
  }
}
