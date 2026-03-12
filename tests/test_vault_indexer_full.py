from pathlib import Path

from tokenpak.agent.vault.blocks import BlockStore
from tokenpak.agent.vault.indexer import VaultIndexer
from tokenpak.agent.vault.symbols import SymbolTable


def _make_indexer() -> VaultIndexer:
    return VaultIndexer(block_store=BlockStore(":memory:"), symbol_table=SymbolTable())


def test_indexer_supports_full_extension_set(tmp_path: Path):
    files = {
        "main.py": "APP_NAME = 'tokenpak'\n\ndef run():\n    return True\n",
        "script.js": "const API_URL = 'x';\nfunction boot() { return API_URL; }\n",
        "types.ts": "export const VERSION = '1.0';\nexport function start() { return VERSION; }\n",
        "view.jsx": "export const App = () => <div>ok</div>;\n",
        "component.tsx": "export const Button = () => <button>ok</button>;\n",
        "README.md": "# Title\n## Overview\nBody text.\n",
        "config.yaml": "name: tokenpak\nmode: dev\n",
        "config.yml": "service: indexer\nenabled: true\n",
        "data.json": '{"name":"tokenpak","version":1}',
        "run.sh": "echo run\n",
        "build.bash": "echo build\n",
        "lib.rs": "pub fn run() -> bool { true }\n",
        "main.go": "package main\nfunc Run() bool { return true }\n",
        "app.rb": "VALUE = 1\ndef run\n  VALUE\nend\n",
        "settings.toml": "[app]\nname = 'tokenpak'\n",
        ".env": "API_KEY=secret\n",
        "service.cfg": "mode=dev\n",
        "service.ini": "[app]\nmode=dev\n",
        "schema.sql": "CREATE TABLE users(id INTEGER PRIMARY KEY);\n",
        "index.html": "<html><body><h1>Hi</h1></body></html>\n",
        "styles.css": "body { color: #111; }\n",
    }

    for rel, content in files.items():
        (tmp_path / rel).write_text(content, encoding="utf-8")

    # Binary or unsupported files should not be indexed.
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.7\n")
    (tmp_path / "store.db").write_bytes(b"SQLite format 3\x00")

    indexer = _make_indexer()
    result = indexer.index_directory(str(tmp_path))

    assert result["files_indexed"] == len(files)
    assert result["files_skipped"] >= 2
    assert len(indexer.blocks.all()) == len(files)

    indexed_paths = {Path(record.path).name for record in indexer.blocks.all()}
    assert "image.png" not in indexed_paths
    assert "doc.pdf" not in indexed_paths
    assert "store.db" not in indexed_paths


def test_symbol_extraction_for_python_markdown_and_json(tmp_path: Path):
    py = tmp_path / "app.py"
    py.write_text(
        "CONFIG_VALUE = 5\n\n"
        "class Service:\n"
        "    pass\n\n"
        "def run_task():\n"
        "    return CONFIG_VALUE\n",
        encoding="utf-8",
    )
    md = tmp_path / "guide.md"
    md.write_text("# Project Guide\n## Usage\n", encoding="utf-8")
    jsn = tmp_path / "settings.json"
    jsn.write_text('{"name":"tokenpak","debug":true}', encoding="utf-8")

    indexer = _make_indexer()
    for p in (py, md, jsn):
        rec = indexer.index_file(str(p))
        assert rec is not None

    py_function = indexer.lookup_symbol("run_task")
    assert py_function and py_function[0].kind in {"function", "method"}

    py_class = indexer.lookup_symbol("Service")
    assert py_class and py_class[0].kind == "class"

    py_variable = indexer.lookup_symbol("CONFIG_VALUE")
    assert py_variable and py_variable[0].kind == "variable"

    md_header = indexer.lookup_symbol("Project Guide")
    assert md_header and md_header[0].kind == "header"

    json_key = indexer.lookup_symbol("name")
    assert json_key and any(sym.kind == "key" for sym in json_key)
