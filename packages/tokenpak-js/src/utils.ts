/**
 * utils.ts — Utility functions for TokenPak JS SDK
 */

const CHARS_PER_TOKEN = 4;

/**
 * Estimate token count for a string.
 * Uses a simple character-based approximation (chars / 4).
 * For production use, consider integrating tiktoken.
 */
export function estimateTokens(text: string): number {
  if (!text || text.length === 0) return 0;
  // Better approximation: count words + overhead for whitespace/punctuation
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  const chars = text.length;
  // Blend word-based and char-based estimates
  const wordBased = Math.ceil(words * 1.3);
  const charBased = Math.ceil(chars / CHARS_PER_TOKEN);
  return Math.max(wordBased, charBased);
}

/**
 * Generate a random ID with a given prefix.
 */
export function generateId(prefix = 'pak'): string {
  const bytes = new Uint8Array(8);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    // Node.js fallback
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  const hex = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
  return `${prefix}_${hex}`;
}

/**
 * Get current ISO 8601 UTC timestamp.
 */
export function nowISO(): string {
  return new Date().toISOString();
}

/**
 * Sanitize a string for use as a block ID.
 */
export function sanitizeId(id: string): string {
  return id.replace(/[^a-zA-Z0-9_\-.]/g, '_').slice(0, 128);
}
