"use strict";
/**
 * TokenPak TelemetryCollector
 * Wraps the /telemetry/* HTTP endpoints.
 * Tracks LLM usage, costs, and latency across your application.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.TelemetryCollector = void 0;
const client_1 = require("./client");
function fromRaw(raw) {
    return {
        eventType: raw.event_type,
        timestamp: new Date(raw.timestamp),
        data: raw.data,
        sessionId: raw.session_id,
        model: raw.model,
    };
}
class TelemetryCollector {
    constructor(config) {
        this.client = new client_1.TokenPakHttpClient(config);
    }
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
    async record(event) {
        await this.client.post('/telemetry', {
            event_type: event.eventType,
            timestamp: (event.timestamp ?? new Date()).toISOString(),
            data: event.data,
            session_id: event.sessionId,
            model: event.model,
        });
    }
    /**
     * List recent telemetry events.
     *
     * @param limit  Max events to return (default: 100)
     * @param model  Filter to a specific model
     */
    async list(limit = 100, model) {
        const params = new URLSearchParams({ limit: String(limit) });
        if (model)
            params.set('model', model);
        const raws = await this.client.get(`/telemetry?${params}`);
        return raws.map(fromRaw);
    }
    /**
     * Get aggregated statistics (total cost, token usage, latency, model breakdown).
     */
    async stats() {
        const raw = await this.client.get('/telemetry/stats');
        return {
            totalEvents: raw.total_events,
            totalCost: raw.total_cost,
            totalTokensIn: raw.total_tokens_in,
            totalTokensOut: raw.total_tokens_out,
            averageLatencyMs: raw.average_latency_ms,
            modelBreakdown: Object.fromEntries(Object.entries(raw.model_breakdown).map(([k, v]) => [
                k,
                {
                    calls: v.calls,
                    cost: v.cost,
                    tokensIn: v.tokens_in,
                    tokensOut: v.tokens_out,
                },
            ])),
        };
    }
    /**
     * Reset all telemetry data (use with caution).
     */
    async reset() {
        await this.client.post('/telemetry/reset', {});
    }
}
exports.TelemetryCollector = TelemetryCollector;
//# sourceMappingURL=TelemetryCollector.js.map