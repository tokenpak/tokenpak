"""Architecture-contract gate tests.

Four surfaces enforce the declared-debt ratchet:

1. Positive: the real external ``lint-imports`` executable reads the committed
   ``.importlinter`` and exits 0 on the current tree.
2. Negative: removing any single ``ignore_imports`` entry (an unlisted edge)
   turns the gate red — proves the contract actually detects violations.
3. Ledger integrity + monotonicity: the ``ignore_imports`` set and
   ``docs/import-debt-ledger.md`` are exactly 1:1, and every current edge stays
   inside the pinned exact baseline (no same-count debt substitution).
4. Services coverage: ``tokenpak/services/`` is a regular package and every
   module under it is visible to the import graph (grimp), so no service
   module can evade the gate.
"""

from __future__ import annotations

import configparser
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / ".importlinter"
LEDGER_PATH = REPO_ROOT / "docs" / "import-debt-ledger.md"
DEBT_BASELINE_PATH = Path(__file__).with_name("import_debt_baseline.txt")

# Initial baseline: 83 declared debt edges. This number may only DECREASE.
# Increasing it requires an explicit release waiver recorded in the ledger.
DEBT_BASELINE = 83

EDGE_RE = re.compile(r"^(?P<src>[\w.]+)\s*->\s*(?P<dst>[\w.]+)$")


def _parse_config_ignores(config_text: str) -> dict[str, set[str]]:
    """Return {contract_section: {"src -> dst", ...}} from .importlinter."""
    parser = configparser.ConfigParser()
    parser.read_string(config_text)
    ignores: dict[str, set[str]] = {}
    for section in parser.sections():
        if not section.startswith("importlinter:contract:"):
            continue
        raw = parser.get(section, "ignore_imports", fallback="")
        edges = set()
        for line in raw.splitlines():
            line = line.split(";")[0].strip()
            if not line:
                continue
            match = EDGE_RE.match(line)
            assert match, f"unparseable ignore_imports line in {section}: {line!r}"
            edges.add(f"{match.group('src')} -> {match.group('dst')}")
        ignores[section] = edges
    return ignores


def _parse_ledger_rows(ledger_text: str) -> dict[str, str]:
    """Return {IMP-id: "src -> dst"} from the ledger's markdown tables."""
    row_re = re.compile(r"^\|\s*(IMP-\d{3})\s*\|\s*`([\w.]+)\s*->\s*([\w.]+)`\s*\|")
    rows: dict[str, str] = {}
    for line in ledger_text.splitlines():
        match = row_re.match(line.strip())
        if match:
            imp_id, src, dst = match.groups()
            assert imp_id not in rows, f"duplicate ledger row {imp_id}"
            rows[imp_id] = f"{src} -> {dst}"
    return rows


def _lint_imports_bin() -> str:
    exe = shutil.which("lint-imports")
    if exe is None:
        candidate = Path(sys.executable).with_name("lint-imports")
        if candidate.exists():
            exe = str(candidate)
    if exe is None:
        pytest.skip("import-linter not installed (pip install -e .[dev])")
    return exe


def _run_gate(config: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_lint_imports_bin(), "--config", str(config)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_gate_is_green_on_committed_config():
    """The real lint-imports executable exits 0 against the committed config."""
    result = _run_gate(CONFIG_PATH)
    assert result.returncode == 0, (
        f"lint-imports exited {result.returncode}; the architecture gate is "
        f"red — either a NEW import edge was added (fix the import; do not "
        f"extend ignore_imports without a release waiver) or a ledgered "
        f"edge was removed from code but not from .importlinter + ledger.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "0 broken" in result.stdout or "broken." not in result.stdout


def test_unlisted_edge_turns_gate_red(tmp_path):
    """Dropping one ignore entry (simulating an unlisted edge) must fail."""
    sentinel = "tokenpak.routing.fallback -> tokenpak.orchestration.retry"
    original = CONFIG_PATH.read_text()
    assert sentinel in original, "sentinel edge missing from config"
    mutated_lines = [line for line in original.splitlines() if line.strip() != sentinel]
    mutated = "\n".join(mutated_lines) + "\n"
    assert mutated != original
    mutated_config = tmp_path / ".importlinter"
    mutated_config.write_text(mutated)

    result = _run_gate(mutated_config)
    assert result.returncode != 0, (
        "gate stayed green with an unlisted edge — the contract is not "
        "actually enforcing the boundary"
    )
    assert "tokenpak.routing.fallback" in result.stdout, (
        f"failure output does not name the violating edge:\n{result.stdout}"
    )


def test_unlisted_entrypoint_bypass_turns_gate_red(tmp_path):
    """The entrypoint boundary contract detects an undeclared direct bypass."""
    sentinel = "tokenpak.cli.commands.preview -> tokenpak.compression.core"
    original = CONFIG_PATH.read_text()
    assert sentinel in original, "entrypoint bypass sentinel missing from config"
    mutated_lines = [line for line in original.splitlines() if line.strip() != sentinel]
    mutated_config = tmp_path / ".importlinter"
    mutated_config.write_text("\n".join(mutated_lines) + "\n")

    result = _run_gate(mutated_config)
    assert result.returncode != 0, (
        "entrypoint boundary stayed green with an undeclared direct pipeline import"
    )
    assert "tokenpak.cli.commands.preview" in result.stdout, (
        f"failure output does not name the violating entrypoint:\n{result.stdout}"
    )


def test_ledger_and_config_are_one_to_one():
    """Every ignore edge has exactly one ledger row and vice versa."""
    ignores = _parse_config_ignores(CONFIG_PATH.read_text())
    union = set().union(*ignores.values())
    ledger = _parse_ledger_rows(LEDGER_PATH.read_text())
    ledger_edges = set(ledger.values())

    missing_from_ledger = union - ledger_edges
    missing_from_config = ledger_edges - union
    assert not missing_from_ledger, (
        f"ignored edges with no ledger row: {sorted(missing_from_ledger)}"
    )
    assert not missing_from_config, (
        f"ledger rows with no ignore entry: {sorted(missing_from_config)}"
    )
    # Ledger rows are unique per edge (no two IMP ids for one edge).
    assert len(ledger_edges) == len(ledger), "duplicate edge across ledger IDs"


def test_debt_is_monotonically_shrinking():
    """Current debt must be a subset of the pinned exact baseline."""
    ledger = _parse_ledger_rows(LEDGER_PATH.read_text())
    ledger_edges = set(ledger.values())
    baseline_edges = {
        line.strip() for line in DEBT_BASELINE_PATH.read_text().splitlines() if line.strip()
    }
    assert len(baseline_edges) == DEBT_BASELINE, (
        f"exact baseline has {len(baseline_edges)} unique edges, expected {DEBT_BASELINE}"
    )
    substituted = ledger_edges - baseline_edges
    assert not substituted, (
        "ledger contains debt outside the pinned baseline; same-count debt "
        f"substitution is forbidden: {sorted(substituted)}"
    )
    assert len(ledger) <= DEBT_BASELINE, (
        f"ledger has {len(ledger)} rows > baseline {DEBT_BASELINE}. Adding "
        f"debt requires an explicit release waiver; new import edges must be "
        f"fixed, not ledgered."
    )
    baseline_note = re.search(r"\*\*Baseline row count:\*\* \*\*(\d+)\*\*", LEDGER_PATH.read_text())
    assert baseline_note, "ledger is missing its baseline row count marker"
    assert int(baseline_note.group(1)) == DEBT_BASELINE, (
        "ledger baseline marker and test DEBT_BASELINE diverged — update both "
        "together (downward only)"
    )


def test_services_package_fully_covered():
    """services/ is a regular package and every module is graph-visible."""
    services_dir = REPO_ROOT / "tokenpak" / "services"
    assert (services_dir / "__init__.py").exists(), (
        "tokenpak/services/__init__.py missing — grimp is blind to services modules without it"
    )

    grimp = pytest.importorskip("grimp")
    graph = grimp.build_graph("tokenpak")
    expected = set()
    for py_file in services_dir.rglob("*.py"):
        rel = py_file.relative_to(REPO_ROOT)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        expected.add(".".join(parts))
    missing = {mod for mod in expected if mod not in graph.modules}
    assert not missing, f"services modules invisible to the import graph: {sorted(missing)}"
