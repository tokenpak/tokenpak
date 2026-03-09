# SPDX-License-Identifier: MIT
"""Context Miss Detection for TokenPak.

Detects when the LLM didn't have enough context — hallucinations, explicit
asks for missing info, wrong signatures, uncertain answers. Logs gaps to
.tokenpak/gaps.json so future queries can expand retrieval automatically.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

# Default gap store location (relative to project root)
DEFAULT_GAPS_PATH = ".tokenpak/gaps.json"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SignalType(str, Enum):
    EXPLICIT_ASK = "EXPLICIT_ASK"
    HALLUCINATED_IMPORT = "HALLUCINATED_IMPORT"
    WRONG_SIGNATURE = "WRONG_SIGNATURE"
    UNCERTAIN_ANSWER = "UNCERTAIN_ANSWER"
    MISSING_INFO = "MISSING_INFO"


@dataclass
class ContextGap:
    query: str
    signal_type: SignalType
    evidence: str  # Substring that triggered detection
    timestamp: str
    related_blocks: List[str] = field(default_factory=list)  # Block paths in context


# ---------------------------------------------------------------------------
# Signal patterns
# ---------------------------------------------------------------------------

# EXPLICIT_ASK — LLM explicitly says it lacks info
_EXPLICIT_ASK_PATTERNS = [
    r"I don['']t have\b",
    r"\bnot provided\b",
    r"no information about\b",
    r"I['']d need to see\b",
    r"I don['']t have access to\b",
    r"wasn['']t provided\b",
    r"not included in\b",
    r"I haven['']t been given\b",
    r"I was not given\b",
    r"no context (?:was |is )?provided",
]

# UNCERTAIN_ANSWER — LLM is guessing
_UNCERTAIN_PATTERNS = [
    r"\bI think\b",
    r"\bprobably\b",
    r"\bI['']m not sure\b",
    r"\bit might be\b",
    r"\bI believe\b",
    r"\bI['']m not certain\b",
    r"\bI['']m unsure\b",
    r"\bperhaps\b",
    r"\bI would guess\b",
    r"\bI assume\b",
]

# MISSING_INFO — LLM says something is absent + references file/function
_MISSING_INFO_PATTERNS = [
    r"I don['']t see\b",
    r"\bthere['']s no\b",
    r"\bcouldn['']t find\b",
    r"\bno (?:such )?\w+ (?:found|exists|defined)\b",
    r"\bnot found in\b",
    r"\babsent from\b",
    r"\bmissing from\b",
]

# Precompile
_EXPLICIT_ASK_RE = [re.compile(p, re.IGNORECASE) for p in _EXPLICIT_ASK_PATTERNS]
_UNCERTAIN_RE = [re.compile(p, re.IGNORECASE) for p in _UNCERTAIN_PATTERNS]
_MISSING_INFO_RE = [re.compile(p, re.IGNORECASE) for p in _MISSING_INFO_PATTERNS]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_match(patterns: List[re.Pattern], text: str) -> Optional[str]:
    """Return the first matching substring, or None."""
    for pat in patterns:
        m = pat.search(text)
        if m:
            # Return a window of context around the match (up to 120 chars)
            start = max(0, m.start() - 10)
            end = min(len(text), m.end() + 80)
            return text[start:end].strip()
    return None


def _extract_imports_from_response(response_text: str) -> List[str]:
    """Extract module names from import statements in the response."""
    modules = []
    # `import foo`, `import foo.bar`
    for m in re.finditer(r"^\s*import\s+([\w.]+)", response_text, re.MULTILINE):
        modules.append(m.group(1).split(".")[0])
    # `from foo import bar`, `from foo.bar import baz`
    for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import", response_text, re.MULTILINE):
        modules.append(m.group(1).split(".")[0])
    return list(set(modules))


def _is_module_in_context(module: str, context_blocks: List[str]) -> bool:
    """Check if a module name appears in any context block path or content."""
    module_lower = module.lower()
    for block in context_blocks:
        if module_lower in block.lower():
            return True
    return False


def _extract_fn_signatures(text: str) -> dict:
    """
    Extract function definitions from text.
    Returns {fn_name: param_count} for each detected definition.
    """
    sigs = {}
    # Python: def foo(a, b, c):
    for m in re.finditer(r"\bdef\s+(\w+)\s*\(([^)]*)\)", text):
        name = m.group(1)
        params_raw = m.group(2).strip()
        if not params_raw:
            count = 0
        else:
            count = len([p for p in params_raw.split(",") if p.strip()])
        sigs[name] = count
    # JS/TS/Go/Rust: function foo(a, b) / func foo(a, b) / fn foo(a, b)
    for m in re.finditer(r"\b(?:function|func|fn)\s+(\w+)\s*\(([^)]*)\)", text):
        name = m.group(1)
        params_raw = m.group(2).strip()
        count = len([p for p in params_raw.split(",") if p.strip()]) if params_raw else 0
        sigs[name] = count
    return sigs


def _extract_fn_calls(text: str) -> dict:
    """
    Extract function calls from text.
    Returns {fn_name: param_count} for each call detected.
    Multiple calls to same fn → keep max param count.
    """
    calls: Dict[str, int] = {}
    for m in re.finditer(r"\b(\w+)\s*\(([^)]*)\)", text):
        name = m.group(1)
        # Skip keywords and builtins
        if name in {
            "if",
            "while",
            "for",
            "def",
            "class",
            "return",
            "import",
            "print",
            "len",
            "range",
            "str",
            "int",
            "list",
            "dict",
            "function",
            "func",
            "fn",
        }:
            continue
        args_raw = m.group(2).strip()
        count = len([a for a in args_raw.split(",") if a.strip()]) if args_raw else 0
        # Track max observed arg count per function name
        calls[name] = max(calls.get(name, 0), count)
    return calls


def _has_file_or_fn_reference(text: str) -> bool:
    """Check if text contains a file path, function/class name, or code entity reference."""
    # File path (e.g. src/auth.py, ./utils/helper.js)
    if re.search(r"[\w./\-]+\.\w{1,6}", text):
        return True
    # Backtick-quoted identifier
    if re.search(r"`\w+`", text):
        return True
    # CamelCase or snake_case identifiers
    if re.search(r"\b[A-Z][a-z]+[A-Z]\w*\b|\b\w+_\w+\b", text):
        return True
    # Generic code entity nouns (plural or singular)
    if re.search(
        r"\b(?:function|functions|class|classes|method|methods|"
        r"file|files|module|modules|import|imports|type|types|"
        r"interface|interfaces|struct|structs|variable|variables|"
        r"constant|constants|definition|definitions|symbol|symbols)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Main detection function
# ---------------------------------------------------------------------------


def detect_misses(
    response_text: str,
    query: str,
    context_blocks: List[str],
) -> List[ContextGap]:
    """
    Detect context gaps in an LLM response.

    Args:
        response_text:  Full LLM response string.
        query:          The original user query.
        context_blocks: List of block content strings that were in context.
                        Can also include block paths (mixed list is fine).

    Returns:
        List of ContextGap objects, one per signal detected.
        Multiple signal types may fire for the same response.
    """
    gaps = []
    now = datetime.now(timezone.utc).isoformat()
    # Build list of block paths (lines that look like file paths)
    block_paths = [
        b
        for b in context_blocks
        if "/" in b
        or b.endswith(".py")
        or b.endswith(".js")
        or b.endswith(".ts")
        or b.endswith(".go")
    ]

    # --- EXPLICIT_ASK ---
    evidence = _first_match(_EXPLICIT_ASK_RE, response_text)
    if evidence:
        gaps.append(
            ContextGap(
                query=query,
                signal_type=SignalType.EXPLICIT_ASK,
                evidence=evidence,
                timestamp=now,
                related_blocks=block_paths,
            )
        )

    # --- UNCERTAIN_ANSWER ---
    evidence = _first_match(_UNCERTAIN_RE, response_text)
    if evidence:
        gaps.append(
            ContextGap(
                query=query,
                signal_type=SignalType.UNCERTAIN_ANSWER,
                evidence=evidence,
                timestamp=now,
                related_blocks=block_paths,
            )
        )

    # --- MISSING_INFO (must also reference a file or function) ---
    evidence = _first_match(_MISSING_INFO_RE, response_text)
    if evidence and _has_file_or_fn_reference(response_text):
        gaps.append(
            ContextGap(
                query=query,
                signal_type=SignalType.MISSING_INFO,
                evidence=evidence,
                timestamp=now,
                related_blocks=block_paths,
            )
        )

    # --- HALLUCINATED_IMPORT ---
    imported_modules = _extract_imports_from_response(response_text)
    for module in imported_modules:
        # Skip stdlib / well-known packages — only flag project-specific modules
        _COMMON_STDLIB = {
            "os",
            "sys",
            "re",
            "json",
            "time",
            "math",
            "io",
            "abc",
            "enum",
            "typing",
            "pathlib",
            "datetime",
            "collections",
            "functools",
            "itertools",
            "hashlib",
            "threading",
            "logging",
            "unittest",
            "dataclasses",
            "contextlib",
            "copy",
            "random",
            "string",
            "subprocess",
            "shutil",
            "tempfile",
            "traceback",
            "warnings",
            "argparse",
            "struct",
            "socket",
            "http",
            "urllib",
            "email",
            "csv",
            "sqlite3",
            "pickle",
            "gzip",
            "zipfile",
            "tarfile",
            # common third-party
            "pytest",
            "numpy",
            "pandas",
            "requests",
            "flask",
            "django",
            "fastapi",
            "pydantic",
            "sqlalchemy",
            "aiohttp",
            "httpx",
            "click",
            "rich",
            "tqdm",
            "yaml",
            "toml",
            "dotenv",
        }
        if module in _COMMON_STDLIB:
            continue
        if not _is_module_in_context(module, context_blocks):
            gaps.append(
                ContextGap(
                    query=query,
                    signal_type=SignalType.HALLUCINATED_IMPORT,
                    evidence=f"import {module}",
                    timestamp=now,
                    related_blocks=block_paths,
                )
            )

    # --- WRONG_SIGNATURE ---
    # Build a combined context string for signature extraction
    combined_context = "\n".join(context_blocks)
    context_sigs = _extract_fn_signatures(combined_context)
    response_calls = _extract_fn_calls(response_text)

    for fn_name, call_argc in response_calls.items():
        if fn_name not in context_sigs:
            continue
        def_argc = context_sigs[fn_name]
        # Allow for `self` offset (Python methods: def foo(self, a) → 1 param for callers)
        if def_argc > 0 and abs(call_argc - (def_argc - 1)) > 0 and abs(call_argc - def_argc) > 0:
            gaps.append(
                ContextGap(
                    query=query,
                    signal_type=SignalType.WRONG_SIGNATURE,
                    evidence=f"{fn_name}(...) called with ~{call_argc} args; defined with {def_argc}",
                    timestamp=now,
                    related_blocks=block_paths,
                )
            )

    return gaps


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_gaps(gaps_path: str) -> List[dict]:
    """Load existing gaps from JSON store."""
    p = Path(gaps_path)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_gaps(gaps: List[ContextGap], gaps_path: str = DEFAULT_GAPS_PATH) -> None:
    """Append new gaps to gaps.json (does not overwrite existing entries)."""
    existing = _load_gaps(gaps_path)
    new_entries = []
    for gap in gaps:
        entry = asdict(gap)
        entry["signal_type"] = gap.signal_type.value  # Store as string
        new_entries.append(entry)
    combined = existing + new_entries
    p = Path(gaps_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(combined, indent=2))


def load_gaps(gaps_path: str = DEFAULT_GAPS_PATH) -> List[dict]:
    """Load all persisted gaps."""
    return _load_gaps(gaps_path)


# ---------------------------------------------------------------------------
# Retrieval expansion check
# ---------------------------------------------------------------------------


def _word_overlap_ratio(query_a: str, query_b: str) -> float:
    """
    Compute word overlap ratio between two queries.
    Returns overlap_count / len(shorter_query_words).
    """
    words_a = set(re.findall(r"\w+", query_a.lower()))
    words_b = set(re.findall(r"\w+", query_b.lower()))
    if not words_a or not words_b:
        return 0.0
    overlap = words_a & words_b
    return len(overlap) / min(len(words_a), len(words_b))


def should_expand_retrieval(
    query: str,
    gaps_path: str = DEFAULT_GAPS_PATH,
    overlap_threshold: float = 0.5,
) -> bool:
    """
    Check if the query is similar to a prior gap query (overlap ≥ threshold).
    Returns True if top_k should be expanded.
    """
    gaps = _load_gaps(gaps_path)
    for gap in gaps:
        prior_query = gap.get("query", "")
        if _word_overlap_ratio(query, prior_query) >= overlap_threshold:
            return True
    return False
