# TokenPak Python SDK: Installation Guide

## System Requirements

- **Python version:** 3.10 or later
- **OS:** Linux, macOS, or Windows
- **pip:** Version 20.0 or later (usually included with Python)

## Basic Installation

The simplest way to install TokenPak:

```bash
pip install tokenpak
```

This installs the core library with heuristic-based compression.

## Optional Dependencies

TokenPak has optional extras for advanced features:

```bash
# Install with ML-based compression support
pip install tokenpak[ml]

# Install with all features (recommended)
pip install tokenpak[ml,tiktoken]

# Or install each extra separately
pip install tokenpak tiktoken
```

## Using a Virtual Environment (Recommended)

**Option 1: venv (built-in)**

```bash
# Create a virtual environment
python3 -m venv ~/my_tokenpak_env

# Activate it
source ~/my_tokenpak_env/bin/activate # Linux/macOS
# or
~/my_tokenpak_env\Scripts\activate # Windows

# Install TokenPak
pip install tokenpak[ml,tiktoken]

# Deactivate when done
deactivate
```

**Option 2: uv (faster, modern)**

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create and activate environment
uv venv ~/my_tokenpak_env
source ~/my_tokenpak_env/bin/activate

# Install TokenPak
pip install tokenpak[ml,tiktoken]
```

## Verify Your Installation

After installing, verify that TokenPak works:

```bash
python3 -c "from tokenpak import HeuristicEngine; print('✓ TokenPak installed!')"
```

Or run a quick test:

```bash
python3 << 'EOF'
from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints

engine = HeuristicEngine()
text = "This is a test. " * 100
hints = CompactionHints(target_tokens=50)
result = engine.compact(text, hints)
print(f"Compressed {len(text)} chars to {len(result)} chars")
print("✓ Installation successful!")
EOF
```

## Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'tokenpak'"

**Solution:** Make sure you're running Python from the correct environment:
```bash
which python3 # Should show path in your venv
pip list | grep tokenpak # Should show tokenpak installed
```

If not in a venv, reinstall:
```bash
pip install --upgrade tokenpak
```

### Issue: "tiktoken not found" or encoding errors

**Solution:** Install the tiktoken extra:
```bash
pip install tokenpak[tiktoken]
```

### Issue: "Permission denied" when installing

**Solution:** Use `--user` flag or a virtual environment:
```bash
pip install --user tokenpak # Install to user directory
# OR use a venv (recommended)
```

### Issue: Python version error (3.9 or earlier)

**Solution:** TokenPak requires Python 3.10+. Upgrade Python or use a package manager:
```bash
# macOS (homebrew)
brew install python@3.11

# Ubuntu/Debian
sudo apt-get install python3.11

# Then install TokenPak with the new version
python3.11 -m pip install tokenpak
```

## Upgrading TokenPak

To upgrade to the latest version:

```bash
pip install --upgrade tokenpak
```

## What's Next?

- **[Quick Start Guide](./quickstart.md)** — Get running in 5 minutes
- **[API Reference](./api-reference.md)** — Explore the full API
- **[Examples](../examples/)** — Real-world usage patterns
