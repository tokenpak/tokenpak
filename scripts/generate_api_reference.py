#!/usr/bin/env python3
"""Generate API reference markdown from tokenpak source code.

Scans public classes and methods, then writes docs/API_REFERENCE.md.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "tokenpak"
OUT_FILE = ROOT / "docs" / "API_REFERENCE.md"

EXCLUDE_DIRS = {"__pycache__", "Projects"}
EXCLUDE_FILES = {"__main__.py"}


@dataclass
class MethodDoc:
    name: str
    signature: str
    doc: str
    returns: str
    raises: list[str] = field(default_factory=list)


@dataclass
class ClassDoc:
    module: str
    name: str
    bases: list[str]
    doc: str
    methods: list[MethodDoc] = field(default_factory=list)


def _ann_to_str(node: ast.AST | None) -> str:
    if node is None:
        return "Any"
    try:
        return ast.unparse(node)
    except Exception:
        return "Any"


def _format_arg(arg: ast.arg, default: ast.AST | None = None) -> str:
    part = arg.arg
    if arg.annotation is not None:
        part += f": {_ann_to_str(arg.annotation)}"
    if default is not None:
        try:
            part += f" = {ast.unparse(default)}"
        except Exception:
            part += " = ..."
    return part


def _build_signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = fn.args
    parts: list[str] = []

    pos_args = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(pos_args) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(pos_args, defaults):
        parts.append(_format_arg(arg, default))

    if args.vararg:
        var = f"*{args.vararg.arg}"
        if args.vararg.annotation:
            var += f": {_ann_to_str(args.vararg.annotation)}"
        parts.append(var)
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_format_arg(arg, default))

    if args.kwarg:
        kw = f"**{args.kwarg.arg}"
        if args.kwarg.annotation:
            kw += f": {_ann_to_str(args.kwarg.annotation)}"
        parts.append(kw)

    ret = _ann_to_str(fn.returns)
    prefix = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {fn.name}({', '.join(parts)}) -> {ret}"


def _extract_raises(doc: str) -> list[str]:
    if not doc:
        return []
    lines = [ln.strip() for ln in doc.splitlines()]
    out: list[str] = []
    in_raises = False
    for ln in lines:
        low = ln.lower()
        if low in {"raises:", "raise:", "exceptions:"}:
            in_raises = True
            continue
        if in_raises:
            if not ln:
                if out:
                    break
                continue
            if ":" in ln:
                out.append(ln.split(":", 1)[0].strip(" -`*"))
            else:
                out.append(ln.strip(" -`*"))
    return [r for r in out if r]


def discover_python_files() -> Iterable[Path]:
    for path in sorted(SRC_DIR.rglob("*.py")):
        rel = path.relative_to(SRC_DIR)
        if any(p in EXCLUDE_DIRS for p in rel.parts):
            continue
        if path.name in EXCLUDE_FILES:
            continue
        yield path


def parse_classes(path: Path) -> list[ClassDoc]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    module = path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")
    classes: list[ClassDoc] = []

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name.startswith("_"):
            continue
        class_doc = ast.get_docstring(node) or ""
        bases = [_ann_to_str(b) for b in node.bases] or ["object"]
        cdoc = ClassDoc(module=module, name=node.name, bases=bases, doc=class_doc)

        for inner in node.body:
            if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if inner.name.startswith("_") and inner.name not in {"__init__", "__call__"}:
                continue
            mdoc = ast.get_docstring(inner) or ""
            cdoc.methods.append(
                MethodDoc(
                    name=inner.name,
                    signature=_build_signature(inner),
                    doc=mdoc,
                    returns=_ann_to_str(inner.returns),
                    raises=_extract_raises(mdoc),
                )
            )

        if cdoc.methods:
            classes.append(cdoc)

    return classes


def build_markdown(classes: list[ClassDoc]) -> str:
    total_methods = sum(len(c.methods) for c in classes)
    lines: list[str] = []
    lines.append("# TokenPak API Reference")
    lines.append("")
    lines.append("> Auto-generated from source code docstrings and type hints via `scripts/generate_api_reference.py`.")
    lines.append("")
    lines.append(f"**Public classes:** {len(classes)}  ")
    lines.append(f"**Public methods:** {total_methods}")
    lines.append("")

    lines.append("## API Index")
    lines.append("")
    lines.append("- **TokenPakClient**: SDK client usage pattern (documented in examples; production-facing entrypoint is `ContextPack` + connectors/processors)")
    lines.append("- **TokenPakProxy**: Proxy service capabilities (implemented across `proxy.py` and `tokenpak/proxy/*` adapters)")
    lines.append("- **Adapters**: `tokenpak.adapters.*`, `tokenpak.proxy.adapters.*`, `tokenpak.telemetry.adapters.*`")
    lines.append("- **Metrics**: `tokenpak.monitoring.metrics.ProxyMetricsCollector`, telemetry collectors/storage")
    lines.append("- **Cache**: `tokenpak.cache.*`, `tokenpak.telemetry.cache.CacheStore`")
    lines.append("- **Config**: `tokenpak.telemetry.config.*`, policy/config models across modules")
    lines.append("")

    lines.append("## Type Hints Guide")
    lines.append("")
    lines.append("- `Optional[T]` means parameter may be `None`.")
    lines.append("- `Union[A, B]` or `A | B` means either type is accepted/returned.")
    lines.append("- Container hints (`list[T]`, `dict[K, V]`) define item/key/value types.")
    lines.append("- Return type `Any` indicates dynamically shaped data.")
    lines.append("")

    example_classes = [
        "ContextPack",
        "RequestValidator",
        "OpenAIAdapter",
        "AnthropicAdapter",
        "ProxyMetricsCollector",
    ]
    lines.append("## Code Examples")
    lines.append("")
    for cls in example_classes:
        lines.append(f"### {cls}")
        lines.append("```python")
        if cls == "ContextPack":
            lines += [
                "from tokenpak.pack import ContextPack",
                "pack = ContextPack()",
                "result = pack.compile_blocks(raw_blocks, source='notes.md')",
            ]
        elif cls == "RequestValidator":
            lines += [
                "from tokenpak.validation.request_validator import RequestValidator",
                "validator = RequestValidator()",
                "validation = validator.validate(payload)",
            ]
        elif cls == "OpenAIAdapter":
            lines += [
                "from tokenpak.adapters.openai import OpenAIAdapter",
                "adapter = OpenAIAdapter(model='gpt-4o-mini', api_key='...')",
                "response = adapter.complete(messages)",
            ]
        elif cls == "AnthropicAdapter":
            lines += [
                "from tokenpak.adapters.anthropic import AnthropicAdapter",
                "adapter = AnthropicAdapter(model='claude-3-5-sonnet-latest', api_key='...')",
                "response = adapter.complete(messages)",
            ]
        else:
            lines += [
                "from tokenpak.monitoring.metrics import ProxyMetricsCollector",
                "metrics = ProxyMetricsCollector()",
                "metrics.record_request(provider='openai', status='ok', latency_ms=120)",
            ]
        lines.append("```")
        lines.append("")

    lines.append("## Class Reference")
    lines.append("")

    for c in classes:
        lines.append(f"### `{c.module}.{c.name}`")
        lines.append("")
        lines.append(f"**Bases:** {', '.join(c.bases)}")
        if c.doc:
            lines.append("")
            lines.append(c.doc.strip())
        lines.append("")

        for m in c.methods:
            lines.append(f"#### `{m.name}`")
            lines.append("")
            lines.append("```python")
            lines.append(m.signature)
            lines.append("```")
            lines.append("")
            lines.append(f"- **Returns:** `{m.returns}`")
            if m.raises:
                lines.append(f"- **Raises:** {', '.join(f'`{r}`' for r in m.raises)}")
            if m.doc:
                lines.append(f"- **Description:** {m.doc.strip().splitlines()[0]}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    all_classes: list[ClassDoc] = []
    for pyfile in discover_python_files():
        all_classes.extend(parse_classes(pyfile))

    all_classes.sort(key=lambda c: (c.module, c.name))
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(build_markdown(all_classes), encoding="utf-8")
    print(f"Wrote {OUT_FILE} with {len(all_classes)} classes")


if __name__ == "__main__":
    main()
