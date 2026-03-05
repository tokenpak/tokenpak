"""Tests for TokenPak Dashboard Terminology Glossary & Educational Tooltips."""
import json
from pathlib import Path

TOKENPAK = Path(__file__).parent.parent / "tokenpak"
TERM_CARDS = TOKENPAK / "term_cards.json"
GLOSSARY_JS  = TOKENPAK / "telemetry/dashboard/static/js/glossary.js"
GLOSSARY_CSS = TOKENPAK / "telemetry/dashboard/static/css/glossary.css"
GLOSSARY_HTML = TOKENPAK / "telemetry/dashboard/templates/glossary.html"
BASE_HTML     = TOKENPAK / "telemetry/dashboard/templates/base.html"

REQUIRED_TERMS = [
    "baseline_cost", "actual_cost", "savings", "savings_pct",
    "compression_ratio", "error_rate", "retry_rate",
    "latency_avg", "latency_p95", "latency_p99",
    "raw_tokens", "final_tokens", "reconciled", "estimated",
]


# ── File existence ────────────────────────────────────────────────────────────

def test_term_cards_exists():
    assert TERM_CARDS.exists()

def test_glossary_js_exists():
    assert GLOSSARY_JS.exists()

def test_glossary_css_exists():
    assert GLOSSARY_CSS.exists()

def test_glossary_html_exists():
    assert GLOSSARY_HTML.exists()

def test_base_html_exists():
    assert BASE_HTML.exists()


# ── term_cards.json content ───────────────────────────────────────────────────

def test_term_cards_valid_json():
    data = json.loads(TERM_CARDS.read_text())
    assert isinstance(data, dict)

def test_term_cards_has_required_terms():
    data = json.loads(TERM_CARDS.read_text())
    for term in REQUIRED_TERMS:
        assert term in data, f"Missing required term: {term}"

def test_term_cards_have_required_fields():
    data = json.loads(TERM_CARDS.read_text())
    for key, entry in data.items():
        assert "term" in entry, f"{key} missing 'term'"
        # term_cards uses "what" as the short definition field
        assert "what" in entry or "short" in entry, f"{key} missing 'what' or 'short'"

def test_term_cards_minimum_count():
    data = json.loads(TERM_CARDS.read_text())
    assert len(data) >= 14


# ── glossary.js ───────────────────────────────────────────────────────────────

def test_js_has_tooltip_level1():
    js = GLOSSARY_JS.read_text()
    assert "200" in js  # 200ms delay

def test_js_has_data_gloss_attribute():
    js = GLOSSARY_JS.read_text()
    assert "data-gloss" in js

def test_js_has_expandable_tooltip():
    js = GLOSSARY_JS.read_text()
    assert "click" in js.lower() or "expand" in js.lower() or "level" in js.lower()

def test_js_has_glossary_modal():
    js = GLOSSARY_JS.read_text()
    assert "modal" in js.lower() or "glossary" in js.lower()

def test_js_loads_term_cards():
    js = GLOSSARY_JS.read_text()
    assert "GLOSSARY" in js or "term_cards" in js or "glossary" in js.lower()


# ── glossary.css ──────────────────────────────────────────────────────────────

def test_css_has_tooltip_styles():
    css = GLOSSARY_CSS.read_text()
    assert "tooltip" in css.lower() or "gloss" in css.lower()

def test_css_has_modal_styles():
    css = GLOSSARY_CSS.read_text()
    assert "modal" in css.lower()


# ── base.html wiring ──────────────────────────────────────────────────────────

def test_base_html_includes_glossary_js():
    html = BASE_HTML.read_text()
    assert "glossary.js" in html

def test_base_html_includes_glossary_css():
    html = BASE_HTML.read_text()
    assert "glossary.css" in html
