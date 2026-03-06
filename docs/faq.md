# FAQ & Troubleshooting

---

## General

### Does TokenPak send my data anywhere?

No. TokenPak runs entirely locally. Your prompts, responses, API keys, and metadata never leave your machine. The proxy intercepts requests between your client and the provider, compresses them locally, and forwards them. There's no TokenPak cloud service involved.

### How does TokenPak affect my API key?

It doesn't. Your API key is in the `Authorization` header and is passed through to the provider unchanged. TokenPak never reads or stores it — it's opaque to the proxy.

### Can I use TokenPak with multiple providers at once?

Yes. TokenPak detects the provider from the `Authorization` header format and routes accordingly. You can use Anthropic and OpenAI simultaneously through the same proxy port.

### What's the performance overhead?

Minimal. Compression adds 10–50ms to requests that benefit from it (typically those over 4500 tokens). Small requests are passed through with near-zero overhead. Cold start overhead is under 100ms.

---

## Installation

### `pip install tokenpak` fails with Python version error

TokenPak requires Python 3.11+. Check your version:

```bash
python --version
# If < 3.11:
pip install "tokenpak>=0.1.0" --python-version 3.11
# Or use pyenv to install Python 3.11
```

### Permission errors on install

```bash
pip install --user tokenpak
# or use a virtual environment:
python -m venv .venv && source .venv/bin/activate
pip install tokenpak
```

---

## Proxy

### Proxy won't start — port already in use

```bash
# Check what's on 8766
lsof -i :8766

# Use a different port
tokenpak serve --port 8767

# Kill the existing process
tokenpak stop
```

### My LLM client gets connection refused

Make sure the proxy is running:

```bash
tokenpak status
# If not running:
tokenpak serve
```

Check the URL format for your client:

- Anthropic clients: `http://localhost:8766` (no `/v1`)
- OpenAI clients: `http://localhost:8766/v1`

### Requests are timing out

The proxy might be waiting on the provider. Check provider connectivity:

```bash
tokenpak doctor
```

If compression is adding too much latency on small requests:

```bash
tokenpak config set compression.mode strict
# Only compress requests over 4500 tokens
```

### I'm getting 401 Unauthorized errors

Your API key isn't reaching the provider. Debug:

```bash
tokenpak debug on --requests 1
# Make a request...
tokenpak debug off
tokenpak trace --last
# Check that Authorization header is present and unchanged
```

---

## Compression

### How do I know compression is working?

```bash
tokenpak status --full
# Should show: compression: enabled | mode: hybrid

# After making a request:
tokenpak cost --today
# Shows: saved X% via compression
```

Or watch the stats footer appended to each response:
```
[TokenPak: 4,231→2,847 tokens | saved 33% | $0.004]
```

### Some of my requests aren't being compressed

Normal — compression is only applied when beneficial. By design:

- Requests under the threshold (`compression.threshold_tokens`, default 4500) are passed through
- If the compressed version would only save <5% tokens, it's skipped
- Code blocks are preserved by default (lossy compression on code is risky)

To lower the threshold:

```bash
tokenpak config set compression.threshold_tokens 2000
```

### Compression seems to be changing my prompt

Check which recipe is firing:

```bash
tokenpak trace --last
# Shows: recipe: python-strip-comments, stages: [...]
```

If you're seeing unwanted changes, you can disable specific recipes:

```bash
tokenpak recipe remove python-strip-comments
```

Or disable compression for specific request patterns:

```json
{
  "compression": {
    "exclude_patterns": [".*system.*", ".*code-review.*"]
  }
}
```

---

## Cost & Telemetry

### The cost numbers look wrong

Check the model pricing config:

```bash
tokenpak config get pricing
```

Prices are periodically updated in the recipe definitions. If a model is missing:

```bash
tokenpak config set pricing.my-model.input_per_1k 0.003
tokenpak config set pricing.my-model.output_per_1k 0.015
```

### How do I reset cost history?

```bash
tokenpak prune --older-than 0d    # delete all history
# or
rm ~/.tokenpak/stats.db            # nuclear option
```

### Where is my data stored?

| Data | Location |
|------|----------|
| Configuration | `~/.tokenpak/config.json` |
| Session database | `.ocp/monitor.db` (or `TOKENPAK_DB` env var) |
| Vault index | `.tokenpak/registry.db` |
| Calibration profile | `~/.tokenpak/calibration.json` |
| Recipes | `~/.tokenpak/recipes/` |

---

## Indexing

### Indexing is slow

Run calibration first:

```bash
tokenpak calibrate ~/vault --max-workers 8 --rounds 2
tokenpak index ~/vault --auto-workers
```

### Vault search returns irrelevant results

Re-index your vault:

```bash
tokenpak index ~/vault --force
```

### Index is using too much disk space

```bash
tokenpak vault blocks --stale
tokenpak prune --older-than 30d
```

---

## Getting Help

```bash
tokenpak doctor        # comprehensive self-diagnosis
tokenpak logs --errors # recent errors
tokenpak --version     # version info for bug reports
```

File issues at: https://github.com/tokenpak/tokenpak/issues
