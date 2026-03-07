"use strict";
/**
 * TokenPak BlockRegistry
 * Wraps the /blocks/* HTTP endpoints.
 * Blocks are reusable named content fragments that can be injected into prompts.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.BlockRegistry = void 0;
const client_1 = require("./client");
function toBlock(raw) {
    return {
        id: raw.id,
        type: raw.type,
        content: raw.content,
        tokenCount: raw.token_count,
        metadata: raw.metadata,
    };
}
class BlockRegistry {
    constructor(config) {
        this.client = new client_1.TokenPakHttpClient(config);
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
    async register(block) {
        const raw = await this.client.post('/blocks', block);
        return toBlock(raw);
    }
    /**
     * Retrieve a block by id.
     */
    async get(id) {
        try {
            const raw = await this.client.get(`/blocks/${encodeURIComponent(id)}`);
            return toBlock(raw);
        }
        catch {
            return null;
        }
    }
    /**
     * List all registered blocks, optionally filtered by type.
     */
    async list(type) {
        const path = type ? `/blocks?type=${encodeURIComponent(type)}` : '/blocks';
        const raws = await this.client.get(path);
        return raws.map(toBlock);
    }
    /**
     * Delete a block by id.
     */
    async delete(id) {
        try {
            await this.client.post(`/blocks/${encodeURIComponent(id)}/delete`, {});
            return true;
        }
        catch {
            return false;
        }
    }
    /**
     * Registry statistics (block counts, token totals).
     */
    async stats() {
        const raw = await this.client.get('/blocks/stats');
        return {
            totalBlocks: raw.total_blocks,
            totalTokens: raw.total_tokens,
            blocksByType: raw.blocks_by_type,
        };
    }
}
exports.BlockRegistry = BlockRegistry;
//# sourceMappingURL=BlockRegistry.js.map