# FAQ & Troubleshooting

---

## General

### How much will TokenPak save me?

It depends on the request path and the features you explicitly enable. The
default HTTP proxy does not invoke `compact_request_body`, so its legacy
threshold does not predict proxy savings.

Check your actual savings:

```bash
tokenpak cost --week
tokenpak savings
```

### Does TokenPak change my responses?

The provider response is forwarded unchanged by the receipt display. Explicit
request hooks may transform eligible input; the default HTTP path does not call
the legacy compact helper, and Claude Code request bodies remain byte-preserved.

Optionally, TokenPak can print a one-line stats footer to the proxy terminal's
standard error:

```bash
TOKENPAK_STATS_FOOTER=1 tokenpak serve
# ⚡ TokenPak: -1,847 tokens (38%) | $0.014 saved
```

Disable by unsetting or setting `TOKENPAK_STATS_FOOTER=0`.

### Will TokenPak break if a provider changes their API?

The proxy applies provider-specific routing, header, credential, validation,
and optional request-hook policies. Adapters fail open where their contract
allows it, but a provider protocol change can still require an adapter update.
Claude Code request bodies retain their byte-preserved route policy.

### Does TokenPak send my data anywhere?

No. TokenPak runs entirely locally. Your prompts, responses, API keys, and metadata never leave your machine. The proxy intercepts requests between your client and the provider, compresses them locally, and forwards them. There's no TokenPak cloud service involved.

### How does TokenPak affect my API key?

It doesn't. Your API key is in the `Authorization` header and is passed through to the provider unchanged. TokenPak never reads or stores it — it's opaque to the proxy.

### Can I use TokenPak with multiple providers at once?

Yes. TokenPak detects the provider from the `Authorization` header format and routes accordingly. You can use Anthropic and OpenAI simultaneously through the same proxy port.

### What's the performance overhead?

Minimal. Compression adds 10–50ms to requests that benefit from it (typically those over 1,500 tokens). Small requests are passed through with near-zero overhead. Cold start overhead is under 100ms.

---

## Installation

### `pip install tokenpak` fails with Python version error

TokenPak requires Python 3.10+. Check your version:

```bash
python --version
# If < 3.10:
pip install "tokenpak>=0.1.0" --python-version 3.10
# Or use pyenv to install Python 3.10
```

### `tokenpak: command not found` after install

Your pip scripts directory isn't in `PATH`. Find it:

```bash
python -m site --user-base
# e.g. /home/user/.local

# Add to PATH:
export PATH="$HOME/.local/bin:$PATH"
```

Add to `~/.bashrc` or `~/.zshrc` to persist.

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
# Strict mode disables compression.
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

### Stats footer shows wrong costs

The cost calculation is based on a built-in pricing catalog (`tokenpak/telemetry/data/pricing_catalog.json`). If you're using a model not in the catalog, TokenPak uses a default rate.

Check which model is being detected:

```bash
tokenpak trace --last
# Look for "model" field in the output
```

---

## Compression

### How do I know compression is working?

```bash
tokenpak compress --help
# Run tokenpak compress with representative local input.
```

For an integration that explicitly calls `compact_request_body`, verify its
before/after token evidence. A config or status value alone is not proof that
the default proxy transformed a body.

### Some of my requests aren't being compressed

This is expected on the default HTTP path: it has no
`compact_request_body` call site. `TOKENPAK_COMPACT` /
`compression.enabled` do not toggle that path, and lowering
`TOKENPAK_COMPACT_THRESHOLD_TOKENS` on `tokenpak serve` does not activate it.

The threshold applies only inside an integration that explicitly calls the
legacy helper. Use `tokenpak compress` for explicit local compaction.

### Compression seems to be changing my prompt

Capture the exact bytes before and after the calling integration's request
hook, then disable or retune that explicit hook. The default HTTP path does not
call `compact_request_body`, and `TOKENPAK_COMPACT=0` does not disable other
request transformations such as an explicitly enabled Pak builder or schema
normalizer.

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
tokenpak prune --older-than 0d # delete all history
# or
rm ~/.tokenpak/stats.db # nuclear option
```

### Where is my data stored?

| Data | Location |
|------|----------|
| Configuration | `~/.tokenpak/config.json` |
| Session database | `~/.tokenpak/monitor.db` (or `TOKENPAK_DB` env var) |
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

### Registry DB is corrupt or missing

```bash
# Rebuild from scratch
rm -f ~/.tokenpak/registry.db
tokenpak index ./knowledge-base
```

---

## Budget & Cost Alerts

### How do I set a monthly budget?

```bash
tokenpak budget set --monthly 50
tokenpak budget alert --at 80 # alert at 80% of budget
```

### Budget alert isn't triggering

```bash
tokenpak budget status
# Verify amount and percentage are set correctly
tokenpak budget set --monthly 50
tokenpak budget alert --at 80
```

---

## Reading Logs

### Proxy startup (expected output)

```
[INFO] TokenPak proxy starting on :8766
[INFO] Telemetry: active → ~/.tokenpak/telemetry.db
[INFO] Ready.
```

### Per-request trace from an explicitly enabled reduction hook

```
[DEBUG] POST /v1/messages → anthropic (claude-opus-4-6)
[DEBUG] Input tokens raw: 8,240 | after compression: 4,891 | saved: 3,349 (40.6%)
[DEBUG] Forwarding request to api.anthropic.com
[DEBUG] Response: 200 OK in 1,234ms
```

### Failover event

```
[WARN] Primary provider failed: anthropic (timeout)
[INFO] Failover: anthropic → openai (gpt-4o)
```

### Systemd log commands

```bash
journalctl --user -u tokenpak -f # live logs
journalctl --user -u tokenpak -n 100 # last 100 lines
journalctl --user -u tokenpak -p err # errors only
```

---

## Getting Help

```bash
tokenpak doctor # comprehensive self-diagnosis
tokenpak logs --errors # recent errors
tokenpak --version # version info for bug reports
```

File issues at: https://github.com/tokenpak/tokenpak/issues
