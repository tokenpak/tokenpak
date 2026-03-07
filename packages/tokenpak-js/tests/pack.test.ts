import { TokenPak } from '../src/pack';
import { Block } from '../src/block';
import { Policy } from '../src/policy';

describe('TokenPak', () => {
  test('creates empty pack', () => {
    const pack = new TokenPak();
    expect(pack.blockCount).toBe(0);
    expect(pack.totalTokens).toBe(0);
    expect(pack.id).toBeTruthy();
  });

  test('adds blocks', () => {
    const pack = new TokenPak();
    pack.add(new Block({ type: 'instructions', content: 'hello' }));
    pack.add({ type: 'knowledge', content: 'world' });
    expect(pack.blockCount).toBe(2);
  });

  test('chaining add', () => {
    const pack = new TokenPak();
    pack
      .add(new Block({ type: 'instructions', content: 'a' }))
      .add(new Block({ type: 'code', content: 'b' }));
    expect(pack.blockCount).toBe(2);
  });

  test('gets block by id', () => {
    const pack = new TokenPak();
    pack.add(new Block({ type: 'code', id: 'myblock', content: 'x' }));
    const found = pack.get('myblock');
    expect(found).toBeDefined();
    expect(found!.id).toBe('myblock');
  });

  test('removes block by id', () => {
    const pack = new TokenPak();
    pack.add(new Block({ type: 'code', id: 'r1', content: 'x' }));
    const removed = pack.remove('r1');
    expect(removed).toBe(true);
    expect(pack.blockCount).toBe(0);
  });

  test('remove returns false if not found', () => {
    const pack = new TokenPak();
    expect(pack.remove('nonexistent')).toBe(false);
  });

  test('filters blocks by type', () => {
    const pack = new TokenPak();
    pack.add(new Block({ type: 'instructions', content: 'a' }));
    pack.add(new Block({ type: 'code', content: 'b' }));
    pack.add(new Block({ type: 'code', content: 'c' }));
    expect(pack.getBlocks('code').length).toBe(2);
    expect(pack.getBlocks('instructions').length).toBe(1);
  });

  test('computes budget remaining', () => {
    const pack = new TokenPak({ budget: 1000 });
    pack.add(new Block({ type: 'instructions', content: 'x', tokens: 100 }));
    expect(pack.remainingBudget).toBe(900);
  });

  test('compiles to CompiledPack', () => {
    const pack = new TokenPak({ budget: 8000 });
    pack.add(new Block({ type: 'instructions', id: 'sys', content: 'You are helpful.' }));
    const compiled = pack.compile();
    expect(compiled.id).toBe(pack.id);
    expect(compiled.blockCount).toBe(1);
    expect(compiled.totalTokens).toBeGreaterThan(0);
  });

  test('drops low-priority blocks when over budget', () => {
    const pack = new TokenPak({ budget: 10 });
    pack.add(new Block({ type: 'instructions', content: 'critical!', priority: 'critical', tokens: 5 }));
    pack.add(new Block({ type: 'knowledge', content: 'not important', priority: 'low', tokens: 100 }));
    const compiled = pack.compile();
    // Should keep critical, drop low
    expect(compiled.blockCount).toBe(1);
    expect(compiled.toJSON().blocks[0].priority).toBe('critical');
  });

  test('never drops critical blocks', () => {
    const pack = new TokenPak({ budget: 1 });
    pack.add(new Block({ type: 'instructions', content: 'x', priority: 'critical', tokens: 500 }));
    const compiled = pack.compile();
    expect(compiled.blockCount).toBe(1);
  });

  test('round-trips JSON', () => {
    const pack = new TokenPak({ budget: 4000, metadata: { task: 'test', source: 'agent:cali' } });
    pack.add(new Block({ type: 'instructions', id: 's1', content: 'hello', priority: 'high' }));
    const wire = pack.toWire();
    const pack2 = TokenPak.fromWire(wire);
    expect(pack2.id).toBe(pack.id);
    expect(pack2.blockCount).toBe(1);
    expect(pack2.get('s1')?.content).toBe('hello');
  });

  test('merges packs', () => {
    const p1 = new TokenPak();
    p1.add(new Block({ type: 'instructions', content: 'a' }));
    const p2 = new TokenPak();
    p2.add(new Block({ type: 'code', content: 'b' }));
    const merged = TokenPak.merge([p1, p2]);
    expect(merged.blockCount).toBe(2);
  });

  test('toPrompt renders blocks', () => {
    const pack = new TokenPak({ metadata: { task: 'demo', source: 'user:test' } });
    pack.add(new Block({ type: 'instructions', id: 's1', content: 'Be helpful.' }));
    const prompt = pack.toPrompt();
    expect(prompt).toContain('INSTRUCTIONS');
    expect(prompt).toContain('Be helpful.');
  });

  test('uses provided policy', () => {
    const policy = new Policy({ mode: 'aggressive', maxTokens: 2000 });
    const pack = new TokenPak({ policy });
    expect(pack.policy?.mode).toBe('aggressive');
    expect(pack.budget).toBe(2000);
  });
});
