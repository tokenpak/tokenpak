# Google Gemini Adapter

TokenPak supports Google Gemini models via the OpenAI-compatible endpoint.

## Setup

```bash
export GEMINI_API_KEY=your-gemini-api-key
```

## Python (via openai SDK)

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8766/v1",
    api_key="your-gemini-api-key",
)

response = client.chat.completions.create(
    model="gemini-1.5-flash",
    messages=[{"role": "user", "content": "Hello from Gemini!"}],
)
print(response.choices[0].message.content)
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | Google AI Studio or Vertex AI key |
| `TOKENPAK_MODE` | `hybrid` | Compression mode (same as other providers) |

## Notes

- Gemini API uses an OpenAI-compatible `/v1/chat/completions` format via Google AI Studio
- For Vertex AI, set `TOKENPAK_VERTEX_PROJECT` and `TOKENPAK_VERTEX_LOCATION`
- Model names: `gemini-1.5-flash`, `gemini-1.5-pro`, `gemini-2.0-flash-exp`
