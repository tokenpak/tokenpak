/**
 * TokenPak CompressionEngine
 * Wraps the /compress and /compress/conversation HTTP endpoints.
 */

import { TokenPakHttpClient } from './client';
import {
  CompressOptions,
  CompressResult,
  ConversationMessage,
  ConversationCompressOptions,
  ConversationCompressResult,
  TokenPakConfig,
} from './types';

interface CompressRequestBody {
  text: string;
  target_tokens?: number;
  strategy?: string;
  cache?: boolean;
  preserve_code?: boolean;
  preserve_structure?: boolean;
}

interface CompressResponseBody {
  original_text: string;
  compressed_text: string;
  original_tokens: number;
  compressed_tokens: number;
  savings_pct: number;
  cache_hit: boolean;
  elapsed_ms: number;
}

interface ConversationRequestBody {
  messages: ConversationMessage[];
  keep_recent?: number;
  target_tokens?: number;
}

interface ConversationResponseBody {
  messages: ConversationMessage[];
  total_savings: number;
}

export class CompressionEngine {
  private readonly client: TokenPakHttpClient;

  constructor(config?: TokenPakConfig) {
    this.client = new TokenPakHttpClient(config);
  }

  /**
   * Compress a single text string.
   * Requires the TokenPak API server to be running.
   *
   * @example
   * const engine = new CompressionEngine();
   * const result = await engine.compress("Long prompt text...");
   * console.log(`Saved ${result.savingsPct.toFixed(1)}% tokens`);
   */
  async compress(text: string, options: CompressOptions = {}): Promise<CompressResult> {
    const body: CompressRequestBody = {
      text,
      target_tokens: options.targetTokens,
      strategy: options.strategy ?? 'heuristic',
      cache: options.cache ?? true,
      preserve_code: options.preserveCode ?? true,
      preserve_structure: options.preserveStructure ?? true,
    };

    const raw = await this.client.post<CompressResponseBody>('/compress', body);

    return {
      originalText: raw.original_text,
      compressedText: raw.compressed_text,
      originalTokens: raw.original_tokens,
      compressedTokens: raw.compressed_tokens,
      savingsPct: raw.savings_pct,
      cacheHit: raw.cache_hit,
      elapsedMs: raw.elapsed_ms,
    };
  }

  /**
   * Compress a conversation history.
   * Keeps recent messages intact, compresses older context.
   *
   * @example
   * const engine = new CompressionEngine();
   * const result = await engine.compressConversation(messages, {
   *   keepRecent: 3,
   *   targetTokens: 4000,
   * });
   * const response = await openai.chat.completions.create({
   *   model: "gpt-4o",
   *   messages: result.messages,
   * });
   */
  async compressConversation(
    messages: ConversationMessage[],
    options: ConversationCompressOptions = {}
  ): Promise<ConversationCompressResult> {
    const body: ConversationRequestBody = {
      messages,
      keep_recent: options.keepRecent ?? 3,
      target_tokens: options.targetTokens ?? 4000,
    };

    const raw = await this.client.post<ConversationResponseBody>(
      '/compress/conversation',
      body
    );

    return {
      messages: raw.messages,
      totalSavings: raw.total_savings,
    };
  }
}
