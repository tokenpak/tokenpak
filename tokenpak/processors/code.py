"""Code processor — extract signatures, imports, and docstrings."""

import re


class CodeProcessor:
    """Extract code structure while dropping implementation details."""

    def process(self, content: str, path: str = "") -> str:
        """
        Compress code by extracting structure.
        
        Strategy:
        - Keep all imports/requires
        - Keep function/class signatures + docstrings
        - Drop function bodies
        - Keep type definitions
        - Keep constants and module-level assignments
        """
        if path.endswith(".py"):
            return self._process_python(content)
        elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
            return self._process_javascript(content)
        else:
            return self._process_generic(content)

    def _process_python(self, content: str) -> str:
        """Extract Python structure."""
        lines = content.split("\n")
        result = []
        i = 0
        indent_stack = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # Blank lines between top-level items
            if not stripped:
                if result and result[-1] != "":
                    result.append("")
                i += 1
                continue

            # Imports — always keep
            if stripped.startswith(("import ", "from ")):
                result.append(line)
                # Handle multi-line imports
                while stripped.endswith("\\") or (stripped.count("(") > stripped.count(")")):
                    i += 1
                    if i >= len(lines):
                        break
                    line = lines[i]
                    stripped = line.strip()
                    result.append(line)
                i += 1
                continue

            # Comments at module level — keep
            if stripped.startswith("#") and not line.startswith(" "):
                result.append(line)
                i += 1
                continue

            # Class definitions
            if re.match(r'^class\s+\w+', stripped):
                result.append(line)
                i += 1
                # Grab docstring
                i = self._grab_docstring(lines, i, result)
                # Grab class body signatures (methods)
                i = self._grab_class_body(lines, i, result)
                continue

            # Function definitions (top-level)
            if re.match(r'^(?:async\s+)?def\s+\w+', stripped):
                # Include decorators above
                result.append(line)
                i += 1
                # Grab docstring
                i = self._grab_docstring(lines, i, result)
                # Skip body
                i = self._skip_body(lines, i, self._indent_level(line))
                result.append("")
                continue

            # Module-level constants and assignments
            if re.match(r'^[A-Z_][A-Z_0-9]*\s*=', stripped) or re.match(r'^\w+\s*:\s*\w+', stripped):
                result.append(line)
                i += 1
                continue

            # Decorators
            if stripped.startswith("@"):
                result.append(line)
                i += 1
                continue

            # Type aliases
            if re.match(r'^(type\s+|TypeAlias)', stripped):
                result.append(line)
                i += 1
                continue

            i += 1

        return "\n".join(result).strip()

    def _process_javascript(self, content: str) -> str:
        """Extract JavaScript/TypeScript structure."""
        lines = content.split("\n")
        result = []
        i = 0
        brace_depth = 0
        in_function_body = False
        function_brace_start = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                if result and result[-1] != "":
                    result.append("")
                i += 1
                continue

            # Imports/requires
            if re.match(r'^(import\s|const\s+\w+\s*=\s*require|export\s+(default\s+)?{)', stripped):
                result.append(line)
                # Multi-line import
                while not stripped.endswith(";") and not stripped.endswith("}"):
                    i += 1
                    if i >= len(lines):
                        break
                    line = lines[i]
                    stripped = line.strip()
                    result.append(line)
                i += 1
                continue

            # Export statements
            if stripped.startswith("export "):
                result.append(line)
                i += 1
                continue

            # Function/class/interface/type declarations
            if re.match(r'^(export\s+)?(async\s+)?function\s+\w+|^(export\s+)?class\s+\w+|^(export\s+)?(interface|type)\s+\w+', stripped):
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
            if re.match(r'^(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s+)?\(', stripped):
                result.append(line)
                if "{" in stripped:
                    i += 1
                    i = self._skip_braces(lines, i)
                else:
                    i += 1
                result.append("")
                continue

            # Constants
            if re.match(r'^(export\s+)?(const|let|var)\s+[A-Z_]', stripped):
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

    def _process_generic(self, content: str) -> str:
        """Fallback: keep first 100 lines."""
        lines = content.split("\n")[:100]
        return "\n".join(lines)

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

    def _grab_class_body(self, lines: list, i: int, result: list) -> int:
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

            # Method definitions
            if re.match(r'^\s+(?:async\s+)?def\s+\w+', line):
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

            # Class-level assignments
            if re.match(r'^\s+\w+\s*[=:]', line) and indent == class_indent + 4:
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
