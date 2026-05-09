# TokenPak Recipe SDK

> Custom recipe development tooling for building domain-specific compression recipes.

Recipes are declarative YAML files that define how TokenPak compresses content
before sending it to LLMs. The Recipe SDK provides a full development workflow:
scaffold → validate → test → benchmark → ship.

---

## Quick Start

```bash
# 1. Scaffold a new recipe
tokenpak recipe create my-legal-cleanup --category legal --domain-example legal

# 2. Edit the generated my-legal-cleanup.yaml to your needs

# 3. Validate the schema
tokenpak recipe validate my-legal-cleanup.yaml

# 4. Test against sample input
tokenpak recipe test my-legal-cleanup.yaml --input-file contract.txt

# 5. Benchmark compression performance
tokenpak recipe benchmark my-legal-cleanup.yaml --runs 10
```

---

## Recipe Format

Every recipe is a YAML file with five top-level keys:

```yaml
name: my-recipe-name # unique identifier; kebab-case
category: general # see categories below
description: "What it does" # shown in tokenpak demo --list

pattern:
 match: extension # how to trigger: any | extension | filename | content | path_pattern
 extensions:
 - .txt
 - .md

action:
 compression_hint: 0.20 # expected fraction removed (0.0–1.0)
 operations:
 - type: strip_comments
 - type: collapse_whitespace
```

### Pattern Match Modes

| mode | required key | description |
|----------------|-------------------|------------------------------------------------|
| `any` | — | Always triggers |
| `extension` | `extensions` | Match by file extension list (`.py`, `.md` …) |
| `filename` | `filenames` | Match exact filenames (`Makefile`, `Cargo.toml`) |
| `content` | `keywords` | Match if content contains any keyword |
| `path_pattern` | `path_patterns` | Match via regex on file path |

### Built-in Operation Types

| type | key params | description |
|----------------------------|-----------------------------------------|------------------------------------------|
| `regex_replace` | `pattern`, `replacement`, `flags` | Regex substitution (re.MULTILINE etc.) |
| `strip_comments` | — | Remove `#` single-line comments |
| `deduplicate_lines` | — | Remove duplicate lines (preserves order) |
| `truncate_lines` | `max_length` (int, default 120) | Truncate long lines |
| `remove_empty_lines` | `max_consecutive` (int, default 1) | Collapse excessive blank lines |
| `collapse_whitespace` | — | Normalize spaces and excessive newlines |
| `python_docstring_compress`| `mode`: keep_summary \| remove | Shorten or remove Python docstrings |
| `remove_filler_phrases` | — | Remove hedging language |
| `json_compact` | — | Minify JSON (removes whitespace) |
| `keyword_filter` | `keep_keywords` (list) | Keep only lines containing keywords |

### Known Categories

`python`, `javascript`, `typescript`, `markdown`, `yaml`, `json`, `sql`, `html`,
`css`, `general`, `legal`, `medical`, `config`, `logs`, `git`

---

## CLI Reference

### `tokenpak recipe create <name>`

Scaffold a new recipe file.

```
Options:
 --output-dir DIR Where to write the file (default: current dir)
 --category CAT Category hint (default: general)
 --description TEXT Short description
 --match-mode MODE any | extension | filename | content | path_pattern
 --ext EXT Extension hint for extension match mode
 --domain-example legal | medical (use a domain-specific template)
```

### `tokenpak recipe validate <file>`

Check a recipe against the schema. Exits 1 on hard errors. Prints warnings for
soft issues (unknown category, empty description, unknown operation types).

### `tokenpak recipe test <file>`

Run a recipe against sample input and print a before/after report.

```
Options:
 --input-text TEXT Raw text to test against
 --input-file FILE Read test input from a file
 --filename-hint NAME Filename to check pattern matching against
```

Output:
```
Pattern match : ✅ yes
Ops applied : strip_comments, collapse_whitespace
Input chars : 1024
Output chars : 812
Compression : 20.7% removed
Hint vs actual : 20.0% expected → 20.7% actual
```

### `tokenpak recipe benchmark <file>`

Measure compression ratio and throughput across multiple samples.

```
Options:
 --samples-file FILE JSON list of sample strings (default: auto-generated)
 --runs N Repetitions per sample for timing (default: 5)
```

Output:
```
Compression (mean) : 21.3% [min 18.4% – max 24.1%]
Hint vs actual : 20.0% → 21.3% (+1.3% delta)
Timing ms (mean) : 0.142 ms [min 0.098 – max 0.201]
```

---

## Domain Examples

Bundled in `recipes/custom-examples/`:

| File | Domain | What it does |
|----------------------------------------|----------|------------------------------------------------|
| `legal-boilerplate-removal.yaml` | Legal | Strips WHEREAS recitals + signature blocks |
| `medical-note-cleanup.yaml` | Medical | Removes PHI headers + confidentiality notices |
| `legal-contract-clause-extract.yaml` | Legal | Keeps operative clauses; removes exhibits |

Generate a domain template with:
```bash
tokenpak recipe create my-legal --domain-example legal
tokenpak recipe create my-medical --domain-example medical
```

---

## Programmatic Usage

```python
from tokenpak.agent.recipe_sdk import RecipeSDK

sdk = RecipeSDK()

# Scaffold
path = sdk.create("my-recipe", category="legal", domain_example="legal")

# Validate
warnings = sdk.validate("my-recipe.yaml") # raises RecipeValidationError on failure

# Test
result = sdk.test("my-recipe.yaml", input_text="WHEREAS Party A...")
print(result["compression_ratio"]) # e.g. 0.34

# Benchmark
bench = sdk.benchmark("my-recipe.yaml", runs=10)
print(bench["compression"]["mean"]) # e.g. 0.33
```

---

## Tips

- Start with `--domain-example` for legal/medical content — saves time.
- Keep `compression_hint` honest: it's used by the intelligence server to
 estimate token savings when auto-selecting recipes.
- Use `content` match mode for domain recipes — file extensions are ambiguous
 for legal/medical text.
- Run `benchmark` before shipping — a recipe that adds zero compression isn't worth the CPU.
