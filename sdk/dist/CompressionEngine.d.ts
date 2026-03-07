/**
 * TokenPak CompressionEngine
 * Wraps the /compress and /compress/conversation HTTP endpoints.
 */
import { CompressOptions, CompressResult, ConversationMessage, ConversationCompressOptions, ConversationCompressResult, TokenPakConfig } from './types';
export declare class CompressionEngine {
    private readonly client;
    constructor(config?: TokenPakConfig);
    /**
     * Compress a single text string.
     * Requires the TokenPak API server to be running.
     *
     * @example
     * const engine = new CompressionEngine();
     * const result = await engine.compress("Long prompt text...");
     * console.log(`Saved ${result.savingsPct.toFixed(1)}% tokens`);
     */
    compress(text: string, options?: CompressOptions): Promise<CompressResult>;
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
    compressConversation(messages: ConversationMessage[], options?: ConversationCompressOptions): Promise<ConversationCompressResult>;
}
//# sourceMappingURL=CompressionEngine.d.ts.map