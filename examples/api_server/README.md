# API Server Integration Example

**Problem:** Every token sent to LLM APIs costs money. Verbose inputs from users or automated pipelines waste budget.

**Solution:** A FastAPI server that sits between your app and the LLM, automatically compressing content with TokenPak before forwarding.

## What This Shows

- FastAPI compression proxy server
- Content-addressed caching for deduplication
- Conversation history compression endpoint
- Real-time savings tracking

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

Then visit http://localhost:8000/docs for the interactive API.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check + stats |
| `/compress` | POST | Compress a single text block |
| `/compress/conversation` | POST | Compress conversation history |
| `/stats` | GET | Cumulative savings stats |

## Example Request

```bash
curl -X POST http://localhost:8000/compress \
  -H "Content-Type: application/json" \
  -d '{"text": "Your verbose text here...", "target_tokens": 200}'
```

## Example Response

```json
{
  "original_text": "Your verbose text here...",
  "compressed_text": "Compressed version...",
  "original_tokens": 450,
  "compressed_tokens": 180,
  "savings_pct": 60.0,
  "cache_hit": false,
  "elapsed_ms": 8.3
}
```

## Time to Complete

~15 minutes
