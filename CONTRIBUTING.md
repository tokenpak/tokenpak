# Contributing to TokenPak

Thanks for your interest in contributing! TokenPak is an open-source project and we welcome contributions of all kinds — bug fixes, new recipes, documentation improvements, and new features.

---

## Getting Started

### Prerequisites

- Python 3.10+
- Git

### Setup

```bash
git clone https://github.com/kaywhy331/tokenpak.git
cd tokenpak

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run the tests
pytest tests/ -q
```

---

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/kaywhy331/tokenpak/issues) first
2. If not found, open a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs. actual behavior
   - Python version and OS

### Suggesting Features

Open an issue with the `enhancement` label. Include:
- The problem you're trying to solve
- Why you think it belongs in the core project
- Any alternative approaches you've considered

### Submitting Code

1. **Fork** the repository
2. **Create a branch** from `master`: `git checkout -b feature/your-feature-name`
3. **Write tests** for your changes
4. **Run the test suite**: `pytest tests/ -q`
5. **Commit** with a clear message: `git commit -m "feat: add XYZ"` 
6. **Push** your branch and **open a Pull Request**

---

## Code Style

- Follow PEP 8
- Use type hints where practical
- Keep functions focused and small
- Docstrings for public functions

We use `black` for formatting (optional but appreciated):
```bash
pip install black
black tokenpak/
```

---

## Adding a Recipe

Recipes are YAML files in `recipes/oss/`. Each recipe defines a compression transformation.

**Naming convention:** `<category>-<description>.yaml`

**Categories:**
- `gen-` — general text transformations
- `py-` — Python-specific
- `js-` — JavaScript/TypeScript
- `cp-` — content/payload (JSON, CSV, etc.)
- `cfg-` — config files
- `md-` — Markdown

**Recipe structure:**

```yaml
name: your-recipe-name
description: "One-line description of what this compresses"
category: gen
triggers:
  - pattern: "some pattern"
    type: regex
transforms:
  - type: replace
    pattern: "..."
    replacement: "..."
examples:
  - input: "..."
    output: "..."
    savings_pct: 25
```

See `recipes/oss/gen-whitespace-normalization.yaml` for a complete example.

---

## Testing

```bash
# Run all tests
pytest tests/ -q

# Run with coverage
pytest tests/ --cov=tokenpak --cov-report=term-missing

# Run a specific test file
pytest tests/test_recipes_engine.py -v
```

---

## Project Structure

```
tokenpak/
├── tokenpak/           # Core library
│   ├── agent/          # Proxy agent (routing, CLI, telemetry)
│   ├── compression/    # Compression pipeline (in agent/)
│   ├── telemetry/      # Cost tracking and dashboard
│   ├── connectors/     # Source connectors (GitHub, Notion, etc.)
│   ├── engines/        # Compression engines
│   └── intelligence/   # Routing intelligence server
├── recipes/oss/        # Open-source compression recipes
├── tests/              # Test suite
├── docs/               # Documentation source
└── .github/workflows/  # CI/CD
```

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).

---

## Code of Conduct

Be respectful, constructive, and welcoming to all contributors. This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/).
