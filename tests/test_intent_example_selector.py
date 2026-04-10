from pathlib import Path

import yaml

from tokenpak.proxy.example_selector import IntentExampleSelector


def _write_example(root: Path, intent: str, name: str, text: str) -> None:
    d = root / intent
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


def test_selects_examples_for_enabled_intent(tmp_path: Path) -> None:
    examples_root = tmp_path / "examples"
    config_path = examples_root / "config.yaml"

    _write_example(examples_root, "classify", "edge-1.md", "A" * 200)
    _write_example(examples_root, "classify", "edge-2.md", "B" * 220)

    cfg = {
        "defaults": {"enabled": False, "max_examples": 0, "min_remaining_tokens": 100},
        "intents": {
            "classify": {
                "enabled": True,
                "max_examples": 2,
                "min_remaining_tokens": 100,
                "files": ["edge-1.md", "edge-2.md"],
            }
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    selector = IntentExampleSelector(examples_root=examples_root, config_path=config_path)
    result = selector.select(intent="classify", token_budget=2000, reserved_tokens=200)

    assert result.skipped_reason == ""
    assert len(result.selected) == 2
    assert result.used_tokens > 0


def test_skips_disabled_intent(tmp_path: Path) -> None:
    examples_root = tmp_path / "examples"
    config_path = examples_root / "config.yaml"

    _write_example(examples_root, "translation", "pair-1.md", "hello -> hola")
    cfg = {
        "defaults": {"enabled": False, "max_examples": 1},
        "intents": {"translation": {"enabled": False, "max_examples": 1}},
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    selector = IntentExampleSelector(examples_root=examples_root, config_path=config_path)
    result = selector.select(intent="translation", token_budget=4000)

    assert result.selected == ()
    assert result.skipped_reason == "intent_disabled"


def test_skips_when_budget_too_tight(tmp_path: Path) -> None:
    examples_root = tmp_path / "examples"
    config_path = examples_root / "config.yaml"

    _write_example(examples_root, "extraction", "schema-1.md", "field: value\n" * 50)
    cfg = {
        "defaults": {"enabled": True, "max_examples": 2, "min_remaining_tokens": 600},
        "intents": {"extraction": {"enabled": True, "max_examples": 2, "min_remaining_tokens": 600}},
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    selector = IntentExampleSelector(examples_root=examples_root, config_path=config_path)
    result = selector.select(intent="extraction", token_budget=800, reserved_tokens=300)

    assert result.selected == ()
    assert result.skipped_reason.startswith("tight_budget")
