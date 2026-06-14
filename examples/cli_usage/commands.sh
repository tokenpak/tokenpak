#!/usr/bin/env bash
# CLI Usage Examples for TokenPak
# ================================
# TokenPak ships with a CLI for indexing and compressing files directly.
#
# Problem: You want to compress a codebase or document set before feeding
#          it to an LLM, without writing Python code.
#
# Solution: Use the `tokenpak` CLI to index, compress, and report.
#
# Setup: pip install tokenpak
# Run:   bash commands.sh

set -e

echo "=== TokenPak CLI Usage Examples ==="
echo ""

# -----------------------------------------------------------------------
# 1. Index a directory (compress all files, store in registry)
# -----------------------------------------------------------------------
echo "--- 1. Index a directory ---"
echo "Command: tokenpak index ./my_project"
echo "What it does: Scans all files, compresses them, stores in .tokenpak/registry.db"
echo ""
# Example (comment out if no project to index):
# tokenpak index ./my_project

# -----------------------------------------------------------------------
# 2. Show index status
# -----------------------------------------------------------------------
echo "--- 2. Show index status ---"
echo "Command: tokenpak index . --status"
echo "What it does: Shows how many files are indexed and total compression stats"
echo ""

# -----------------------------------------------------------------------
# 3. Compress a single file to stdout
# -----------------------------------------------------------------------
echo "--- 3. Compress a single file ---"
echo "Creating a test file..."
cat > /tmp/tokenpak_test.py << 'PYTHON'
def greet(name):
    """
    This function greets a person by name.
    It takes a string argument representing the person's name.
    It returns a formatted greeting string.
    """
    # Build the greeting string using an f-string
    greeting = f"Hello, {name}!"  # format the name into greeting
    return greeting  # return the completed greeting
PYTHON

echo "Original file:"
cat /tmp/tokenpak_test.py
echo ""

# Use Python API to compress (simulating what tokenpak pack would do)
python3 - << 'PYEOF'
import sys
from tokenpak import HeuristicEngine

engine = HeuristicEngine()
with open('/tmp/tokenpak_test.py') as f:
    content = f.read()

compressed = engine.compact(content)
print("--- Compressed output (via Python API) ---")
print(compressed)
print(f"\nOriginal:   {len(content)} chars")
print(f"Compressed: {len(compressed)} chars")
print(f"Savings:    {1 - len(compressed)/len(content):.0%}")
PYEOF

# -----------------------------------------------------------------------
# 4. Batch compress multiple files
# -----------------------------------------------------------------------
echo ""
echo "--- 4. Common patterns ---"
echo ""
echo "# Compress and pipe to clipboard (macOS):"
echo "  tokenpak index ./src && pbpaste"
echo ""
echo "# Index with specific file types only:"
echo "  tokenpak index ./src --include '*.py,*.ts'"
echo ""
echo "# Verbose output to see per-file compression:"
echo "  tokenpak index ./src --verbose"
echo ""
echo "# Check version:"
python3 -c "import tokenpak; print(f'TokenPak version: {tokenpak.__version__}')"

echo ""
echo "✅ CLI usage examples complete!"
