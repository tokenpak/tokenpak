/**
 * TokenPak TelemetryCollector
 * Wraps the /telemetry/* HTTP endpoints.
 * Tracks LLM usage, costs, and latency across your application.
 */
import { TelemetryEvent, TelemetryStats, TokenPakConfig } from './types';
export declare class TelemetryCollector {
    private readonly client;
    constructor(config?: TokenPakConfig);
    /**
     * Record a telemetry event.
     *
     * @example
     * await telemetry.record({
     *   eventType: 'completion',
     *   timestamp: new Date(),
     *   data: { tokensIn: 1200, tokensOut: 350, costUsd: 0.0042 },
     *   model: 'gpt-4o',
     * });
     */
    record(event: Omit<TelemetryEvent, 'timestamp'> & {
        timestamp?: Date;
    }): Promise<void>;
    /**
     * List recent telemetry events.
     *
     * @param limit  Max events to return (default: 100)
     * @param model  Filter to a specific model
     */
    list(limit?: number, model?: string): Promise<TelemetryEvent[]>;
    /**
     * Get aggregated statistics (total cost, token usage, latency, model breakdown).
     */
    stats(): Promise<TelemetryStats>;
    /**
     * Reset all telemetry data (use with caution).
     */
    reset(): Promise<void>;
}
//# sourceMappingURL=TelemetryCollector.d.ts.map