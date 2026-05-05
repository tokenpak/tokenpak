#!/usr/bin/env bash
# Slim core install smoke for TokenPak.
#
# Creates a fresh virtualenv, installs tokenpak without extras, verifies the
# core metadata excludes TIP7 heavy packages, imports the current canonical slim
# surfaces, and fails if heavy optional packages resolve in the environment.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TMPDIR="${TMPDIR:-/tmp}"
VENV_DIR="$(mktemp -d "${TMPDIR%/}/tokenpak-slim-smoke.XXXXXX")"

cleanup() {
  if [[ "${TOKENPAK_KEEP_SLIM_SMOKE_VENV:-}" == "1" ]]; then
    echo "Keeping smoke venv: ${VENV_DIR}"
  else
    rm -rf "${VENV_DIR}"
  fi
}
trap cleanup EXIT

cd "${ROOT}"

"${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import pathlib
import tomllib

heavy = {
    "sentence-transformers",
    "tree-sitter-languages",
    "scipy",
    "scikit-learn",
    "pandas",
    "sympy",
    "llmlingua",
    "litellm",
    "transformers",
    "torch",
}

def normalize(name: str) -> str:
    out = name.lower().replace("_", "-").replace(".", "-")
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")

def dep_name(spec: str) -> str:
    head = spec.split(";", 1)[0].strip()
    for sep in ("[", "<", ">", "=", "!", "~", " "):
        head = head.split(sep, 1)[0]
    return normalize(head)

pyproject = pathlib.Path("pyproject.toml")
data = tomllib.loads(pyproject.read_text())
core = {dep_name(spec) for spec in data["project"].get("dependencies", [])}
found = sorted((core & heavy) | {name for name in core if name.startswith("nvidia-")})
print("CORE_HEAVY_DEPS", found)
if found:
    raise SystemExit(
        "heavy optional packages are present in [project.dependencies]: "
        + ", ".join(found)
    )
PY

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
"${VENV_DIR}/bin/python" -m pip install -e . >/dev/null

"${VENV_DIR}/bin/python" - <<'PY'
from __future__ import annotations

import importlib
import importlib.metadata as metadata
import importlib.util

for module in ("tokenpak", "tokenpak.proxy", "tokenpak.proxy.server"):
    importlib.import_module(module)

heavy_dists = {
    "sentence-transformers",
    "tree-sitter-languages",
    "scipy",
    "scikit-learn",
    "pandas",
    "sympy",
    "llmlingua",
    "litellm",
    "transformers",
    "torch",
}
installed = {dist.metadata["Name"].lower().replace("_", "-") for dist in metadata.distributions()}
resolved = sorted(
    name for name in installed if name in heavy_dists or name.startswith("nvidia-")
)
print("RESOLVED_HEAVY_DISTS", resolved)
if resolved:
    raise SystemExit("heavy optional packages resolved in slim env: " + ", ".join(resolved))

heavy_modules = {
    "torch",
    "sentence_transformers",
    "tree_sitter_languages",
    "scipy",
    "pandas",
    "sympy",
    "llmlingua",
    "litellm",
    "transformers",
}
present_modules = sorted(name for name in heavy_modules if importlib.util.find_spec(name) is not None)
print("RESOLVED_HEAVY_MODULES", present_modules)
if present_modules:
    raise SystemExit("heavy optional modules importable in slim env: " + ", ".join(present_modules))

print("SLIM_INSTALL_SMOKE_OK")
PY
