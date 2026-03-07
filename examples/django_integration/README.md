# Django / FastAPI Integration Template

**Problem:** Your web app sends user chat history directly to LLM APIs, burning tokens on verbose or redundant content.

**Solution:** Add `LLMCompressionService` as a service layer or middleware. It compresses messages before they leave your app, with caching to avoid re-processing identical content.

## What This Shows

- `LLMCompressionService`: a reusable service class for any web framework
- Django middleware pattern (drop-in `MIDDLEWARE` entry)
- FastAPI dependency injection pattern
- Conversation history preparation with sliding window compression

## Setup

```bash
pip install -r requirements.txt
```

## Run Demo

```bash
python main.py
```

## Django Integration

```python
# settings.py
MIDDLEWARE = [
    ...
    'myapp.middleware.TokenPakMiddleware',
]
```

## FastAPI Integration

```python
from main import get_compression_service, LLMCompressionService
from fastapi import Depends

@app.post("/chat")
def chat(
    req: ChatRequest,
    svc: LLMCompressionService = Depends(get_compression_service)
):
    messages = svc.prepare_messages(req.messages, req.system_prompt)
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=messages,
    )
    return response
```

## Direct Service Usage

```python
service = LLMCompressionService(target_tokens=800)

# Compress a single string
compressed, cache_hit = service.compress(user_input)

# Prepare full conversation for LLM
messages = service.prepare_messages(chat_history, system_prompt="You are...")
```

## Time to Complete

~15 minutes
