# Getting Started

Get TokenPak running in under 5 minutes.

---

## Requirements

- Python 3.11+
- An existing LLM client (Claude Code, OpenAI client, etc.)
- Your provider API key (Anthropic, OpenAI, etc.)

---

## Install

=== "pip"
    ```bash
    pip install tokenpak
    ```

=== "From source"
    ```bash
    git clone https://github.com/tokenpak/tokenpak
    cd tokenpak
    pip install -e .
    ```

=== "With optional extras"
    ```bash
    # Accurate token counting (recommended)
    pip install tokenpak[tiktoken]

    # ML-powered compression (advanced)
    pip install tokenpak[ml]
    ```

---

## Start the Proxy

```bash
tokenpak serve --port 8766
```

The proxy starts on `http://localhost:8766` and is ready to accept requests immediately.

!!! tip "Run in background"
    ```bash
    tokenpak serve --port 8766 --daemon
    # Stop with:
    tokenpak stop
    ```

---

## Connect Your LLM Client

Point your existing tool at the TokenPak proxy instead of the provider directly.

=== "Claude Code"
    ```bash
    # Set the API base URL (in your shell config or .env)
    export ANTHROPIC_BASE_URL=http://localhost:8766
    ```
    Or configure in `~/.claude/settings.json`:
    ```json
    {
      "env": {
        "ANTHROPIC_BASE_URL": "http://localhost:8766"
      }
    }
    ```

=== "OpenAI Python"
    ```python
    from openai import OpenAI

    client = OpenAI(
        base_url="http://localhost:8766/v1",
        api_key="your-key-here"
    )
    ```

=== "OpenAI CLI"
    ```bash
    export OPENAI_BASE_URL=http://localhost:8766/v1
    ```

=== "Any HTTP client"
    Replace your provider base URL with `http://localhost:8766`. 
    TokenPak auto-detects the provider from the `Authorization` header and routes accordingly.

Your credentials pass through unchanged. TokenPak never stores them.

---

## Verify It's Working

```bash
tokenpak status
```

Expected output:
```
✓ Proxy: running on :8766
✓ Compression: enabled (balanced mode)
✓ Cost tracking: active
✓ Session: 0 requests
```

Make a test request through your client, then:

```bash
tokenpak cost --today
# Cost today: $0.002 | Tokens saved: 1,847 (38%)
```

---

## Index Your Vault (Optional, Zero Tokens)

If you work with a large codebase or notes vault, index it for instant semantic search:

```bash
tokenpak index ~/vault
tokenpak vault search "compression benchmark"
```

This uses a local SQLite registry — no LLM calls, no cost.

---

## Auto-Calibration (Recommended)

Let TokenPak calibrate optimal parallelism for your hardware:

```bash
tokenpak calibrate ~/vault --max-workers 8 --rounds 2
```

This runs once and saves a profile to `~/.tokenpak/calibration.json`. Future indexing runs use it automatically.

---

## Set a Budget (Optional)

Protect yourself from runaway costs:

```bash
tokenpak budget set --monthly 50      # $50/month limit
tokenpak budget alert --at 80%        # warn at 80%
```

---

## Next Steps

- [Proxy Setup](guides/proxy-setup.md) — advanced proxy configuration, SSL, multi-provider
- [CLI Reference](cli-reference.md) — full command reference
- [Recipe Development](guides/recipes.md) — custom compression recipes
