/**
 * TokenPak BlockRegistry
 * Wraps the /blocks/* HTTP endpoints.
 * Blocks are reusable named content fragments that can be injected into prompts.
 */
import { Block, BlockRegistryStats, TokenPakConfig } from './types';
export declare class BlockRegistry {
    private readonly client;
    constructor(config?: TokenPakConfig);
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
    register(block: Omit<Block, 'tokenCount'>): Promise<Block>;
    /**
     * Retrieve a block by id.
     */
    get(id: string): Promise<Block | null>;
    /**
     * List all registered blocks, optionally filtered by type.
     */
    list(type?: string): Promise<Block[]>;
    /**
     * Delete a block by id.
     */
    delete(id: string): Promise<boolean>;
    /**
     * Registry statistics (block counts, token totals).
     */
    stats(): Promise<BlockRegistryStats>;
}
//# sourceMappingURL=BlockRegistry.d.ts.map