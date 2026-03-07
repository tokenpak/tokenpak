"use strict";
/**
 * TokenPak CompressionEngine
 * Wraps the /compress and /compress/conversation HTTP endpoints.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.CompressionEngine = void 0;
const client_1 = require("./client");
class CompressionEngine {
    constructor(config) {
        this.client = new client_1.TokenPakHttpClient(config);
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
    async compress(text, options = {}) {
        const body = {
            text,
            target_tokens: options.targetTokens,
            strategy: options.strategy ?? 'heuristic',
            cache: options.cache ?? true,
            preserve_code: options.preserveCode ?? true,
            preserve_structure: options.preserveStructure ?? true,
        };
        const raw = await this.client.post('/compress', body);
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
    async compressConversation(messages, options = {}) {
        const body = {
            messages,
            keep_recent: options.keepRecent ?? 3,
            target_tokens: options.targetTokens ?? 4000,
        };
        const raw = await this.client.post('/compress/conversation', body);
        return {
            messages: raw.messages,
            totalSavings: raw.total_savings,
        };
    }
}
exports.CompressionEngine = CompressionEngine;
//# sourceMappingURL=CompressionEngine.js.map