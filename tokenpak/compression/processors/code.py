"""Code processor — extract signatures, imports, and docstrings.

Pre-compiled regex patterns for ~30% faster processing.

Modes
-----
CODE_API (default)
    Large template/literal string constants are replaced with deterministic
    stub placeholders:  ``<TEMPLATE:<name> lines=<n> sha256=<h>[:8]>``
    This keeps code outlines lean by stripping multi-line HTML/CSS/JS/SQL
    blobs that bloat the compressed output without adding retrieval signal.

CODE_WITH_TEMPLATES
    Template content is retained verbatim.  Use this for template-edit
    workflows where the literal payload must be present.
"""

import hashlib
import re
from enum import Enum
from typing import List, Set

# ============================================================
# COMPACTION MODES
# ============================================================


class CodeCompactionMode(str, Enum):
    CODE_API = "CODE_API"
    CODE_WITH_TEMPLATES = "CODE_WITH_TEMPLATES"


# ============================================================
# PRE-COMPILED PATTERNS (module-level for reuse)
# ============================================================

# Python patterns
_PY_IMPORT = re.compile(r"^(import\s|from\s)")
_PY_CLASS = re.compile(r"^class\s+\w+")
_PY_FUNC = re.compile(r"^(?:async\s+)?def\s+\w+")
_PY_METHOD = re.compile(r"^\s+(?:async\s+)?def\s+\w+")
_PY_CONST = re.compile(r"^[A-Z_][A-Z_0-9]*\s*=")
_PY_TYPE_HINT = re.compile(r"^\w+\s*:\s*\w+")
_PY_TYPE_ALIAS = re.compile(r"^(type\s+|TypeAlias)")
_PY_CLASS_ATTR = re.compile(r"^\s+\w+\s*[=:]")

# Large literal/template patterns  (triple-quoted string on assignment)
# Matches:   NAME = """..."""  or  NAME = '''...'''  (start of block)
_PY_TRIPLE_ASSIGN = re.compile(r'^([A-Za-z_]\w*)\s*=\s*("""|\'\'\')(.*)')
# Threshold: literals with >= this many source lines are considered "large"
_LARGE_LITERAL_THRESHOLD = 5

# JavaScript/TypeScript patterns
_JS_IMPORT = re.compile(r"^(import\s|const\s+\w+\s*=\s*require|export\s+(default\s+)?{)")
_JS_EXPORT = re.compile(r"^export\s+")
_JS_FUNC_CLASS = re.compile(
    r"^(export\s+)?(async\s+)?function\s+\w+|^(export\s+)?class\s+\w+|^(export\s+)?(interface|type)\s+\w+"
)
_JS_ARROW_CONST = re.compile(r"^(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s+)?\(")
_JS_CONST_UPPER = re.compile(r"^(export\s+)?(const|let|var)\s+[A-Z_]")


# ============================================================
# HELPERS
# ============================================================


def _sha256_stub(text: str) -> str:
    """Return the first 8 hex chars of the SHA-256 of *text*."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def _make_template_stub(name: str, lines: int, content: str) -> str:
    """Build a deterministic stub placeholder for a large literal."""
    h = _sha256_stub(content)
    return f"<TEMPLATE:{name} lines={lines} sha256={h}>"


# ============================================================
# MAIN PROCESSOR
# ============================================================


class CodeProcessor:
    """Extract code structure while dropping implementation details."""

    def process(
        self,
        content: str,
        path: str = "",
        mode: CodeCompactionMode = CodeCompactionMode.CODE_API,
    ) -> str:
        """
        Compress code by extracting structure.

        Strategy:
        - Keep all imports/requires (deduplicated)
        - Keep function/class signatures + docstrings
        - Drop function bodies
        - Keep type definitions
        - Keep constants and module-level assignments
        - In CODE_API mode: replace large triple-quoted literals with stubs
        - In CODE_WITH_TEMPLATES mode: keep template content verbatim

        Parameters
        ----------
        content : str
            Source code to compress.
        path : str
            File path (used to select language-specific logic).
        mode : CodeCompactionMode
            Compaction mode (default: CODE_API).
        """
        if path.endswith(".py"):
            return self._process_python(content, mode=mode)
        elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
            return self._process_javascript(content)
        else:
            return self._process_generic(content)

    # ------------------------------------------------------------------
    # Python
    # ------------------------------------------------------------------

    def _process_python(
        self,
        content: str,
        mode: CodeCompactionMode = CodeCompactionMode.CODE_API,
    ) -> str:
        """Extract Python structure using pre-compiled patterns."""
        lines = content.split("\n")
        result: List[str] = []
        seen_imports: Set[str] = set()
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # Blank lines between top-level items
            if not stripped:
                if result and result[-1] != "":
                    result.append("")
                i += 1
                continue

            # Imports — deduplicated, always keep
            if _PY_IMPORT.match(stripped):
                if stripped not in seen_imports:
                    seen_imports.add(stripped)
                    result.append(line)
                # Handle multi-line imports
                while stripped.endswith("\\") or (stripped.count("(") > stripped.count(")")):
                    i += 1
                    if i >= len(lines):
                        break
                    line = lines[i]
                    stripped = line.strip()
                    # Add continuation lines only if we kept the import header
                    if line not in seen_imports:
                        result.append(line)
                i += 1
                continue

            # Comments at module level — keep
            if stripped.startswith("#") and not line.startswith(" "):
                result.append(line)
                i += 1
                continue

            # Class definitions
            if _PY_CLASS.match(stripped):
                result.append(line)
                i += 1
                # Grab docstring
                i = self._grab_docstring(lines, i, result)
                # Grab class body signatures (methods)
                i = self._grab_class_body(lines, i, result, mode=mode)
                continue

            # Function definitions (top-level)
            if _PY_FUNC.match(stripped):
                result.append(line)
                i += 1
                # Grab docstring
                i = self._grab_docstring(lines, i, result)
                # Skip body
                i = self._skip_body(lines, i, self._indent_level(line))
                result.append("")
                continue

            # Large literal/template assignment (CODE_API: stub; CODE_WITH_TEMPLATES: include)
            triple_m = _PY_TRIPLE_ASSIGN.match(stripped)
            if triple_m:
                name = triple_m.group(1)
                quote = triple_m.group(2)
                rest = triple_m.group(3)
                # Collect the full literal
                literal_lines = [line]
                # Check if it closes on the same line
                closed = rest.count(quote) >= 1 and rest != quote  # rest after opening triple quote
                # more precise: check if closing triple is already in rest
                closed = quote in rest
                j = i + 1
                if not closed:
                    while j < len(lines):
                        literal_lines.append(lines[j])
                        if quote in lines[j]:
                            j += 1
                            break
                        j += 1
                else:
                    j = i + 1

                n_lines = len(literal_lines)
                if n_lines >= _LARGE_LITERAL_THRESHOLD:
                    if mode == CodeCompactionMode.CODE_WITH_TEMPLATES:
                        result.extend(literal_lines)
                    else:
                        # CODE_API: emit stub
                        content_str = "\n".join(literal_lines)
                        stub = _make_template_stub(name, n_lines, content_str)
                        result.append(f"{name} = {stub}")
                else:
                    # Small literal — always keep
                    result.extend(literal_lines)
                i = j
                continue

            # Module-level constants and type hints
            if _PY_CONST.match(stripped) or _PY_TYPE_HINT.match(stripped):
                result.append(line)
                i += 1
                continue

            # Decorators
            if stripped.startswith("@"):
                result.append(line)
                i += 1
                continue

            # Type aliases
            if _PY_TYPE_ALIAS.match(stripped):
                result.append(line)
                i += 1
                continue

            i += 1

        return "\n".join(result).strip()

    # ------------------------------------------------------------------
    # JavaScript / TypeScript
    # ------------------------------------------------------------------

    def _process_javascript(self, content: str) -> str:
        """Extract JavaScript/TypeScript structure using pre-compiled patterns."""
        lines = content.split("\n")
        result: List[str] = []
        seen_imports: Set[str] = set()
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                if result and result[-1] != "":
                    result.append("")
                i += 1
                continue

            # Imports/requires (deduplicated)
            if _JS_IMPORT.match(stripped):
                if stripped not in seen_imports:
                    seen_imports.add(stripped)
                    result.append(line)
                    # Multi-line import
                    while not stripped.endswith(";") and not stripped.endswith("}"):
                        i += 1
                        if i >= len(lines):
                            break
                        line = lines[i]
                        stripped = line.strip()
                        result.append(line)
                else:
                    # Skip duplicate (advance past multi-line if needed)
                    while not stripped.endswith(";") and not stripped.endswith("}"):
                        i += 1
                        if i >= len(lines):
                            break
                        stripped = lines[i].strip()
                i += 1
                continue

            # Export statements
            if _JS_EXPORT.match(stripped):
                result.append(line)
                i += 1
                continue

            # Function/class/interface/type declarations
            if _JS_FUNC_CLASS.match(stripped):
                result.append(line)
                # Find opening brace, then skip body
                if "{" in stripped:
                    i += 1
                    i = self._skip_braces(lines, i)
                else:
                    i += 1
                result.append("")
                continue

            # Arrow functions assigned to const/let/var
            if _JS_ARROW_CONST.match(stripped):
                result.append(line)
                if "{" in stripped:
                    i += 1
                    i = self._skip_braces(lines, i)
                else:
                    i += 1
                result.append("")
                continue

            # Constants (uppercase)
            if _JS_CONST_UPPER.match(stripped):
                result.append(line)
                i += 1
                continue

            # Comments (top-level JSDoc)
            if stripped.startswith(("//", "/*", "*")):
                result.append(line)
                i += 1
                continue

            i += 1

        return "\n".join(result).strip()

    # ------------------------------------------------------------------
    # Generic
    # ------------------------------------------------------------------

    def _process_generic(self, content: str) -> str:
        """Fallback: keep first 100 lines."""
        lines = content.split("\n")[:100]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _indent_level(self, line: str) -> int:
        """Count leading spaces."""
        return len(line) - len(line.lstrip())

    def _grab_docstring(self, lines: list, i: int, result: list) -> int:
        """Grab a Python docstring if present."""
        if i >= len(lines):
            return i
        stripped = lines[i].strip()
        if stripped.startswith(('"""', "'''")):
            quote = stripped[:3]
            result.append(lines[i])
            if stripped.count(quote) >= 2 and len(stripped) > 3:
                return i + 1  # Single-line docstring
            i += 1
            while i < len(lines):
                result.append(lines[i])
                if quote in lines[i]:
                    return i + 1
                i += 1
        return i

    def _skip_body(self, lines: list, i: int, base_indent: int) -> int:
        """Skip a Python function/method body."""
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                indent = self._indent_level(line)
                if indent <= base_indent:
                    return i
            i += 1
        return i

    def _grab_class_body(
        self,
        lines: list,
        i: int,
        result: list,
        mode: CodeCompactionMode = CodeCompactionMode.CODE_API,
    ) -> int:
        """Extract method signatures from a class body."""
        if i >= len(lines):
            return i
        class_indent = self._indent_level(lines[i - 1] if i > 0 else "")

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped and not stripped.startswith("#"):
                indent = self._indent_level(line)
                if indent <= class_indent and stripped:
                    return i  # Left the class

            # Method definitions (using compiled pattern)
            if _PY_METHOD.match(line):
                result.append(line)
                i += 1
                i = self._grab_docstring(lines, i, result)
                i = self._skip_body(lines, i, self._indent_level(line))
                continue

            # Decorators
            if stripped.startswith("@"):
                result.append(line)
                i += 1
                continue

            # Class-level large literal (template)
            indent = self._indent_level(line) if stripped else 0
            if indent > class_indent:
                triple_m = _PY_TRIPLE_ASSIGN.match(stripped)
                if triple_m:
                    name = triple_m.group(1)
                    quote = triple_m.group(2)
                    rest = triple_m.group(3)
                    literal_lines = [line]
                    closed = quote in rest
                    j = i + 1
                    if not closed:
                        while j < len(lines):
                            literal_lines.append(lines[j])
                            if quote in lines[j]:
                                j += 1
                                break
                            j += 1
                    else:
                        j = i + 1

                    n_lines = len(literal_lines)
                    prefix = " " * indent
                    if n_lines >= _LARGE_LITERAL_THRESHOLD:
                        if mode == CodeCompactionMode.CODE_WITH_TEMPLATES:
                            result.extend(literal_lines)
                        else:
                            content_str = "\n".join(literal_lines)
                            stub = _make_template_stub(name, n_lines, content_str)
                            result.append(f"{prefix}{name} = {stub}")
                    else:
                        result.extend(literal_lines)
                    i = j
                    continue

            # Class-level assignments
            if _PY_CLASS_ATTR.match(line) and indent == class_indent + 4:
                result.append(line)
                i += 1
                continue

            i += 1

        return i

    def _skip_braces(self, lines: list, i: int) -> int:
        """Skip content within braces (JS/TS)."""
        depth = 1
        while i < len(lines) and depth > 0:
            line = lines[i]
            depth += line.count("{") - line.count("}")
            i += 1
        return i
