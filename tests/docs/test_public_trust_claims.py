"""Guard public docs against stale demo and install claims."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def _public_markdown_files() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def _public_text_files() -> list[Path]:
    files = _public_markdown_files()
    for suffix in ("*.md", "*.py", "*.sh"):
        files.extend(
            path
            for path in sorted((ROOT / "examples").rglob(suffix))
            if "__pycache__" not in path.parts
        )
    return files


def test_public_docs_do_not_reference_removed_optional_extras():
    stale_refs: list[str] = []
    for path in _public_text_files():
        text = path.read_text(encoding="utf-8")
        for forbidden in ("tokenpak[ml]", "tokenpak[tiktoken]", "tokenpak[ml,tiktoken]"):
            if forbidden in text:
                stale_refs.append(f"{path.relative_to(ROOT)}: {forbidden}")

    assert not stale_refs


def test_public_docs_do_not_present_fixture_as_receipt():
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in _public_markdown_files())

    for forbidden in (
        "Cost today: $0.002",
        "Tokens saved: 1,847",
        "tokenpak demo # Live demo",
        "Live Compression Demo",
    ):
        assert forbidden not in public_text

    assert "offline fixture" in public_text.lower()
    assert "receipt-backed savings" in public_text


def test_public_docs_avoid_overbroad_trust_claims():
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in _public_markdown_files())

    for forbidden in (
        "zero data leaving your machine",
        "80%+ of TokenPak operations",
        "The slim install keeps core functionality under 200 MB",
    ):
        assert forbidden not in public_text

    comparison = _read("docs/comparison.md")
    assert "configured upstream API" in comparison
    assert "No prompt content goes through TokenPak's cloud" in comparison
