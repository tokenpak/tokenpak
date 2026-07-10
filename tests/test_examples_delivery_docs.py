from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DOCS_INDEX = ROOT / "docs" / "index.md"
EXAMPLES_README = ROOT / "examples" / "README.md"

CLONE_URL = "https://github.com/tokenpak/tokenpak.git"
ARCHIVE_URL = "https://github.com/tokenpak/tokenpak/archive/refs/heads/main.zip"
RUN_COMMAND = "python examples/basic_compression.py"
PIP_INSTALL_COMMAND = "python -m pip install -U tokenpak"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_examples_readme_documents_package_install_delivery_path() -> None:
    text = _read(EXAMPLES_README)

    assert CLONE_URL in text
    assert ARCHIVE_URL in text
    assert PIP_INSTALL_COMMAND in text
    assert RUN_COMMAND in text
    assert "not bundled inside" in text
    assert "PyPI wheel" in text
    assert "provider credentials" in text


def test_public_entrypoints_link_to_examples_delivery_path() -> None:
    readme = _read(README)
    docs_index = _read(DOCS_INDEX)

    assert "[examples/README.md](examples/README.md)" in readme
    assert "[Runnable Examples](../examples/README.md)" in docs_index
    assert CLONE_URL in readme
    assert RUN_COMMAND in readme
