# TokenPak Installation Guide

Everything you need to install the TokenPak Python SDK and verify it's working.

---

## Python Version Requirements

TokenPak requires **Python 3.8 or higher**.

Recommended: Python 3.11+ for best performance. Check your version:

```bash
python3 --version
```

---

## Install via pip

**Basic install:**

```bash
pip install tokenpak
```

**With accurate token counting (recommended for production):**

```bash
pip install tokenpak[tiktoken]
```

**With ML-powered compression (advanced, larger install):**

```bash
pip install tokenpak[ml]
```

---

## Virtual Environment Setup (Recommended)

Isolate TokenPak from your global Python environment:

```bash
# Create a virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows

# Install TokenPak
pip install tokenpak

# Deactivate when done
deactivate
```

Using [uv](https://github.com/astral-sh/uv)? Even faster:

```bash
uv venv && uv pip install tokenpak
```

---

## Verify Your Install

Run a quick import check:

```bash
python3 -c "from tokenpak import HeuristicEngine; print('TokenPak OK ✅')"
```

Expected output:

```
TokenPak OK ✅
```

Check the installed version:

```bash
python3 -c "import tokenpak; print(tokenpak.__version__)"
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'tokenpak'`**
→ Make sure your virtual environment is activated and you ran `pip install tokenpak` inside it.

**`pip: command not found`**
→ Try `pip3` instead, or use `python3 -m pip install tokenpak`.

**SSL errors during install**
→ Upgrade pip first: `pip install --upgrade pip`, then retry.

**Version conflicts**
→ Install in a fresh virtual environment to avoid dependency clashes.

---

> For more help, see [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) or open an issue on GitHub.
