"use strict";
/**
 * TokenPak — Deterministic context compression for LLMs
 *
 * @packageDocumentation
 *
 * @example
 * ```typescript
 * import { CompressionEngine } from 'tokenpak';
 *
 * const engine = new CompressionEngine({ baseUrl: 'http://localhost:8000' });
 * const result = await engine.compress("Very long prompt text...");
 * console.log(`Saved ${result.savingsPct.toFixed(1)}% tokens`);
 * ```
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.VERSION = exports.TokenPakTimeoutError = exports.TokenPakConnectionError = exports.TokenPakError = exports.TokenPakHttpClient = exports.TelemetryCollector = exports.BlockRegistry = exports.CacheManager = exports.CompressionEngine = void 0;
var CompressionEngine_1 = require("./CompressionEngine");
Object.defineProperty(exports, "CompressionEngine", { enumerable: true, get: function () { return CompressionEngine_1.CompressionEngine; } });
var CacheManager_1 = require("./CacheManager");
Object.defineProperty(exports, "CacheManager", { enumerable: true, get: function () { return CacheManager_1.CacheManager; } });
var BlockRegistry_1 = require("./BlockRegistry");
Object.defineProperty(exports, "BlockRegistry", { enumerable: true, get: function () { return BlockRegistry_1.BlockRegistry; } });
var TelemetryCollector_1 = require("./TelemetryCollector");
Object.defineProperty(exports, "TelemetryCollector", { enumerable: true, get: function () { return TelemetryCollector_1.TelemetryCollector; } });
var client_1 = require("./client");
Object.defineProperty(exports, "TokenPakHttpClient", { enumerable: true, get: function () { return client_1.TokenPakHttpClient; } });
var types_1 = require("./types");
Object.defineProperty(exports, "TokenPakError", { enumerable: true, get: function () { return types_1.TokenPakError; } });
Object.defineProperty(exports, "TokenPakConnectionError", { enumerable: true, get: function () { return types_1.TokenPakConnectionError; } });
Object.defineProperty(exports, "TokenPakTimeoutError", { enumerable: true, get: function () { return types_1.TokenPakTimeoutError; } });
/** Package version */
exports.VERSION = '1.0.0';
//# sourceMappingURL=index.js.map