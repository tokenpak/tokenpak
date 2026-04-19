"""Tree-sitter Code Processor for TokenPak.

Parses code files structurally via tree-sitter and extracts the API surface:
imports, class/function signatures, docstrings, type definitions, and
top-level constants. Function bodies are replaced with `...` or `{}`.

Supported languages: Python, JavaScript, TypeScript, Go, Rust.
Graceful fallback to CodeProcessor if tree-sitter fails to parse.

Target compression: 3-10x on typical code files.
"""

import warnings
from typing import Optional

# Suppress the tree_sitter_languages FutureWarning about old API
warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")

try:
    from tree_sitter_languages import get_parser as _ts_get_parser

    _TS_AVAILABLE = True
except ImportError:
    _TS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Language → file extension mapping
# ---------------------------------------------------------------------------

EXTENSION_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
}


def _detect_language(path: str) -> Optional[str]:
    """Detect tree-sitter language from file extension."""
    from pathlib import Path

    suffix = Path(path).suffix.lower()
    return EXTENSION_TO_LANG.get(suffix)


def is_available() -> bool:
    """Return True if tree-sitter is importable and working."""
    return _TS_AVAILABLE


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------


def _text(node) -> str:
    """Decode a node's source text."""
    return (node.text or b"").decode("utf-8", errors="replace")


def _sig_before_body(node, body_types: tuple) -> str:
    """
    Return node text up to (not including) the first child whose type is in
    body_types.  Used to extract function/class signatures.
    """
    src = _text(node)
    for child in node.children:
        if child.type in body_types:
            offset = child.start_byte - node.start_byte
            return src[:offset].rstrip()
    return src


def _first_docstring(block_node) -> Optional[str]:
    """
    Return the first docstring from a Python block node, or None.
    A docstring is an expression_statement whose sole child is a string.
    """
    for child in block_node.children:
        if child.type == "expression_statement":
            for grandchild in child.children:
                if grandchild.type == "string":
                    return _text(child)
            break  # Only the very first statement can be a docstring
        elif child.type not in ("\n", "comment"):
            break
    return None


# ---------------------------------------------------------------------------
# Python extractor
# ---------------------------------------------------------------------------

_PY_BODY = ("block",)
_PY_MODULE_KEEP = {
    "import_statement",
    "import_from_statement",
    "comment",
    "expression_statement",  # module-level constants / type aliases
    "type_alias_statement",
}
_PY_FN_TYPES = {"function_definition", "async_function_definition"}
_PY_CLASS_TYPES = {"class_definition"}
_PY_SKIP = {
    "if_statement",
    "for_statement",
    "while_statement",
    "with_statement",
    "try_statement",
    "match_statement",
}


def _py_format_fn(node, indent: str = "") -> str:
    """Format a Python function_definition: signature + optional docstring + `...`."""
    sig = _sig_before_body(node, _PY_BODY)
    # Add `...` on the line after the signature
    lines = [f"{indent}{sig.strip()}"]

    # Look for docstring in the body block
    for child in node.children:
        if child.type == "block":
            doc = _first_docstring(child)
            if doc:
                lines.append(f"{indent}    {doc.strip()}")
    lines.append(f"{indent}    ...")
    return "\n".join(lines)


def _py_format_class(node, indent: str = "") -> str:
    """Format a Python class_definition: header + optional docstring + method stubs."""
    sig = _sig_before_body(node, _PY_BODY)
    lines = [f"{indent}{sig.strip()}"]

    for child in node.children:
        if child.type != "block":
            continue

        # Class docstring
        doc = _first_docstring(child)
        if doc:
            lines.append(f"{indent}    {doc.strip()}")

        # Members
        for member in child.children:
            mt = member.type

            # Decorated functions / classes
            if mt == "decorated_definition":
                for dec in member.children:
                    if dec.type == "decorator":
                        lines.append(f"{indent}    {_text(dec).strip()}")
                    elif dec.type in _PY_FN_TYPES:
                        lines.append(_py_format_fn(dec, indent + "    "))
                        lines.append("")
                    elif dec.type in _PY_CLASS_TYPES:
                        lines.append(_py_format_class(dec, indent + "    "))
                        lines.append("")

            elif mt in _PY_FN_TYPES:
                lines.append(_py_format_fn(member, indent + "    "))
                lines.append("")

            elif mt == "expression_statement":
                # Class-level assignments (e.g. `x: int = 0`)
                txt = _text(member).strip()
                if txt and not txt.startswith('"""') and not txt.startswith("'''"):
                    lines.append(f"{indent}    {txt}")

        break  # Processed the single block child

    return "\n".join(lines)


def _extract_python(source: str) -> str:
    """Extract Python API surface using tree-sitter."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parser = _ts_get_parser("python")

    tree = parser.parse(source.encode())
    root = tree.root_node

    parts = []

    for node in root.children:
        nt = node.type

        if nt in _PY_MODULE_KEEP:
            txt = _text(node).strip()
            if txt:
                parts.append(txt)

        elif nt == "decorated_definition":
            # Decorator + function or class
            decorator_lines = []
            target = None
            for child in node.children:
                if child.type == "decorator":
                    decorator_lines.append(_text(child).strip())
                elif child.type in _PY_FN_TYPES:
                    target = ("fn", child)
                elif child.type in _PY_CLASS_TYPES:
                    target = ("cls", child)
            for dec in decorator_lines:
                parts.append(dec)
            if target:
                kind, child = target
                if kind == "fn":
                    parts.append(_py_format_fn(child))
                else:
                    parts.append(_py_format_class(child))

        elif nt in _PY_FN_TYPES:
            parts.append(_py_format_fn(node))

        elif nt in _PY_CLASS_TYPES:
            parts.append(_py_format_class(node))

        elif nt in _PY_SKIP:
            pass  # Drop runtime code at module level

    return "\n\n".join(p for p in parts if p.strip())


# ---------------------------------------------------------------------------
# JavaScript / TypeScript extractor
# ---------------------------------------------------------------------------

_JS_BODY = ("statement_block",)
_JS_MODULE_KEEP = {
    "import_statement",
    "import_declaration",
    "comment",
}
_JS_FN_TYPES = {"function_declaration", "generator_function_declaration"}
_JS_CLASS_TYPES = {"class_declaration"}
_JS_EXPORT_KEEP = {"export_statement"}


def _js_format_fn(node, indent: str = "") -> str:
    """Format a JS function_declaration: signature + `{}`."""
    sig = _sig_before_body(node, _JS_BODY)
    return f"{indent}{sig.strip()} {{}}"


def _js_format_class(node, indent: str = "") -> str:
    """Format a JS class_declaration: header + method stubs."""
    sig = _sig_before_body(node, ("class_body",))
    lines = [f"{indent}{sig.strip()} {{"]

    for child in node.children:
        if child.type != "class_body":
            continue
        for member in child.children:
            mt = member.type
            if mt in {"method_definition", "field_definition"}:
                msig = _sig_before_body(member, _JS_BODY)
                lines.append(f"{indent}  {msig.strip()} {{}}")
            elif mt == "comment":
                lines.append(f"{indent}  {_text(member).strip()}")
        break

    lines.append(f"{indent}}}")
    return "\n".join(lines)


def _js_format_export(node, indent: str = "") -> str:
    """Format an export_statement, drilling into the exported declaration."""
    parts = []
    for child in node.children:
        if child.type in _JS_FN_TYPES:
            parts.append(_js_format_fn(child, indent))
        elif child.type in _JS_CLASS_TYPES:
            parts.append(_js_format_class(child, indent))
        elif child.type == "lexical_declaration":
            parts.append(_js_format_lexical(child, indent))
        elif child.type not in {"export", "default", ";"}:
            txt = _text(child).strip()
            if txt:
                parts.append(f"{indent}{txt}")
    return "\n".join(parts) if parts else f"{indent}{_text(node).strip()}"


def _js_format_lexical(node, indent: str = "") -> str:
    """Format a const/let/var declaration, stripping arrow function bodies."""
    src = _text(node).strip()
    # For arrow functions, strip the body
    if "=>" in src and "{" in src:
        arrow_idx = src.index("=>")
        brace_idx = src.index("{", arrow_idx)
        return f"{indent}{src[:brace_idx].rstrip()} {{}}"
    return f"{indent}{src}"


def _extract_javascript(source: str, lang: str = "javascript") -> str:
    """Extract JS/TS API surface using tree-sitter."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parser = _ts_get_parser(lang)

    tree = parser.parse(source.encode())
    root = tree.root_node
    parts = []

    for node in root.children:
        nt = node.type

        if nt in _JS_MODULE_KEEP:
            parts.append(_text(node).strip())

        elif nt in _JS_FN_TYPES:
            parts.append(_js_format_fn(node))

        elif nt in _JS_CLASS_TYPES:
            parts.append(_js_format_class(node))

        elif nt in _JS_EXPORT_KEEP:
            parts.append(_js_format_export(node))

        elif nt == "lexical_declaration":
            # Only keep constants (uppercase) or simple type assignments
            src = _text(node).strip()
            name_match = None
            for child in node.children:
                if child.type == "variable_declarator":
                    for gc in child.children:
                        if gc.type == "identifier":
                            name_match = _text(gc)
                            break
                    break
            if name_match and (name_match[0].isupper() or "type" in src[:10]):
                parts.append(_js_format_lexical(node))

    return "\n\n".join(p for p in parts if p.strip())


# ---------------------------------------------------------------------------
# Go extractor
# ---------------------------------------------------------------------------

_GO_BODY = ("block",)
_GO_MODULE_KEEP = {
    "package_clause",
    "import_declaration",
    "const_declaration",
    "var_declaration",
    "comment",
}
_GO_FN_TYPES = {"function_declaration", "method_declaration"}
_GO_TYPE_TYPES = {"type_declaration"}


def _go_format_fn(node) -> str:
    """Format a Go function/method: signature + `{}`."""
    sig = _sig_before_body(node, _GO_BODY)
    return f"{sig.strip()} {{}}"


def _extract_go(source: str) -> str:
    """Extract Go API surface using tree-sitter."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parser = _ts_get_parser("go")

    tree = parser.parse(source.encode())
    root = tree.root_node
    parts = []

    for node in root.children:
        nt = node.type

        if nt in _GO_MODULE_KEEP:
            parts.append(_text(node).strip())

        elif nt in _GO_FN_TYPES:
            parts.append(_go_format_fn(node))

        elif nt in _GO_TYPE_TYPES:
            # Structs, interfaces, type aliases — keep in full (no body to strip)
            parts.append(_text(node).strip())

    return "\n\n".join(p for p in parts if p.strip())


# ---------------------------------------------------------------------------
# Rust extractor
# ---------------------------------------------------------------------------

_RS_BODY = ("block",)
_RS_IMPL_BODY = ("declaration_list",)
_RS_MODULE_KEEP = {
    "use_declaration",
    "const_item",
    "type_item",
    "static_item",
    "attribute_item",
    "inner_attribute_item",
    "line_comment",
    "block_comment",
}
_RS_STRUCT_TYPES = {"struct_item", "enum_item", "trait_item", "union_item"}
_RS_FN_TYPES = {"function_item"}
_RS_IMPL_TYPES = {"impl_item"}


def _rs_format_fn(node, indent: str = "") -> str:
    """Format a Rust function_item: signature + `{}`."""
    sig = _sig_before_body(node, _RS_BODY)
    return f"{indent}{sig.strip()} {{}}"


def _rs_format_impl(node) -> str:
    """Format a Rust impl block: header + method stubs."""
    sig = _sig_before_body(node, _RS_IMPL_BODY)
    lines = [f"{sig.strip()} {{"]

    for child in node.children:
        if child.type != "declaration_list":
            continue
        for member in child.children:
            mt = member.type
            if mt in _RS_FN_TYPES:
                lines.append(_rs_format_fn(member, "    "))
            elif mt in {"attribute_item", "line_comment"}:
                lines.append(f"    {_text(member).strip()}")
        break

    lines.append("}")
    return "\n".join(lines)


def _extract_rust(source: str) -> str:
    """Extract Rust API surface using tree-sitter."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parser = _ts_get_parser("rust")

    tree = parser.parse(source.encode())
    root = tree.root_node
    parts = []

    for node in root.children:
        nt = node.type

        if nt in _RS_MODULE_KEEP:
            parts.append(_text(node).strip())

        elif nt in _RS_STRUCT_TYPES:
            # Structs/enums/traits — keep in full (field list, not a body to drop)
            parts.append(_text(node).strip())

        elif nt in _RS_FN_TYPES:
            parts.append(_rs_format_fn(node))

        elif nt in _RS_IMPL_TYPES:
            parts.append(_rs_format_impl(node))

    return "\n\n".join(p for p in parts if p.strip())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract(source: str, path: str) -> Optional[str]:
    """
    Extract API surface from a code file using tree-sitter.

    Args:
        source: Full file content as a string.
        path:   File path (used for language detection).

    Returns:
        Extracted/compressed source string, or None if language unsupported
        or tree-sitter is unavailable.
    """
    if not _TS_AVAILABLE:
        return None

    lang = _detect_language(path)
    if lang is None:
        return None

    try:
        if lang == "python":
            return _extract_python(source)
        elif lang in ("javascript",):
            return _extract_javascript(source, "javascript")
        elif lang == "typescript":
            return _extract_javascript(source, "typescript")
        elif lang == "go":
            return _extract_go(source)
        elif lang == "rust":
            return _extract_rust(source)
    except Exception:
        return None  # Signal fallback to caller

    return None


class TreeSitterProcessor:
    """
    Processor that uses tree-sitter to extract code structure.

    Drop-in replacement for CodeProcessor for supported languages.
    Falls back to CodeProcessor on parse failure or unsupported language.
    """

    def __init__(self, fallback=None):
        # Import here to avoid circular imports
        from .code import CodeProcessor

        self._fallback = fallback or CodeProcessor()

    def process(self, content: str, path: str = "") -> str:
        """
        Process a code file: extract API surface via tree-sitter, fall back
        to CodeProcessor if tree-sitter is unavailable, unsupported, or fails.

        Also logs original_tokens vs extracted_tokens for compression metrics.
        """
        result = extract(content, path)
        if result is not None and result.strip():
            return result
        # Fallback
        return self._fallback.process(content, path)
