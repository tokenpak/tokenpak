/**
 * schema.ts — TokenPak Schema Validation
 *
 * Lightweight schema validation without external dependencies.
 * Validates packs against the TokenPak v1.0 schema rules.
 */

import { WireObject } from './compiled';

export interface ValidationError {
  path: string;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: ValidationError[];
}

const VALID_BLOCK_TYPES = new Set([
  'instructions', 'code', 'knowledge', 'memory',
  'conversation', 'evidence', 'system',
]);

const VALID_PRIORITIES = new Set([
  'critical', 'high', 'medium', 'low', 'internal',
]);

const VALID_COMPACTION_MODES = new Set([
  'lossless', 'balanced', 'aggressive', 'semantic',
]);

const ID_PATTERN = /^[a-zA-Z0-9_\-.]+$/;

/**
 * Validate a wire object against the TokenPak v1.0 schema.
 *
 * @example
 * const result = validate(compiled.toJSON());
 * if (!result.valid) {
 *   console.error(result.errors);
 * }
 */
export function validate(wire: unknown): ValidationResult {
  const errors: ValidationError[] = [];

  if (!wire || typeof wire !== 'object') {
    return { valid: false, errors: [{ path: '', message: 'Pack must be an object' }] };
  }

  const obj = wire as Record<string, unknown>;

  // header
  if (!obj.header || typeof obj.header !== 'object') {
    errors.push({ path: 'header', message: 'header is required' });
  } else {
    const h = obj.header as Record<string, unknown>;
    if (!h.version || typeof h.version !== 'string') {
      errors.push({ path: 'header.version', message: 'header.version is required' });
    } else if (!/^1\.\d+$/.test(h.version as string)) {
      errors.push({ path: 'header.version', message: `Invalid version: ${h.version}` });
    }
    if (!h.id || typeof h.id !== 'string') {
      errors.push({ path: 'header.id', message: 'header.id is required' });
    } else if ((h.id as string).length < 4 || (h.id as string).length > 128) {
      errors.push({ path: 'header.id', message: 'header.id must be 4–128 chars' });
    }
    if (!h.created || typeof h.created !== 'string') {
      errors.push({ path: 'header.created', message: 'header.created is required' });
    }
  }

  // metadata
  if (!obj.metadata || typeof obj.metadata !== 'object') {
    errors.push({ path: 'metadata', message: 'metadata is required' });
  } else {
    const m = obj.metadata as Record<string, unknown>;
    if (!m.task || typeof m.task !== 'string') {
      errors.push({ path: 'metadata.task', message: 'metadata.task is required' });
    }
    if (!m.source || typeof m.source !== 'string') {
      errors.push({ path: 'metadata.source', message: 'metadata.source is required' });
    }
  }

  // blocks
  if (!Array.isArray(obj.blocks)) {
    errors.push({ path: 'blocks', message: 'blocks must be an array' });
  } else if (obj.blocks.length === 0) {
    errors.push({ path: 'blocks', message: 'blocks must contain at least one item' });
  } else {
    for (let i = 0; i < obj.blocks.length; i++) {
      const b = obj.blocks[i] as Record<string, unknown>;
      const base = `blocks[${i}]`;

      if (!b.type || !VALID_BLOCK_TYPES.has(b.type as string)) {
        errors.push({ path: `${base}.type`, message: `Invalid block type: ${b.type}` });
      }
      if (!b.id || typeof b.id !== 'string') {
        errors.push({ path: `${base}.id`, message: 'Block id is required' });
      } else if (!ID_PATTERN.test(b.id as string)) {
        errors.push({ path: `${base}.id`, message: `Block id has invalid characters: ${b.id}` });
      }
      if (typeof b.content !== 'string') {
        errors.push({ path: `${base}.content`, message: 'Block content must be a string' });
      }
      if (b.priority !== undefined && !VALID_PRIORITIES.has(b.priority as string)) {
        errors.push({ path: `${base}.priority`, message: `Invalid priority: ${b.priority}` });
      }
      if (b.quality !== undefined) {
        const q = b.quality as number;
        if (typeof q !== 'number' || q < 0 || q > 1) {
          errors.push({ path: `${base}.quality`, message: 'Block quality must be 0.0–1.0' });
        }
      }
    }

    // Check for duplicate block IDs
    const ids = (obj.blocks as Record<string, unknown>[]).map(b => b.id);
    const seen = new Set<unknown>();
    for (const id of ids) {
      if (seen.has(id)) {
        errors.push({ path: 'blocks', message: `Duplicate block id: ${id}` });
      }
      seen.add(id);
    }
  }

  // policies (optional)
  if (obj.policies) {
    const p = obj.policies as Record<string, unknown>;
    if (p.compaction) {
      const c = p.compaction as Record<string, unknown>;
      if (c.mode && !VALID_COMPACTION_MODES.has(c.mode as string)) {
        errors.push({ path: 'policies.compaction.mode', message: `Invalid mode: ${c.mode}` });
      }
    }
  }

  return { valid: errors.length === 0, errors };
}
