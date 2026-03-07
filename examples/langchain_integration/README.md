# LangChain Integration Example

**Problem:** LangChain chains pass verbose documents, chat history, and retrieved context to LLMs — often wasting 40-60% of tokens on redundant content.

**Solution:** Drop-in TokenPak-backed components:
- `TokenPakChatMessageHistory`: Compresses older turns automatically
- `TokenPakDocumentCompressor`: Shrinks retrieved docs before they hit the LLM

## What This Shows

- LangChain-compatible `ChatMessageHistory` with automatic compression
- RAG pipeline document compression
- Compression that preserves recent context (sliding window)

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

## LangChain Integration

```python
from main import TokenPakChatMessageHistory, TokenPakDocumentCompressor
from langchain.chains import ConversationChain

# Drop-in for any LangChain memory
history = TokenPakChatMessageHistory(max_tokens=2000, keep_recent_turns=4)
messages = history.messages  # Automatically compressed when over budget

# RAG document compression
compressor = TokenPakDocumentCompressor(target_tokens=200)
compressed_docs = compressor.compress_documents(retrieved_docs, query)
```

## Token Budget Impact

| History | Without TokenPak | With TokenPak | Savings |
|---|---|---|---|
| 10 turns | ~2,000 | ~1,100 | 45% |
| 20 turns | ~4,000 | ~1,700 | 58% |

## Time to Complete

~15 minutes
