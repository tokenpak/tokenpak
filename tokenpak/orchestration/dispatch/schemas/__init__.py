"""JSON Schema exports for the Dispatch records (Standards Delta v0 §4–§5).

Each of the twelve records in
:data:`tokenpak.orchestration.dispatch.models.DISPATCH_RECORD_MODELS` has a
JSON Schema (Pydantic v2 / JSON Schema draft 2020-12) committed alongside this
module as ``<RecordName>.json``. The committed files are the published
contract; :func:`generate_schemas` reproduces them from the live models and
:func:`export_all_schemas` rewrites them on disk (used to regenerate after a
model change).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tokenpak.orchestration.dispatch.models import DISPATCH_RECORD_MODELS

SCHEMA_DIR = Path(__file__).parent


def generate_schemas() -> dict[str, dict[str, Any]]:
    """Return ``{record_name: json_schema}`` generated from the live models."""

    return {
        name: model.model_json_schema()
        for name, model in DISPATCH_RECORD_MODELS.items()
    }


def schema_path(name: str) -> Path:
    """Return the on-disk path for a record's committed JSON Schema."""

    return SCHEMA_DIR / f"{name}.json"


def export_all_schemas(dest_dir: Path | None = None) -> list[Path]:
    """Write every record's JSON Schema to ``dest_dir`` (default: this package).

    Returns the list of written paths. Files are pretty-printed with a trailing
    newline so the committed schemas are diff-friendly.
    """

    target = dest_dir or SCHEMA_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in generate_schemas().items():
        path = target / f"{name}.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


def load_schema(name: str) -> dict[str, Any]:
    """Load a committed JSON Schema by record name from disk."""

    path = schema_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"no committed JSON Schema for {name!r} at {path}; "
            "run export_all_schemas() to regenerate."
        )
    return json.loads(path.read_text())


__all__ = [
    "SCHEMA_DIR",
    "generate_schemas",
    "schema_path",
    "export_all_schemas",
    "load_schema",
]
