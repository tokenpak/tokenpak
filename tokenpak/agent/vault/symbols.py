"""TokenPak Agent Vault Symbol Table — extract and manage symbol definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .ast_parser import ASTParser, ParsedNode


@dataclass
class Symbol:
    """A named code symbol (function, class, constant, etc.)."""
    name: str
    kind: str           # "function" | "class" | "method" | "import" | "constant"
    path: str           # File path (relative or absolute)
    line: int           # Line number where defined
    signature: str      # Declaration text
    docstring: Optional[str] = None
    qualified_name: str = ""  # module.ClassName.method

    def __post_init__(self):
        if not self.qualified_name:
            module = Path(self.path).stem
            self.qualified_name = f"{module}.{self.name}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "signature": self.signature,
            "docstring": self.docstring,
            "qualified_name": self.qualified_name,
        }


class SymbolTable:
    """Build and query a symbol table from source files.

    Usage::

        table = SymbolTable()
        table.index_file("mymodule.py", source_code)
        results = table.lookup("MyClass")
        all_syms = table.all_symbols()
    """

    def __init__(self):
        self._symbols: list[Symbol] = []
        self._by_name: dict[str, list[Symbol]] = {}
        self._parser = ASTParser()

    def index_file(self, path: str, content: str) -> list[Symbol]:
        """Parse a file and add its symbols to the table. Returns new symbols."""
        nodes: list[ParsedNode] = self._parser.parse_file(path, content)
        new_symbols: list[Symbol] = []

        for node in nodes:
            sym = Symbol(
                name=node.name,
                kind=node.kind,
                path=path,
                line=node.line_start,
                signature=node.signature,
                docstring=node.docstring,
            )
            self._symbols.append(sym)
            self._by_name.setdefault(node.name, []).append(sym)
            new_symbols.append(sym)

        return new_symbols

    def lookup(self, name: str) -> list[Symbol]:
        """Find all symbols matching the given name (exact)."""
        return list(self._by_name.get(name, []))

    def search(self, query: str) -> list[Symbol]:
        """Case-insensitive substring search across symbol names."""
        q = query.lower()
        return [s for s in self._symbols if q in s.name.lower()]

    def all_symbols(self, kind: Optional[str] = None) -> list[Symbol]:
        """Return all symbols, optionally filtered by kind."""
        if kind:
            return [s for s in self._symbols if s.kind == kind]
        return list(self._symbols)

    def symbols_in_file(self, path: str) -> list[Symbol]:
        """Return all symbols defined in a given file."""
        return [s for s in self._symbols if s.path == path]

    def clear(self) -> None:
        """Remove all indexed symbols."""
        self._symbols.clear()
        self._by_name.clear()

    def __len__(self) -> int:
        return len(self._symbols)
