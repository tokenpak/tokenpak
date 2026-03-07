/**
 * node-basic.js — Basic TokenPak usage in Node.js
 *
 * Run: node node-basic.js
 */

// When published: const { TokenPak, Block, Policy, validate } = require('tokenpak');
const path = require('path');
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { TokenPak, Block, Policy, validate } = require('../dist/index.js');

// 1. Create a pack with a budget
const pack = new TokenPak({
  budget: 8000,
  metadata: {
    task: 'customer_support',
    source: 'agent:support_bot',
  },
});

// 2. Add blocks
pack.add(new Block({
  type: 'instructions',
  id: 'system_prompt',
  content: 'You are a helpful customer support assistant. Be concise and friendly.',
  priority: 'critical',
}));

pack.add(new Block({
  type: 'knowledge',
  id: 'product_docs',
  content: 'TokenPak is the context standard for AI systems. It helps manage token budgets efficiently.',
  priority: 'high',
}));

pack.add(new Block({
  type: 'conversation',
  id: 'recent_chat',
  content: 'User: How do I install TokenPak?\nAssistant: Run `npm install tokenpak`.',
  priority: 'medium',
}));

// 3. Compile
const compiled = pack.compile();

console.log('Pack ID:', compiled.id);
console.log('Blocks:', compiled.blockCount);
console.log('Total tokens:', compiled.totalTokens);

// 4. Get prompt for LLM
const prompt = compiled.toPrompt();
console.log('\n=== Prompt ===');
console.log(prompt);

// 5. Wire format for transport
const wire = compiled.toWire();
console.log('\n=== Wire format (truncated) ===');
console.log(wire.slice(0, 200) + '...');

// 6. Validate
const result = validate(compiled.toJSON());
console.log('\nValidation:', result.valid ? '✅ valid' : '❌ invalid', result.errors);

// 7. Round-trip
const pack2 = TokenPak.fromWire(wire);
console.log('\nRound-trip blocks:', pack2.blockCount);
