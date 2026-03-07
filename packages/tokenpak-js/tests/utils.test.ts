import { estimateTokens, generateId, sanitizeId } from '../src/utils';

describe('estimateTokens', () => {
  test('empty string returns 0', () => {
    expect(estimateTokens('')).toBe(0);
  });

  test('estimates positive count for text', () => {
    expect(estimateTokens('hello world')).toBeGreaterThan(0);
  });

  test('longer text = more tokens', () => {
    const short = estimateTokens('hi');
    const long = estimateTokens('This is a much longer sentence with many words.');
    expect(long).toBeGreaterThan(short);
  });
});

describe('generateId', () => {
  test('generates unique IDs', () => {
    const ids = new Set(Array.from({ length: 100 }, () => generateId('pak')));
    expect(ids.size).toBe(100);
  });

  test('uses prefix', () => {
    const id = generateId('blk');
    expect(id.startsWith('blk_')).toBe(true);
  });
});

describe('sanitizeId', () => {
  test('removes invalid chars', () => {
    expect(sanitizeId('hello world!')).toBe('hello_world_');
  });

  test('keeps valid chars', () => {
    expect(sanitizeId('hello-world_1.2')).toBe('hello-world_1.2');
  });

  test('truncates to 128', () => {
    const long = 'a'.repeat(200);
    expect(sanitizeId(long).length).toBe(128);
  });
});
