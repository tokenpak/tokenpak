# Recipe: Model Routing by Use Case

**What this solves:** Automatically route requests to the best model for the task — GPT-4 for code generation, Claude Haiku for simple chat — without application code changes.

## Prerequisites
- TokenPak installed
- API keys for multiple providers (OpenAI, Anthropic)
- Understanding of your models' strengths (GPT-4 = code, Claude = reasoning, Haiku = chat)

## Config Snippet

```yaml
# config.yaml
providers:
  openai:
    type: openai
    api_key: ${OPENAI_API_KEY}

  anthropic:
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}

models:
  gpt-4:
    provider: openai
    cost_per_1k_input: 3
    cost_per_1k_output: 6

  gpt-3.5-turbo:
    provider: openai
    cost_per_1k_input: 0.5
    cost_per_1k_output: 1.5

  claude-opus:
    provider: anthropic
    cost_per_1k_input: 15
    cost_per_1k_output: 75

  claude-haiku:
    provider: anthropic
    cost_per_1k_input: 0.25
    cost_per_1k_output: 1.25

# Use-case routing: map request intent to best model
routing:
  enabled: true

  # Detect use case from system prompt keywords
  routes:
    - name: code_generation
      keywords: [code, function, class, debug, refactor, algorithm, optimize]
      preferred_model: gpt-4
      fallback_model: gpt-3.5-turbo

    - name: reasoning_heavy
      keywords: [reason, explain, think, complex, architecture, strategy]
      preferred_model: claude-opus
      fallback_model: claude-haiku

    - name: simple_chat
      keywords: [greet, hello, chat, casual, small-talk]
      preferred_model: claude-haiku
      fallback_model: gpt-3.5-turbo

    - name: creative_writing
      keywords: [write, story, poem, creative, fiction, narrative]
      preferred_model: claude-opus
      fallback_model: gpt-4

    - name: default
      # Catch-all: balanced cost/quality
      preferred_model: gpt-3.5-turbo
      fallback_model: claude-haiku

# Cost optimization: route to cheaper model if budget tight
cost_aware_routing:
  enabled: true
  # If daily budget >80% consumed, downgrade to cheaper model
  degrade_above_budget_pct: 80
  degradation_map:
    gpt-4: gpt-3.5-turbo
    claude-opus: claude-haiku
```

## Test & Verify

**Step 1:** Validate config:
```bash
tokenpak validate-config config.yaml
# Expected output:
# ✓ Config valid
# ✓ Routing: 5 routes configured (code_generation, reasoning_heavy, simple_chat, creative_writing, default)
# ✓ Fallbacks configured for all routes
```

**Step 2:** Test code generation route:
```bash
tokenpak proxy --config config.yaml

# Code-related prompt
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "routing/code_generation",
    "messages": [
      {
        "role": "system",
        "content": "You are an expert Python developer. Debug this function."
      },
      {
        "role": "user",
        "content": "Why is this code slow? [function code here]"
      }
    ]
  }' -s | jq '{model_used: .model, provider: .provider}'

# Expected output:
# {
#   "model_used": "gpt-4",
#   "provider": "openai"
# }
```

**Step 3:** Test simple chat route:
```bash
# Simple greeting
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "routing/simple_chat",
    "messages": [
      {
        "role": "system",
        "content": "You are a friendly chatbot. Greet the user casually."
      },
      {
        "role": "user",
        "content": "Hello, how are you?"
      }
    ]
  }' -s | jq '{model_used: .model, provider: .provider}'

# Expected output:
# {
#   "model_used": "claude-haiku",
#   "provider": "anthropic"
# }
```

**Step 4:** Test reasoning route:
```bash
# Complex reasoning prompt
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "routing/reasoning_heavy",
    "messages": [
      {
        "role": "system",
        "content": "You are a strategic thinker. Reason through this architecture decision."
      },
      {
        "role": "user",
        "content": "How should we design our API gateway strategy?"
      }
    ]
  }' -s | jq '{model_used: .model, provider: .provider}'

# Expected output:
# {
#   "model_used": "claude-opus",
#   "provider": "anthropic"
# }
```

**Step 5:** Verify cost savings over time:
```bash
# Check usage report
tokenpak usage-report --period day
# Expected output showing cost per use case:
# code_generation (gpt-4):    5 req, $0.45
# reasoning_heavy (claude-opus): 3 req, $0.12
# simple_chat (claude-haiku):  12 req, $0.04
# creative_writing (claude-opus): 2 req, $0.08
# default (gpt-3.5-turbo):     8 req, $0.06
# TOTAL: $0.75 (60% cheaper than always using gpt-4)
```

## What Just Happened

TokenPak analyzed incoming requests and automatically selected the right model:

1. **Request arrives** with system prompt containing keywords
2. **Keyword matching** against configured routes (code_generation, reasoning_heavy, etc.)
3. **Model selection** based on matched route (e.g., code → gpt-4)
4. **Fallback routing** if preferred model unavailable (code → gpt-3.5-turbo)
5. **Cost check** — if budget tight, downgrade to cheaper alternative (gpt-4 → gpt-3.5-turbo)

Your application doesn't need to know about model selection — TokenPak handles it transparently, optimizing for cost and quality simultaneously.

## Common Pitfalls

**Pitfall 1: Keywords are too broad**
- ❌ Wrong: `keywords: [write]` matches everything (emails, logs, responses)
- ✅ Right: Narrow context: `[write, story, creative, fiction]` for creative writing only

**Pitfall 2: No fallback model**
- ❌ Wrong: Preferred model unavailable → request fails
- ✅ Right: Always have a fallback: `preferred_model: gpt-4, fallback_model: gpt-3.5-turbo`

**Pitfall 3: Cost-aware degradation is too aggressive**
- ❌ Wrong: Degrade to Haiku at 50% budget (degrades too early, bad user experience)
- ✅ Right: Degrade at 80-90% budget, giving yourself room to recover

**Pitfall 4: Routing rules contradict each other**
- ❌ Wrong: "code" → gpt-4, but "optimize code" → claude-haiku
- ✅ Right: Clear hierarchy: longest match wins, or explicit priority order

**Pitfall 5: Keyword matching on entire message**
- ❌ Wrong: Matches body text (too noisy, catches false positives)
- ✅ Right: Match on system prompt only (intent signal, not user content)
