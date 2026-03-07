import { Block } from '../src/block';

describe('Block', () => {
  test('creates with required options', () => {
    const b = new Block({ type: 'instructions', content: 'hello' });
    expect(b.type).toBe('instructions');
    expect(b.content).toBe('hello');
    expect(b.priority).toBe('medium');
    expect(b.quality).toBe(1.0);
    expect(b.compacted).toBe(false);
    expect(b.id).toBeTruthy();
  });

  test('uses provided id', () => {
    const b = new Block({ type: 'code', id: 'my_id', content: 'x' });
    expect(b.id).toBe('my_id');
  });

  test('estimates token count', () => {
    const b = new Block({ type: 'knowledge', content: 'hello world foo bar baz' });
    expect(b.tokens).toBeGreaterThan(0);
  });

  test('uses provided token count', () => {
    const b = new Block({ type: 'code', content: 'x', tokens: 42 });
    expect(b.tokens).toBe(42);
  });

  test('serializes to JSON', () => {
    const b = new Block({ type: 'evidence', id: 'ev1', content: 'data', priority: 'high' });
    const raw = b.toJSON();
    expect(raw.type).toBe('evidence');
    expect(raw.id).toBe('ev1');
    expect(raw.content).toBe('data');
    expect(raw.priority).toBe('high');
  });

  test('round-trips JSON', () => {
    const b = new Block({ type: 'memory', id: 'mem1', content: 'remember this', priority: 'low', quality: 0.8 });
    const b2 = Block.fromJSON(b.toJSON());
    expect(b2.type).toBe('memory');
    expect(b2.id).toBe('mem1');
    expect(b2.content).toBe('remember this');
    expect(b2.priority).toBe('low');
    expect(b2.quality).toBe(0.8);
  });

  test('toString includes type and id', () => {
    const b = new Block({ type: 'system', id: 'sys1', content: 'x' });
    expect(b.toString()).toContain('system');
    expect(b.toString()).toContain('sys1');
  });
});
