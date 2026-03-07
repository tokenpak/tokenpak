import { Policy } from '../src/policy';

describe('Policy', () => {
  test('defaults to balanced mode', () => {
    const p = new Policy();
    expect(p.mode).toBe('balanced');
  });

  test('creates with options', () => {
    const p = new Policy({ mode: 'aggressive', maxTokens: 4000, priorityOrder: ['instructions', 'code'] });
    expect(p.mode).toBe('aggressive');
    expect(p.maxTokens).toBe(4000);
    expect(p.priorityOrder).toEqual(['instructions', 'code']);
  });

  test('serializes to JSON wire format', () => {
    const p = new Policy({ mode: 'lossless', maxTokens: 8000 });
    const j = p.toJSON();
    expect((j.compaction as any).mode).toBe('lossless');
    expect((j.compaction as any).max_tokens).toBe(8000);
  });

  test('round-trips JSON', () => {
    const p = new Policy({ mode: 'semantic', maxTokens: 6000, priorityOrder: ['instructions'] });
    const p2 = Policy.fromJSON(p.toJSON());
    expect(p2.mode).toBe('semantic');
    expect(p2.maxTokens).toBe(6000);
    expect(p2.priorityOrder).toEqual(['instructions']);
  });

  test('budget budget from maxTokens when no budget specified', () => {
    const p = new Policy({ maxTokens: 4000 });
    const j = p.toJSON();
    expect((j.budget as any).total).toBe(4000);
  });
});
