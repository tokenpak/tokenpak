from __future__ import annotations

import json

from tokenpak.agentic.error_normalizer import ErrorNormalizer, FailureSignatureDB


def test_default_port_bind_synonyms_normalize_to_single_signature():
    n = ErrorNormalizer()
    assert n.normalize("EADDRINUSE") == "PORT_BIND_FAILURE"
    assert n.normalize("address already in use") == "PORT_BIND_FAILURE"
    assert n.normalize("bind failed on 0.0.0.0:8080") == "PORT_BIND_FAILURE"


def test_external_pattern_config_from_home_path(tmp_path, monkeypatch):
    fake_home = tmp_path
    config_dir = fake_home / ".tokenpak"
    config_dir.mkdir(parents=True)
    (config_dir / "error_patterns.json").write_text(
        json.dumps([
            {"regex": "database is locked", "normalized_signature": "DB_LOCKED"}
        ])
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

    n = ErrorNormalizer()
    assert n.normalize("SQLite Error: database is locked") == "DB_LOCKED"


def test_db_normalizes_before_lookup_and_merges_stats():
    db = FailureSignatureDB()
    db.record_failure("EADDRINUSE", repair_recipe="switch-port")
    db.record_failure("address already in use", repair_recipe="switch-port")

    rec = db.lookup("bind failed on 0.0.0.0")
    assert rec is not None
    assert rec.signature == "PORT_BIND_FAILURE"
    assert rec.count == 2
    assert "switch-port" in rec.repair_recipes


def test_auto_learn_suggests_merge_when_recipe_shared_by_multiple_signatures():
    db = FailureSignatureDB()
    db.record_failure("EADDRINUSE", repair_recipe="switch-port")
    db.record_failure("CONNECTION REFUSED", repair_recipe="switch-port")

    suggestions = db.auto_learn_merge_suggestions()
    assert suggestions
    assert suggestions[0].recipe == "switch-port"
    assert len(suggestions[0].signatures) >= 2
