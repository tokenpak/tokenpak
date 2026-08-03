"""Publication contract for the six TIP v1 JSON schemas.

The TIP optimization schemas in ``tokenpak/tip/schemas/`` are published,
citable artifacts: each declares a canonical versioned ``$id`` URL and is
indexed (with a conformance statement) by ``docs/tip-schemas.md``. These tests
pin that contract so the published surface cannot drift silently:

- the six v1 schema files exist and parse as JSON,
- each is a structurally valid draft-07 JSON Schema,
- each ``$id`` is the canonical versioned URL derived from its filename,
- the docs index references every canonical ``$id`` and every filename.

Frozen-revision rule: a published v1 schema never changes incompatibly under
its v1 URL. Breaking changes ship as a new ``-v2`` schema at a new URL, so the
expected list below only ever grows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "tokenpak" / "tip" / "schemas"
DOCS_INDEX = REPO_ROOT / "docs" / "tip-schemas.md"

SCHEMA_ID_BASE = "https://docs.tokenpak.ai/schemas/tip/"

V1_SCHEMA_FILENAMES = [
    "tip-cache-policy.v1.json",
    "tip-capabilities.v1.json",
    "tip-compression-policy.v1.json",
    "tip-fidelity-policy.v1.json",
    "tip-optimization-trace.v1.json",
    "tip-route-class.v1.json",
]


def canonical_id(filename: str) -> str:
    """Map an in-tree schema filename to its canonical published ``$id``.

    ``tip-cache-policy.v1.json`` -> ``.../tip/tip-cache-policy-v1.json``
    (the published URL uses the dash-revision form).
    """
    stem, _, _ = filename.partition(".v1.json")
    return f"{SCHEMA_ID_BASE}{stem}-v1.json"


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict]:
    loaded = {}
    for name in V1_SCHEMA_FILENAMES:
        path = SCHEMA_DIR / name
        assert path.is_file(), f"missing published TIP schema: {path}"
        loaded[name] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def test_exactly_the_published_v1_schemas_exist(schemas):
    on_disk = sorted(p.name for p in SCHEMA_DIR.glob("*.v1.json"))
    assert on_disk == sorted(V1_SCHEMA_FILENAMES), (
        "v1 schema set changed; update the publication index and this contract"
    )


@pytest.mark.parametrize("name", V1_SCHEMA_FILENAMES)
def test_schema_declares_draft07_and_canonical_id(schemas, name):
    schema = schemas[name]
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert schema["$id"] == canonical_id(name)
    assert schema.get("title"), f"{name} must carry a title"
    assert schema.get("description"), f"{name} must carry a description"


@pytest.mark.parametrize("name", V1_SCHEMA_FILENAMES)
def test_schema_is_structurally_valid(schemas, name):
    jsonschema = pytest.importorskip("jsonschema")
    validator_cls = jsonschema.validators.validator_for(schemas[name])
    validator_cls.check_schema(schemas[name])


def test_docs_index_cites_every_schema(schemas):
    assert DOCS_INDEX.is_file(), f"missing schema publication index: {DOCS_INDEX}"
    index_text = DOCS_INDEX.read_text(encoding="utf-8")
    for name in V1_SCHEMA_FILENAMES:
        assert name in index_text, f"docs index does not mention {name}"
        assert canonical_id(name) in index_text, (
            f"docs index does not cite the canonical $id for {name}"
        )
