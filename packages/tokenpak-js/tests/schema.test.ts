import { validate } from '../src/schema';
import { TokenPak } from '../src/pack';
import { Block } from '../src/block';

function validWire() {
  const pack = new TokenPak({ metadata: { task: 'test', source: 'user:test' } });
  pack.add(new Block({ type: 'instructions', id: 'sys', content: 'hello' }));
  return pack.compile().toJSON();
}

describe('validate', () => {
  test('valid pack passes', () => {
    const result = validate(validWire());
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  test('rejects null', () => {
    const result = validate(null);
    expect(result.valid).toBe(false);
  });

  test('rejects missing header', () => {
    const wire = validWire();
    delete (wire as any).header;
    const result = validate(wire);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.path === 'header')).toBe(true);
  });

  test('rejects invalid header.version', () => {
    const wire = validWire();
    (wire as any).header.version = '2.0';
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects missing metadata', () => {
    const wire = validWire();
    delete (wire as any).metadata;
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects missing metadata.task', () => {
    const wire = validWire();
    delete (wire as any).metadata.task;
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects missing blocks array', () => {
    const wire = validWire();
    delete (wire as any).blocks;
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects empty blocks array', () => {
    const wire = validWire();
    (wire as any).blocks = [];
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects invalid block type', () => {
    const wire = validWire();
    (wire as any).blocks[0].type = 'invalid_type';
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects invalid priority', () => {
    const wire = validWire();
    (wire as any).blocks[0].priority = 'ultra';
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects invalid quality', () => {
    const wire = validWire();
    (wire as any).blocks[0].quality = 2.5;
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects duplicate block IDs', () => {
    const wire = validWire();
    wire.blocks.push({ ...wire.blocks[0] });
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });

  test('rejects block id with invalid chars', () => {
    const wire = validWire();
    (wire as any).blocks[0].id = 'bad id!';
    const result = validate(wire);
    expect(result.valid).toBe(false);
  });
});
