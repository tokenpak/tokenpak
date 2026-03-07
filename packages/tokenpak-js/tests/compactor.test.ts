import { applyBudget, sortByPriority, truncateBlock } from '../src/compactor';
import { Block } from '../src/block';

function makeBlock(priority: Block['priority'], tokens: number, id: string): Block {
  return new Block({ type: 'knowledge', id, content: 'x'.repeat(tokens * 4), priority, tokens });
}

describe('applyBudget', () => {
  test('returns unchanged if under budget', () => {
    const blocks = [
      makeBlock('high', 100, 'b1'),
      makeBlock('medium', 100, 'b2'),
    ];
    const result = applyBudget(blocks, 500);
    expect(result.blocks).toHaveLength(2);
    expect(result.tokensRemoved).toBe(0);
    expect(result.blocksDropped).toBe(0);
  });

  test('drops internal priority first', () => {
    const blocks = [
      makeBlock('critical', 50, 'b1'),
      makeBlock('internal', 200, 'b2'),
    ];
    const result = applyBudget(blocks, 100, 'balanced');
    expect(result.blocks.every(b => b.priority !== 'internal')).toBe(true);
    expect(result.blocksDropped).toBe(1);
  });

  test('drops low priority when balanced', () => {
    const blocks = [
      makeBlock('high', 50, 'b1'),
      makeBlock('low', 200, 'b2'),
      makeBlock('internal', 50, 'b3'),
    ];
    const result = applyBudget(blocks, 60, 'balanced');
    const remaining = result.blocks.map(b => b.id);
    expect(remaining).toContain('b1');
    expect(remaining).not.toContain('b2');
  });

  test('never drops critical', () => {
    const blocks = [
      makeBlock('critical', 500, 'c1'),
      makeBlock('low', 10, 'l1'),
    ];
    const result = applyBudget(blocks, 100, 'aggressive');
    expect(result.blocks.some(b => b.id === 'c1')).toBe(true);
  });

  test('lossless mode only drops internal', () => {
    const blocks = [
      makeBlock('low', 200, 'l1'),
      makeBlock('internal', 100, 'i1'),
    ];
    const result = applyBudget(blocks, 150, 'lossless');
    // Only internal should be dropped
    expect(result.blocks.some(b => b.id === 'l1')).toBe(true);
    expect(result.blocks.every(b => b.id !== 'i1')).toBe(true);
  });
});

describe('sortByPriority', () => {
  test('sorts critical first', () => {
    const blocks = [
      makeBlock('low', 10, 'l'),
      makeBlock('critical', 10, 'c'),
      makeBlock('medium', 10, 'm'),
    ];
    const sorted = sortByPriority(blocks);
    expect(sorted[0].id).toBe('c');
  });
});

describe('truncateBlock', () => {
  test('does not truncate if within budget', () => {
    const b = new Block({ type: 'knowledge', content: 'hello', tokens: 2 });
    const result = truncateBlock(b, 10);
    expect(result.content).toBe('hello');
  });

  test('truncates long content', () => {
    const longContent = 'a'.repeat(10000);
    const b = new Block({ type: 'knowledge', content: longContent, tokens: 2500 });
    const result = truncateBlock(b, 100);
    expect(result.content.length).toBeLessThan(longContent.length);
    expect(result.content).toContain('[... truncated]');
    expect(result.compacted).toBe(true);
  });
});
