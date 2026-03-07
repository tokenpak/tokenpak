/**
 * vercel-edge.js — TokenPak on Vercel Edge Functions
 *
 * Deploy as: /api/chat.js in a Next.js app
 *
 * This pattern works with Cloudflare Workers too.
 */

// import { TokenPak, Block } from 'tokenpak';

export const config = { runtime: 'edge' };

export default async function handler(req) {
  const { messages, userQuestion } = await req.json();

  // Build a TokenPak from the conversation
  const pack = new TokenPak({
    budget: 4000,  // Leave room for model response
    metadata: {
      task: 'chat_response',
      source: 'service:edge_chat',
    },
  });

  // System instructions (always kept — critical)
  pack.add(new Block({
    type: 'instructions',
    id: 'system',
    content: 'You are a helpful assistant. Be concise.',
    priority: 'critical',
  }));

  // Recent conversation history (medium priority — may be dropped if over budget)
  if (messages?.length > 0) {
    const history = messages
      .slice(-10)  // Last 10 messages max
      .map(m => `${m.role}: ${m.content}`)
      .join('\n');

    pack.add(new Block({
      type: 'conversation',
      id: 'history',
      content: history,
      priority: 'medium',
    }));
  }

  // Current question (high priority — keep unless truly out of space)
  pack.add(new Block({
    type: 'evidence',
    id: 'current_question',
    content: userQuestion,
    priority: 'high',
  }));

  // Compile with budget enforcement
  const compiled = pack.compile();
  const systemPrompt = compiled.toPrompt({ includeMetadata: false });

  // Forward to your LLM API
  const response = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${process.env.OPENAI_API_KEY}`,
    },
    body: JSON.stringify({
      model: 'gpt-4o',
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userQuestion },
      ],
      max_tokens: 1000,
    }),
  });

  const result = await response.json();

  return new Response(
    JSON.stringify({
      reply: result.choices[0].message.content,
      tokenpak_id: compiled.id,
      tokens_used: compiled.totalTokens,
    }),
    { headers: { 'Content-Type': 'application/json' } }
  );
}
