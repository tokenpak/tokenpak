# TokenPak Config Templates

Ready-to-use configuration templates for common deployment scenarios.
Copy one, add your API key, and run — no other edits required.

---

## Quick Start

```bash
# 1. Copy a template
cp configs/single-user.yaml ~/.tokenpak/config.yaml

# 2. Set your API key (env var, not in config file)
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Start the proxy
tokenpak start
```

---

## Templates

| File | Use Case |
|------|----------|
| `single-user.yaml` | One person, one provider, sensible defaults |
| `team-internal.yaml` | Shared proxy for a team, auth + audit logging |
| `anthropic-only.yaml` | All-in on Claude — Claude-specific optimizations |
| `openai-only.yaml` | All-in on GPT — OpenAI-specific optimizations |
| `mixed-routing.yaml` | Multi-provider smart routing, automatic failover |
| `local-ollama.yaml` | Local models via Ollama — no API key needed |
| `cost-saving-max.yaml` | Aggressive caching, cheap-first routing, minimum spend |
| `privacy-first.yaml` | No logging, redaction on, strict mode — HIPAA/legal use |

---

## Which Template Should I Use?

**"Just get started"** → `single-user.yaml`

**"I'm on a team"** → `team-internal.yaml`

**"I only use Claude"** → `anthropic-only.yaml`

**"I only use GPT"** → `openai-only.yaml`

**"I want to mix providers and save money"** → `mixed-routing.yaml`

**"I want to run 100% locally with no API"** → `local-ollama.yaml`

**"My API bill is too high"** → `cost-saving-max.yaml`

**"I'm handling sensitive/private data"** → `privacy-first.yaml`

---

## API Keys

**Never put API keys in the config file.** Use environment variables:

| Provider | Environment Variable |
|----------|----------------------|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Google | `GOOGLE_API_KEY` |
| Ollama | No key needed |

Set them in your shell or a `.env` file (never commit `.env`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
```

---

## Config Location

TokenPak reads from `~/.tokenpak/config.yaml` by default.

```bash
# Copy template
cp configs/single-user.yaml ~/.tokenpak/config.yaml

# Or specify a custom path
tokenpak start --config /path/to/config.yaml
```

---

## Validate Your Config

```bash
tokenpak config-check
```

Or manually check JSON/YAML syntax:

```bash
python3 -c "import yaml, sys; yaml.safe_load(open('~/.tokenpak/config.yaml'))" && echo "Valid"
```

---

## Customizing

Each template has inline comments explaining every setting.
The full list of options with defaults is in `tokenpak/config_loader.py` → `generate_default_yaml()`.
