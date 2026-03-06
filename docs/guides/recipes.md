# Recipe Development Guide

Build custom compression recipes to squeeze more savings from your specific domain.

---

## What Are Recipes?

Recipes are declarative YAML files that tell TokenPak how to compress content. They're matched by file type, extension, or content pattern, then apply a sequence of operations to reduce token count while preserving semantics.

TokenPak ships with built-in recipes for Python, JavaScript, Markdown, JSON, YAML, SQL, and more. Custom recipes let you target your own conventions.

---

## Quick Start

```bash
# Scaffold a new recipe
tokenpak recipe create my-legal-cleanup --category legal

# Edit it
$EDITOR my-legal-cleanup.yaml

# Validate the schema
tokenpak recipe validate my-legal-cleanup.yaml

# Test against sample input
tokenpak recipe test my-legal-cleanup.yaml --input-file contract.txt

# Benchmark compression performance
tokenpak recipe benchmark my-legal-cleanup.yaml --runs 10

# Install to your active recipe set
tokenpak recipe install my-legal-cleanup.yaml
```

---

## Recipe Format

Every recipe is a YAML file with five top-level keys:

```yaml
name: my-recipe-name            # unique identifier; kebab-case
category: general               # see categories below
description: "What it does"     # shown in tokenpak demo --list

pattern:
  match: extension              # how to trigger this recipe
  extensions:
    - .txt
    - .md

action:
  compression_hint: 0.20        # expected fraction removed (0.0–1.0)
  operations:
    - type: strip_comments
    - type: collapse_whitespace
```

---

## Pattern Match Modes

| Mode | Required key | Description |
|------|-------------|-------------|
| `any` | — | Always triggers (use carefully) |
| `extension` | `extensions` | Match by file extension: `.py`, `.md`, etc. |
| `filename` | `filenames` | Exact filenames: `Makefile`, `Cargo.toml` |
| `content` | `keywords` | Triggers if content contains any keyword |
| `path_pattern` | `path_patterns` | Regex match on full file path |

**Examples:**

```yaml
# Match Python files
pattern:
  match: extension
  extensions: [.py]

# Match test files anywhere
pattern:
  match: path_pattern
  path_patterns: [".*/tests?/.*"]

# Match content with legal boilerplate
pattern:
  match: content
  keywords: ["WHEREAS", "INDEMNIFY", "hereinafter"]
```

---

## Built-in Operations

### Text Operations

| Operation | Params | Description |
|-----------|--------|-------------|
| `strip_comments` | — | Remove `#` single-line comments |
| `collapse_whitespace` | — | Normalize spaces and newlines |
| `remove_empty_lines` | `max_consecutive` (default: 1) | Collapse blank lines |
| `deduplicate_lines` | — | Remove duplicate lines (preserves order) |
| `truncate_lines` | `max_length` (default: 120) | Truncate long lines |
| `remove_filler_phrases` | — | Remove hedging language ("Please note that...") |

### Code Operations

| Operation | Params | Description |
|-----------|--------|-------------|
| `python_docstring_compress` | `mode`: `keep_summary` \| `remove` | Shorten Python docstrings |
| `regex_replace` | `pattern`, `replacement`, `flags` | Arbitrary regex substitution |

### Structured Data Operations

| Operation | Params | Description |
|-----------|--------|-------------|
| `json_compact` | — | Minify JSON (remove whitespace) |
| `keyword_filter` | `keep_keywords` (list) | Keep only lines containing these keywords |

---

## Recipe Examples

### Strip Python Type Annotations (Lossy)

```yaml
name: python-strip-types
category: python
description: Remove type annotations to save tokens in large Python files

pattern:
  match: extension
  extensions: [.py]

action:
  compression_hint: 0.15
  operations:
    - type: regex_replace
      pattern: ':\s*(str|int|float|bool|list|dict|Optional\[.*?\]|List\[.*?\])\s*='
      replacement: ' ='
      flags: MULTILINE
    - type: python_docstring_compress
      mode: keep_summary
    - type: remove_empty_lines
      max_consecutive: 1
```

### Legal Boilerplate Squasher

```yaml
name: legal-boilerplate
category: legal
description: Compress repetitive legal preamble while preserving operative clauses

pattern:
  match: content
  keywords: ["WHEREAS", "NOW, THEREFORE", "hereinafter referred to"]

action:
  compression_hint: 0.30
  operations:
    - type: regex_replace
      pattern: 'WHEREAS.*?(?=WHEREAS|NOW, THEREFORE)'
      replacement: '[WHEREAS clause omitted]\n'
      flags: DOTALL
    - type: remove_filler_phrases
    - type: collapse_whitespace
```

### Test File Compressor

```yaml
name: test-file-compress
category: python
description: Strip verbose test descriptions, keep assertions

pattern:
  match: path_pattern
  path_patterns: [".*/tests?/.*\\.py$", ".*test_.*\\.py$"]

action:
  compression_hint: 0.25
  operations:
    - type: python_docstring_compress
      mode: remove
    - type: strip_comments
    - type: remove_empty_lines
      max_consecutive: 1
```

### Obsidian Note Cleaner

```yaml
name: obsidian-notes
category: markdown
description: Strip Obsidian-specific syntax before sending notes to LLMs

pattern:
  match: content
  keywords: ["%%", "^", "[["]

action:
  compression_hint: 0.10
  operations:
    - type: regex_replace
      pattern: '%%.*?%%'
      replacement: ''
      flags: DOTALL
    - type: regex_replace
      pattern: '\[\[([^\]|]+)\|([^\]]+)\]\]'
      replacement: '\2'
    - type: regex_replace
      pattern: '\[\[([^\]]+)\]\]'
      replacement: '\1'
    - type: remove_empty_lines
      max_consecutive: 1
```

---

## Testing & Benchmarking

### Test Against Sample Input

```bash
tokenpak recipe test my-recipe.yaml --input-file sample.py
# Shows: original tokens, compressed tokens, reduction %
```

### Benchmark Multiple Runs

```bash
tokenpak recipe benchmark my-recipe.yaml --runs 10
# Reports: mean reduction, p95 latency, worst-case output
```

### A/B Test Two Recipes

```bash
tokenpak ab create recipe-test \
  --variant-a "recipe:my-recipe-v1" \
  --variant-b "recipe:my-recipe-v2"

tokenpak ab status recipe-test
# After enough traffic:
tokenpak ab apply recipe-test   # apply the winner
```

---

## Known Categories

`python`, `javascript`, `typescript`, `markdown`, `yaml`, `json`, `sql`, `html`,
`css`, `shell`, `go`, `rust`, `java`, `kotlin`, `swift`, `ruby`, `php`,
`legal`, `medical`, `financial`, `ops`, `general`

Use an existing category when possible — it helps TokenPak apply category-level heuristics.

---

## Installing & Sharing Recipes

```bash
# Install a local recipe file
tokenpak recipe install my-recipe.yaml

# List installed recipes
tokenpak recipe list

# Remove a recipe
tokenpak recipe remove my-recipe-name
```

Recipes are stored in `~/.tokenpak/recipes/`. You can version them in your project's git repo and install on deploy.

---

## Tips

- **Start with `compression_hint: 0.10`** — be conservative, test, then increase
- **Never compress code that will be executed** — recipes are for context, not output
- **Use `regex_replace` with `flags: MULTILINE`** for multiline patterns
- **Test with real files** from your domain, not toy examples
- **Check `tokenpak trace --last`** to see which recipe fired on a recent request
