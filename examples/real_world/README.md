# Real-World Scenarios

Production patterns for common TokenPak use cases.

## Examples

| Example | Use Case | Relative Savings |
|---|---|---|
| [vector_compression.py](./vector_compression.py) | Compress RAG retrieval chunks within token budget | Moderate |
| [db_query_compression.py](./db_query_compression.py) | Compress database result text fields | Moderate–High |
| [api_response_compression.py](./api_response_compression.py) | Compress third-party API responses | High |

## Run Any Example

```bash
cd examples/real_world
pip install tokenpak
python vector_compression.py
python db_query_compression.py
python api_response_compression.py
```

## Vector Store (RAG)

Compress retrieved chunks to fit more context in your token budget:

```python
from tokenpak import HeuristicEngine
engine = HeuristicEngine()

# In your RAG pipeline, after retrieval:
for chunk in retrieved_chunks:
    chunk["text"] = engine.compact(chunk["text"])
    # Now inject into prompt — fits more chunks in same budget
```

## Database Results

Compress verbose text columns before LLM analysis:

```python
for row in cursor.fetchall():
    if "notes" in row and len(row["notes"]) > 100:
        row["notes"] = engine.compact(row["notes"])
```

## API Responses

Strip metadata + compress prose from REST/GraphQL responses:

```python
# Only extract what matters; compress prose fields
compressed = {
    "title": response["title"],
    "body": engine.compact(response["body"]),
    "status": response["status"],
}
```
