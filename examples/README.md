# TokenPak Python Examples

Practical, copy-paste-ready Python examples for common TokenPak SDK workflows.
These scripts are intentionally local-first and runnable without external API calls.
Use them to understand compression, caching, metrics, and proxy cost behavior.

## Quick start

For users who installed TokenPak from PyPI, the top-level repository examples
are intentionally delivered through the public source tree, not bundled inside
the PyPI wheel. Clone or download the repository for the example files, then
install the package normally in an isolated environment:

```bash
git clone https://github.com/tokenpak/tokenpak.git
cd tokenpak
python -m venv .venv
source .venv/bin/activate
python -m pip install -U tokenpak
python examples/basic_compression.py
```

`basic_compression.py` is deterministic and runs without provider credentials.

For source contributors who want imports to resolve to the checked-out code
instead of the installed package:

```bash
python -m pip install -e .
python examples/basic_compression.py
```

You can also download the source archive from
`https://github.com/tokenpak/tokenpak/archive/refs/heads/main.zip` and run the
same commands from the extracted directory.

## Examples at a glance

### 1) `basic_compression.py`
**Use case:** First-time SDK users who want a minimal compress workflow.

What it shows:
- Create a local `TokenPakClient`
- Compress a large prompt string
- Compute token savings with `count_tokens`
- Print a simple cost/savings-style summary

Run:
```bash
python examples/basic_compression.py
```

Expected output (shape):
- Original tokens: <int>
- Compressed tokens: <int>
- Saved <pct>% tokens

---

### 2) `streaming_compression.py`
**Use case:** Very large context payloads where chunking improves memory behavior.

What it shows:
- Split text into chunks with an iterator
- Compress each chunk independently
- Track peak in-memory chunk token count
- Stitch chunk outputs into one compressed payload

Run:
```bash
python examples/streaming_compression.py
```

Expected output (shape):
- Input tokens: <int>
- Output tokens: <int>
- Peak chunk tokens: <int>
- Memory efficiency note

---

### 3) `cache_usage.py`
**Use case:** Repeated prompts in agents, workers, or scheduled jobs.

What it shows:
- Prompt-keyed cache for repeated requests
- Hit/miss tracking over a sample workload
- Final hit-rate reporting
- Fast repeated response behavior

Run:
```bash
python examples/cache_usage.py
```

Expected output (shape):
- Prompt-by-prompt logs
- Cache stats block
- Hits / Misses / Hit rate

---

### 4) `metrics_collection.py`
**Use case:** Compare savings across models for observability and routing policy.

What it shows:
- Per-model sample rows
- Savings calculation per model
- Aggregate savings across all samples
- Compact report-table style output

Run:
```bash
python examples/metrics_collection.py
```

Expected output (shape):
- `Model metrics` header
- One row per model
- Overall savings summary

---

### 5) `with_proxy.py`
**Use case:** Local proxy deployments (e.g., `localhost:8766`) with cost accounting.

What it shows:
- Configure a client with `base_url`
- Estimate direct model cost vs compressed proxy cost
- Print estimated dollar savings
- Demonstrate integration pattern without network dependency

Run:
```bash
python examples/with_proxy.py
```

Expected output (shape):
- Proxy endpoint line
- Direct model cost
- Proxy-compressed cost
- Estimated savings

## Notes

- These examples are educational and deterministic by design.
- No external API calls are required for execution.
- Safe to run in CI or local dev environments.

## Setting up API Keys

TokenPak proxy passes your API calls to upstream providers (Anthropic, OpenAI, Google, etc.).

### Option A: Proxy passes through your key (default)

Set your API keys as environment variables before running these examples:

```bash
# For Anthropic/Claude
export ANTHROPIC_API_KEY="sk-ant-..."

# For OpenAI
export OPENAI_API_KEY="sk-..."

# For Google Gemini
export GEMINI_API_KEY="your-gemini-key"
```

Then in your Python code, use the proxy as the base URL:

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8766",  # TokenPak proxy
    api_key="sk-ant-...",  # Your real API key
)
```

### Option B: Proxy validates incoming requests (advanced)

For multi-user or production setups, you can configure the proxy to require and validate API keys. See the main README for advanced proxy configuration.

## API docs

- Project README: `../README.md`
- Python package source: `../tokenpak/`
- CLI usage: `../tokenpak/cli.py`
- Test suite reference: `../tests/`
