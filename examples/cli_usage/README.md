# CLI Usage Example

**Problem:** You need to compress a codebase or document set before feeding it to an LLM, but you don't want to write Python code.

**Solution:** Use the `tokenpak` CLI to index and compress files directly from the terminal.

## What This Shows

- Indexing a directory with `tokenpak index`
- Checking compression statistics
- Common CLI patterns and flags

## Setup

```bash
pip install tokenpak
```

## Run

```bash
bash commands.sh
```

## Common Commands

```bash
# Index a project directory
tokenpak index ./my_project

# Show index status and stats
tokenpak index . --status

# Index with verbose per-file output
tokenpak index ./src --verbose

# Check TokenPak version
python -c "import tokenpak; print(tokenpak.__version__)"
```

## Example Output

```
Indexing ./my_project...
  ✅ src/main.py        420 → 180 tokens (57% savings)
  ✅ src/utils.py       310 → 145 tokens (53% savings)
  ✅ README.md          850 → 390 tokens (54% savings)

Total: 23 files, 8,420 → 3,890 tokens (53.8% savings)
Registry: .tokenpak/registry.db
```

## Time to Complete

~5 minutes
