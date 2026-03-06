# Installing TokenPak

## Requirements

- Python 3.10 or higher
- pip or pipx

---

## Quick Install (Recommended)

```bash
pip install tokenpak
```

Verify the install:

```bash
tokenpak --help
```

---

## Install with Optional Dependencies

### Token counting (for accurate savings reports)

```bash
pip install "tokenpak[tokens]"
```

This installs `tiktoken` for exact token counts instead of estimates.

### Development dependencies

```bash
pip install "tokenpak[dev]"
```

Includes `pytest` and testing tools.

---

## Install from Source

```bash
git clone https://github.com/kaywhy331/tokenpak.git
cd tokenpak
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

---

## Using pipx (Isolated Install)

If you want the CLI globally without polluting your Python environment:

```bash
pipx install tokenpak
```

---

## Upgrading

```bash
pip install --upgrade tokenpak
```

---

## Verify Installation

```bash
# Check version
tokenpak --version

# Start the proxy
tokenpak serve --port 8766

# View CLI help
tokenpak --help
```

---

## Uninstalling

```bash
pip uninstall tokenpak
```

---

## Troubleshooting

### `tokenpak: command not found`

Make sure your Python scripts directory is on your PATH. With `pip`, scripts go to:
- **Linux/macOS**: `~/.local/bin/` — add to PATH in `~/.bashrc` or `~/.zshrc`
- **Windows**: `%APPDATA%\Python\Scripts\` — add to PATH in System settings

With `pipx`, it handles this automatically.

### ImportError on Python < 3.10

TokenPak requires Python 3.10+. Check your version:

```bash
python --version
```

Use `python3.10`, `python3.11`, or `python3.12` explicitly if needed.

### Port 8766 already in use

```bash
tokenpak serve --port 8767
```

Or kill the existing process:

```bash
lsof -ti:8766 | xargs kill -9
```

---

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for more detailed diagnostics.
