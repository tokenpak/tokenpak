"""TokenPak Agent Vault AST Parser — language-specific code structure extraction."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ParsedNode:
    """A parsed code construct (function, class, import, etc.)."""

    kind: str  # "function" | "class" | "method" | "import" | "constant"
    name: str
    line_start: int
    line_end: int
    signature: str  # The declaration line(s)
    docstring: Optional[str] = None
    decorators: list = field(default_factory=list)


class ASTParser:
    """Language-aware parser that extracts structural information from code files.

    Supports Python natively via the stdlib ``ast`` module.
    Falls back to regex-based extraction for JS/TS and other languages.

    Usage::

        parser = ASTParser()
        nodes = parser.parse_file("mymodule.py", source_code)
        for node in nodes:
            print(node.kind, node.name, node.signature)
    """

    def parse_file(self, path: str, content: str) -> list[ParsedNode]:
        """Parse a source file and return a list of structural nodes."""
        ext = Path(path).suffix.lower()
        if ext == ".py":
            return self._parse_python(content)
        elif ext in (".js", ".jsx", ".ts", ".tsx"):
            return self._parse_javascript(content)
        else:
            return self._parse_generic(content)

    # ------------------------------------------------------------------
    # Python
    # ------------------------------------------------------------------

    def _parse_python(self, content: str) -> list[ParsedNode]:
        nodes: list[ParsedNode] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._parse_generic(content)

        lines = content.splitlines()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "function" if not self._is_method(node, tree) else "method"  # type: ignore
                sig = self._python_signature(node, lines)  # type: ignore
                docstring = ast.get_docstring(node)
                decorators = [f"@{self._unparse(d)}" for d in node.decorator_list]
                nodes.append(
                    ParsedNode(
                        kind=kind,
                        name=node.name,
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        signature=sig,
                        docstring=docstring,
                        decorators=decorators,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                sig = f"class {node.name}({', '.join(self._unparse(b) for b in node.bases)}):"
                docstring = ast.get_docstring(node)
                nodes.append(
                    ParsedNode(
                        kind="class",
                        name=node.name,
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        signature=sig,
                        docstring=docstring,
                    )
                )

        # Capture module-level assignments as variables/constants.
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    for name in self._extract_python_target_names(target):
                        nodes.append(
                            ParsedNode(
                                kind="variable",
                                name=name,
                                line_start=node.lineno,
                                line_end=node.end_lineno or node.lineno,
                                signature=f"{name} = ...",
                            )
                        )
            elif isinstance(node, ast.AnnAssign):
                for name in self._extract_python_target_names(node.target):
                    nodes.append(
                        ParsedNode(
                            kind="variable",
                            name=name,
                            line_start=node.lineno,
                            line_end=node.end_lineno or node.lineno,
                            signature=f"{name}: ...",
                        )
                    )

        # Capture module-level assignments as variables/constants.
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    for name in self._extract_python_target_names(target):
                        nodes.append(ParsedNode(
                            kind="variable",
                            name=name,
                            line_start=node.lineno,
                            line_end=node.end_lineno or node.lineno,
                            signature=f"{name} = ...",
                        ))
            elif isinstance(node, ast.AnnAssign):
                for name in self._extract_python_target_names(node.target):
                    nodes.append(ParsedNode(
                        kind="variable",
                        name=name,
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        signature=f"{name}: ...",
                    ))

        return sorted(nodes, key=lambda n: n.line_start)

    def _python_signature(self, node: ast.FunctionDef, lines: list[str]) -> str:
        """Reconstruct the def line from source."""
        try:
            line = lines[node.lineno - 1]
            # Include continuation lines if paren not closed
            open_p = line.count("(") - line.count(")")
            idx = node.lineno
            while open_p > 0 and idx < len(lines):
                idx += 1
                nxt = lines[idx - 1]
                line += "\n" + nxt
                open_p += nxt.count("(") - nxt.count(")")
            return line
        except IndexError:
            return f"def {node.name}(...):"

    def _is_method(self, func_node: ast.FunctionDef, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if item is func_node:
                        return True
        return False

    @staticmethod
    def _unparse(node: ast.expr) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return "..."

    def _extract_python_target_names(self, target: ast.AST) -> list[str]:
        """Extract simple variable names from assignment targets."""
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for elt in target.elts:
                names.extend(self._extract_python_target_names(elt))
            return names
        return []

    # ------------------------------------------------------------------
    # JavaScript / TypeScript (regex fallback)
    # ------------------------------------------------------------------

    def _parse_javascript(self, content: str) -> list[ParsedNode]:
        nodes: list[ParsedNode] = []
        lines = content.splitlines()

        # Match: function name(, const name = (...) =>, class Name
        patterns = [
            (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", "function"),
            (r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(.*\)\s*=>", "function"),
            (r"^(?:export\s+)?class\s+(\w+)", "class"),
            (r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", "variable"),
        ]

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            for pattern, kind in patterns:
                m = re.match(pattern, stripped)
                if m:
                    nodes.append(
                        ParsedNode(
                            kind=kind,
                            name=m.group(1),
                            line_start=i,
                            line_end=i,
                            signature=stripped[:120],
                        )
                    )
                    break

        return nodes

    # ------------------------------------------------------------------
    # Generic fallback
    # ------------------------------------------------------------------

    def _parse_generic(self, content: str) -> list[ParsedNode]:
        """Best-effort extraction using common patterns."""
        nodes: list[ParsedNode] = []
        lines = content.splitlines()

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Any line that looks like a definition
            m = re.match(
                r"^(?:pub\s+)?(?:fn|def|func|function|class|struct|enum)\s+(\w+)", stripped
            )
            if m:
                nodes.append(
                    ParsedNode(
                        kind="definition",
                        name=m.group(1),
                        line_start=i,
                        line_end=i,
                        signature=stripped[:120],
                    )
                )

        return nodes
