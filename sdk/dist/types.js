"use strict";
/**
 * TokenPak TypeScript SDK — Type Definitions
 * @module types
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.TokenPakTimeoutError = exports.TokenPakConnectionError = exports.TokenPakError = void 0;
// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------
class TokenPakError extends Error {
    constructor(message, statusCode, cause) {
        super(message);
        this.statusCode = statusCode;
        this.cause = cause;
        this.name = 'TokenPakError';
    }
}
exports.TokenPakError = TokenPakError;
class TokenPakConnectionError extends TokenPakError {
    constructor(baseUrl, cause) {
        super(`Cannot connect to TokenPak server at ${baseUrl}`, undefined, cause);
        this.name = 'TokenPakConnectionError';
    }
}
exports.TokenPakConnectionError = TokenPakConnectionError;
class TokenPakTimeoutError extends TokenPakError {
    constructor(timeoutMs) {
        super(`Request timed out after ${timeoutMs}ms`);
        this.name = 'TokenPakTimeoutError';
    }
}
exports.TokenPakTimeoutError = TokenPakTimeoutError;
//# sourceMappingURL=types.js.map