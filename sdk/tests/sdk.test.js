/**
 * TokenPak SDK — Unit Tests (compiled JS)
 * Tests imports, class instantiation, and non-HTTP logic.
 * Does NOT require a live server.
 */

const {
  CompressionEngine,
  CacheManager,
  BlockRegistry,
  TelemetryCollector,
  TokenPakHttpClient,
  TokenPakError,
  TokenPakConnectionError,
  TokenPakTimeoutError,
  VERSION,
} = require('../dist/index');

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------
describe('SDK exports', () => {
  test('VERSION is a non-empty string', () => {
    expect(typeof VERSION).toBe('string');
    expect(VERSION.length).toBeGreaterThan(0);
  });

  test('all main classes are exported', () => {
    expect(CompressionEngine).toBeDefined();
    expect(CacheManager).toBeDefined();
    expect(BlockRegistry).toBeDefined();
    expect(TelemetryCollector).toBeDefined();
    expect(TokenPakHttpClient).toBeDefined();
  });

  test('error classes are exported', () => {
    expect(TokenPakError).toBeDefined();
    expect(TokenPakConnectionError).toBeDefined();
    expect(TokenPakTimeoutError).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Error classes
// ---------------------------------------------------------------------------
describe('Error classes', () => {
  test('TokenPakError is an instance of Error', () => {
    const err = new TokenPakError('test error');
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(TokenPakError);
    expect(err.message).toBe('test error');
    expect(err.name).toBe('TokenPakError');
  });

  test('TokenPakConnectionError extends TokenPakError with correct message', () => {
    const err = new TokenPakConnectionError('http://localhost:9999');
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(TokenPakError);
    expect(err).toBeInstanceOf(TokenPakConnectionError);
    expect(err.message).toContain('http://localhost:9999');
    expect(err.name).toBe('TokenPakConnectionError');
  });

  test('TokenPakTimeoutError extends TokenPakError with correct message', () => {
    const err = new TokenPakTimeoutError(5000);
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(TokenPakError);
    expect(err).toBeInstanceOf(TokenPakTimeoutError);
    expect(err.message).toContain('5000');
    expect(err.name).toBe('TokenPakTimeoutError');
  });
});

// ---------------------------------------------------------------------------
// Instantiation (no HTTP calls needed)
// ---------------------------------------------------------------------------
describe('Class instantiation', () => {
  test('CompressionEngine can be constructed with no args', () => {
    const engine = new CompressionEngine();
    expect(engine).toBeDefined();
    expect(typeof engine.compress).toBe('function');
    expect(typeof engine.compressConversation).toBe('function');
  });

  test('CompressionEngine can be constructed with config', () => {
    const engine = new CompressionEngine({ baseUrl: 'http://localhost:9999', timeout: 5000 });
    expect(engine).toBeDefined();
  });

  test('CacheManager can be constructed', () => {
    const cache = new CacheManager({ baseUrl: 'http://localhost:9999' });
    expect(cache).toBeDefined();
    expect(typeof cache.get).toBe('function');
    expect(typeof cache.set).toBe('function');
    expect(typeof cache.delete).toBe('function');
    expect(typeof cache.clear).toBe('function');
    expect(typeof cache.stats).toBe('function');
  });

  test('BlockRegistry can be constructed', () => {
    const registry = new BlockRegistry({ baseUrl: 'http://localhost:9999' });
    expect(registry).toBeDefined();
    expect(typeof registry.register).toBe('function');
    expect(typeof registry.get).toBe('function');
    expect(typeof registry.list).toBe('function');
    expect(typeof registry.delete).toBe('function');
  });

  test('TelemetryCollector can be constructed', () => {
    const telemetry = new TelemetryCollector({ baseUrl: 'http://localhost:9999' });
    expect(telemetry).toBeDefined();
    expect(typeof telemetry.record).toBe('function');
    expect(typeof telemetry.stats).toBe('function');
  });

  test('TokenPakHttpClient can be constructed with custom config', () => {
    const client = new TokenPakHttpClient({
      baseUrl: 'http://localhost:9999',
      timeout: 1000,
      apiKey: 'test-key',
      headers: { 'X-Custom': 'value' },
    });
    expect(client).toBeDefined();
  });
});
