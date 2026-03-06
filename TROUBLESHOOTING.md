# TokenPak Troubleshooting & FAQ

---

## Common Questions

### Is my data private?

Yes — completely. TokenPak runs as a **local proxy on your machine**. Your prompts, API keys, and responses never reach TokenPak servers. Compression happens in-process before the request is forwarded to your provider.

The only outbound connection TokenPak makes is the one you'd make anyway: your request to Anthropic/OpenAI/Google, with the compressed payload.

Anonymous usage metrics are **opt-in only** (`TOKENPAK_METRICS_ENABLED=1`). If you haven't set that, nothing is reported.

---

### How much will TokenPak save me?

It depends on your usage pattern:

| Use case | Typical savings |
|---|---|
| Short Q&A / chat | 5–15% |
| Code review with large context | 30–50% |
| Long document analysis | 40–60% |
| Codebase search + compressed context | Up to 84% (with vault indexing) |

Compression only activates above the threshold (default: 4,500 tokens). Small requests pass through unchanged.

Check your actual savings:

```bash
tokenpak cost --week
tokenpak savings --lifetime
```

---

### Why aren't my requests being compressed?

A few common reasons:

1. **Request is below threshold** — compression activates at 4,500 tokens by default.
   ```bash
   # Lower the threshold
   TOKENPAK_COMPACT_THRESHOLD_TOKENS=1000 tokenpak serve
   ```

2. **Compression is disabled** — check:
   ```bash
   tokenpak status
   # Look for "Compression: enabled"
   ```
   If disabled:
   ```bash
   TOKENPAK_COMPACT=1 tokenpak serve
   ```

3. **Mode is not active** — verify:
   ```bash
   tokenpak config get compression.level
   ```

4. **Request isn't routing through the proxy** — confirm your client is using the proxy base URL:
   ```bash
   echo $ANTHROPIC_BASE_URL   # should be http://localhost:8766
   ```

---

### Does TokenPak change my responses?

No. TokenPak compresses the *input* (your prompts and context), not the output. The response from the LLM is forwarded to your client unchanged.

Optionally, TokenPak can append a one-line stats footer to responses:

```bash
TOKENPAK_STATS_FOOTER=1 tokenpak serve
# ⚡ TokenPak: -1,847 tokens (38%) | $0.014 saved
```

Disable by unsetting or setting `TOKENPAK_STATS_FOOTER=0`.

---

### Can I use TokenPak with multiple providers at once?

Yes. A single proxy instance handles Anthropic, OpenAI, and Google simultaneously. TokenPak detects the provider from the `Authorization` header format:

- `Bearer sk-ant-...` → Anthropic
- `Bearer sk-...` → OpenAI  
- `Bearer AIza...` → Google

---

### Will TokenPak break if a provider changes their API?

The proxy is a transparent passthrough — it only reads/modifies the request body for compression, then forwards everything else as-is (headers, auth, paths). Provider API changes in response format won't break it. If a new request format is introduced, the worst case is that compression is skipped and the request passes through unmodified.

---

## Installation Issues

### `pip install tokenpak` fails with Python version error

```
ERROR: tokenpak requires Python >=3.10
```

Check your Python version:

```bash
python --version
```

If you're on an older version, use pyenv or a virtual environment with Python 3.10+:

```bash
pyenv install 3.11.0
pyenv local 3.11.0
pip install tokenpak
```

---

### `tokenpak: command not found` after install

Your pip scripts directory isn't in `PATH`. Find it:

```bash
python -m site --user-base
# e.g. /home/user/.local

# Add to PATH:
export PATH="$HOME/.local/bin:$PATH"
```

Add to `~/.bashrc` or `~/.zshrc` to persist.

---

### Permission errors on install

```bash
# User install (no sudo needed)
pip install --user tokenpak

# Or use a virtual environment
python -m venv .venv
source .venv/bin/activate
pip install tokenpak
```

---

## Proxy Issues

### Proxy won't start — address already in use

```
OSError: [Errno 98] Address already in use
```

Check what's on port 8766:

```bash
lsof -i :8766
# or
ss -tlnp | grep 8766
```

Options:
- Stop the existing process: `tokenpak stop`
- Use a different port: `tokenpak serve --port 8767`

---

### Requests return "Connection refused"

1. Make sure the proxy is running:
   ```bash
   tokenpak status
   ```

2. Check the URL your client is using — **port matters, and `/v1` path depends on the provider**:
   - Anthropic: `http://localhost:8766` (no `/v1`)
   - OpenAI: `http://localhost:8766/v1`

3. If using systemd, check the service is active:
   ```bash
   systemctl --user status tokenpak
   ```

---

### Requests are timing out

This usually means the proxy is running but the request to the provider is timing out (not a TokenPak issue). Check:

```bash
# Test provider directly (bypassing proxy)
curl https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY"

# Check proxy debug logs
TOKENPAK_DEBUG=1 tokenpak serve
```

---

### Proxy is running but savings are 0%

Check compression threshold:

```bash
tokenpak debug on          # enable request capture
# ... make a request ...
tokenpak trace --last      # inspect the pipeline trace
```

If `input_tokens_raw` is below `TOKENPAK_COMPACT_THRESHOLD_TOKENS` (default 4500), compression won't run. Lower the threshold for smaller prompts.

---

### Stats footer shows wrong costs

The cost calculation is based on a built-in pricing catalog (`tokenpak/telemetry/data/pricing_catalog.json`). If you're using a model not in the catalog, TokenPak uses a default rate.

Check which model is being detected:

```bash
tokenpak trace --last
# Look for "model" field in the output
```

---

## Vault & Indexing

### `tokenpak index` is slow

Run calibration first — it benchmarks your machine and picks the optimal worker count:

```bash
tokenpak calibrate ~/vault
```

Then re-run the index. Calibration settings are saved to `~/.tokenpak/calibration.json` and used automatically.

---

### Search returns irrelevant results

The vault index uses BM25 term matching. Tips for better results:

- Use specific technical terms rather than natural language questions
- Re-index if files have changed: `tokenpak index ~/vault`
- Use `--watch` mode to keep the index current automatically

---

### Registry DB is corrupt or missing

```bash
# Rebuild from scratch
rm -f ~/.tokenpak/registry.db
tokenpak index ~/vault
```

---

## Budget & Cost Tracking

### `tokenpak cost` shows $0.00 even after many requests

Cost tracking requires the proxy to be the active proxy (requests must flow through it). Verify:

```bash
tokenpak status --full
# Look for "Session: N requests"
```

If requests aren't showing up, your client isn't pointing at the proxy.

---

### Budget alert isn't triggering

```bash
tokenpak budget status
```

Check that the budget was set correctly:

```bash
tokenpak budget set --monthly 50
tokenpak budget alert --at 80
```

---

## Reading Logs

### Proxy startup

```
[INFO] TokenPak proxy starting on :8766
[INFO] Compression: enabled (hybrid mode, threshold=4500 tokens)
[INFO] Telemetry: active → ~/.tokenpak/telemetry.db
[INFO] Ready.
```

### Per-request (debug mode)

```
[DEBUG] POST /v1/messages  →  anthropic  (claude-opus-4-6)
[DEBUG] Input tokens raw: 8,240 | after compression: 4,891 | saved: 3,349 (40.6%)
[DEBUG] Forwarding request to api.anthropic.com
[DEBUG] Response: 200 OK in 1,234ms
```

### Failover event

```
[WARN]  Primary provider failed: anthropic (timeout)
[INFO]  Failover: anthropic → openai (gpt-4o)
[INFO]  ⚠️ failover:anthropic→openai
```

### Systemd logs

```bash
# Live logs
journalctl --user -u tokenpak -f

# Last 100 lines
journalctl --user -u tokenpak -n 100

# Errors only
journalctl --user -u tokenpak -p err
```

---

## Still Stuck?

1. Run `tokenpak doctor` — it checks your install, config, and connectivity
2. Enable debug: `TOKENPAK_DEBUG=1 tokenpak serve`
3. Check the trace: `tokenpak trace --last`
4. Open an issue: [github.com/kaywhy331/tokenpak/issues](https://github.com/kaywhy331/tokenpak/issues)
