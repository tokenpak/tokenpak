"""
tokenpak/agent/semantic/term_card_builder.py

Tiered Lazy Term-Card Builder
==============================
Generates and maintains 5W1H micro term-cards for the TokenPak knowledge base.

Tiers
-----
  Tier 0 — hand-curated high-frequency terms (100–300 target)
  Tier 1 — auto-generated medium-confidence terms extracted from index/source
  Tier 2 — lazy on-demand generation when an unknown term appears at runtime

Card schema (all string fields have hard character caps):
  canonical key      str   (snake_case)
  term               str   (≤60 chars)
  what               str   (≤120 chars)
  who                str   (≤80 chars)
  where              list  (each item ≤60 chars, ≤5 items)
  why                str   (≤120 chars)
  how                str   (≤150 chars)
  not_this           str   (≤120 chars, optional)
  aliases            list  (each item ≤40 chars, ≤6 items)
  tier               int   (0 | 1 | 2)
  confidence         float (0.0–1.0)
  source_refs        list  (each item ≤80 chars, ≤10 items)
  updated_at         str   ISO-8601
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard character caps per field
# ---------------------------------------------------------------------------

CAPS: dict[str, int] = {
    "term": 60,
    "what": 120,
    "who": 80,
    "why": 120,
    "how": 150,
    "not_this": 120,
}
CAPS_LIST_ITEM: dict[str, int] = {
    "where": 60,
    "aliases": 40,
    "source_refs": 80,
}
CAPS_LIST_MAX: dict[str, int] = {
    "where": 5,
    "aliases": 6,
    "source_refs": 10,
}

REQUIRED_FIELDS = {"term", "what", "who", "where", "why", "how", "aliases", "tier", "confidence", "source_refs"}

TERM_CARDS_PATH = Path(__file__).resolve().parents[2] / "term_cards.json"

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _truncate(value: str, cap: int) -> str:
    """Hard-truncate a string to `cap` characters."""
    if len(value) > cap:
        logger.debug("Truncating field value from %d to %d chars", len(value), cap)
        return value[: cap - 1].rstrip() + "…"
    return value


def enforce_caps(card: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *card* with all fields truncated to their hard caps."""
    out = dict(card)
    for field, cap in CAPS.items():
        if field in out and isinstance(out[field], str):
            out[field] = _truncate(out[field], cap)
    for field, item_cap in CAPS_LIST_ITEM.items():
        if field in out and isinstance(out[field], list):
            max_items = CAPS_LIST_MAX[field]
            out[field] = [
                _truncate(str(item), item_cap) for item in out[field][:max_items]
            ]
    return out


def validate_card(card: dict[str, Any]) -> list[str]:
    """
    Return a list of validation error strings.
    Empty list means the card is valid.
    """
    errors: list[str] = []
    term = card.get("term", "<unknown>")

    missing = REQUIRED_FIELDS - set(card.keys())
    if missing:
        errors.append(f"[{term}] Missing required fields: {sorted(missing)}")

    for field, cap in CAPS.items():
        value = card.get(field, "")
        if isinstance(value, str) and len(value) > cap:
            errors.append(f"[{term}] Field '{field}' exceeds {cap} chars ({len(value)})")

    for field, item_cap in CAPS_LIST_ITEM.items():
        items = card.get(field, [])
        if isinstance(items, list):
            if len(items) > CAPS_LIST_MAX[field]:
                errors.append(
                    f"[{term}] Field '{field}' has {len(items)} items (max {CAPS_LIST_MAX[field]})"
                )
            for i, item in enumerate(items):
                if isinstance(item, str) and len(item) > item_cap:
                    errors.append(
                        f"[{term}] Field '{field}[{i}]' exceeds {item_cap} chars ({len(item)})"
                    )

    tier = card.get("tier")
    if tier not in (0, 1, 2):
        errors.append(f"[{term}] Invalid tier: {tier!r} (must be 0, 1, or 2)")

    confidence = card.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        errors.append(f"[{term}] Invalid confidence: {confidence!r} (must be 0.0–1.0)")

    return errors


# ---------------------------------------------------------------------------
# Alias conflict detection
# ---------------------------------------------------------------------------


def detect_alias_conflicts(cards: dict[str, Any]) -> list[str]:
    """
    Scan all cards and report any alias that maps to more than one canonical term.
    Returns a list of human-readable conflict descriptions (non-silent).
    """
    alias_map: dict[str, list[str]] = defaultdict(list)
    for canonical, card in cards.items():
        # canonical key itself
        alias_map[canonical.lower()].append(canonical)
        for alias in card.get("aliases", []):
            alias_map[alias.lower()].append(canonical)

    conflicts = []
    for alias, owners in alias_map.items():
        unique_owners = list(dict.fromkeys(owners))  # preserve insertion order, dedup
        if len(unique_owners) > 1:
            conflicts.append(
                f"Alias conflict: '{alias}' is claimed by: {', '.join(unique_owners)}"
            )
    return conflicts


# ---------------------------------------------------------------------------
# Deterministic sort + serialisation
# ---------------------------------------------------------------------------


def sort_cards(cards: dict[str, Any]) -> dict[str, Any]:
    """Return cards dict sorted deterministically: tier ASC, term ASC."""
    return dict(
        sorted(
            cards.items(),
            key=lambda kv: (kv[1].get("tier", 9), kv[0].lower()),
        )
    )


def load_cards(path: Path = TERM_CARDS_PATH) -> dict[str, Any]:
    """Load term_cards.json; return empty dict if missing."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_cards(cards: dict[str, Any], path: Path = TERM_CARDS_PATH) -> None:
    """Save cards to JSON with deterministic ordering."""
    sorted_cards = sort_cards(cards)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(sorted_cards, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Tier 1 auto-extraction from source files
# ---------------------------------------------------------------------------

# Patterns to identify candidate terms in Python source
_SNAKE_IDENTIFIER = re.compile(r"\b([a-z][a-z0-9]+(?:_[a-z0-9]+){2,})\b")
_CONST_IDENTIFIER = re.compile(r"\b([A-Z][A-Z0-9]+(?:_[A-Z0-9]+){1,})\b")
_CLASS_NAME = re.compile(r"class\s+([A-Za-z][A-Za-z0-9]+)(?:\(|:)")

# Minimum occurrences before a term is considered "medium-confidence"
_MIN_OCCURRENCES = 3


def extract_candidates_from_source(
    source_root: Path,
    existing_keys: set[str],
) -> dict[str, dict[str, Any]]:
    """
    Scan Python source files under *source_root* and return candidate Tier 1
    term cards for snake_case identifiers that appear ≥ _MIN_OCCURRENCES times
    and are not already in *existing_keys*.

    The returned cards are stubs — callers should enrich them before saving.
    """
    frequency: dict[str, int] = defaultdict(int)
    source_map: dict[str, list[str]] = defaultdict(list)

    py_files = list(source_root.rglob("*.py"))
    for py_file in py_files:
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(py_file.relative_to(source_root))
        for match in _SNAKE_IDENTIFIER.finditer(text):
            ident = match.group(1)
            if ident in existing_keys:
                continue
            # Skip very short or generic Python keywords
            if len(ident) < 5:
                continue
            frequency[ident] += 1
            if rel not in source_map[ident]:
                source_map[ident].append(rel)

    now = datetime.now(timezone.utc).isoformat()
    candidates: dict[str, dict[str, Any]] = {}
    for ident, count in frequency.items():
        if count >= _MIN_OCCURRENCES and ident not in existing_keys:
            candidates[ident] = {
                "term": ident,
                "what": f"Auto-extracted identifier '{ident}' — needs human review.",
                "who": "Engineering",
                "where": [],
                "why": "Appears frequently in codebase",
                "how": f"Seen {count}× in source",
                "not_this": "",
                "aliases": [],
                "tier": 1,
                "confidence": min(0.4 + count * 0.02, 0.75),
                "source_refs": source_map[ident][:CAPS_LIST_MAX["source_refs"]],
                "updated_at": now,
            }
    return candidates


# ---------------------------------------------------------------------------
# Tier 2 lazy-add
# ---------------------------------------------------------------------------


def lazy_add(
    term: str,
    cards: dict[str, Any],
    source_refs: Optional[list[str]] = None,
    path: Path = TERM_CARDS_PATH,
) -> dict[str, Any]:
    """
    Add a stub Tier 2 card for *term* if it doesn't already exist.
    Returns the card (existing or newly created).
    Saves to disk immediately.
    """
    key = term.lower().replace(" ", "_")
    if key in cards:
        return cards[key]

    now = datetime.now(timezone.utc).isoformat()
    stub = enforce_caps(
        {
            "term": term[:CAPS["term"]],
            "what": f"Unknown term '{term}' — pending enrichment.",
            "who": "Unknown",
            "where": [],
            "why": "Encountered at runtime with no existing definition",
            "how": "Lazy-generated stub; requires human review",
            "not_this": "",
            "aliases": [],
            "tier": 2,
            "confidence": 0.1,
            "source_refs": (source_refs or [])[:CAPS_LIST_MAX["source_refs"]],
            "updated_at": now,
        }
    )
    cards[key] = stub
    save_cards(cards, path)
    logger.info("Lazy-added Tier 2 stub for term: %s → %s", term, key)
    return stub


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------


def validation_report(cards: dict[str, Any]) -> str:
    """
    Run full validation over all cards and return a human-readable report.
    Also detects alias conflicts.
    """
    lines: list[str] = []
    all_errors: list[str] = []

    for canonical, card in cards.items():
        errors = validate_card(card)
        all_errors.extend(errors)

    conflicts = detect_alias_conflicts(cards)

    tier_counts = defaultdict(int)
    for card in cards.values():
        tier_counts[card.get("tier", "?")] += 1

    lines.append(f"=== TokenPak Term-Card Validation Report ===")
    lines.append(f"Total cards : {len(cards)}")
    for t in sorted(k for k in tier_counts if isinstance(k, int)):
        lines.append(f"  Tier {t}    : {tier_counts[t]}")
    lines.append("")

    if all_errors:
        lines.append(f"Schema errors ({len(all_errors)}):")
        for err in all_errors:
            lines.append(f"  ✗ {err}")
    else:
        lines.append("Schema errors: none ✓")

    lines.append("")
    if conflicts:
        lines.append(f"Alias conflicts ({len(conflicts)}):")
        for c in conflicts:
            lines.append(f"  ⚠ {c}")
    else:
        lines.append("Alias conflicts: none ✓")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build pipeline — Tier 0/1 batch build
# ---------------------------------------------------------------------------


def build(
    source_root: Optional[Path] = None,
    cards_path: Path = TERM_CARDS_PATH,
    add_tier1: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Full build pipeline:
      1. Load existing cards.
      2. Enforce caps on all existing cards (normalise).
      3. Optionally scan source for Tier 1 candidates.
      4. Detect alias conflicts (report, don't silently skip).
      5. Sort deterministically.
      6. Save (unless dry_run).
      7. Return final cards dict.
    """
    cards = load_cards(cards_path)
    logger.info("Loaded %d existing cards from %s", len(cards), cards_path)

    # Normalise existing cards
    normalised = 0
    for key in list(cards.keys()):
        before = json.dumps(cards[key])
        cards[key] = enforce_caps(cards[key])
        if json.dumps(cards[key]) != before:
            normalised += 1
    if normalised:
        logger.info("Normalised %d cards to enforce field caps", normalised)

    # Tier 1 auto-extraction
    new_candidates = 0
    if add_tier1 and source_root and source_root.exists():
        candidates = extract_candidates_from_source(source_root, set(cards.keys()))
        logger.info("Found %d Tier 1 candidates from source scan", len(candidates))
        for key, card in candidates.items():
            if key not in cards:
                cards[key] = card
                new_candidates += 1

    # Conflict detection (always run, always report)
    conflicts = detect_alias_conflicts(cards)
    if conflicts:
        logger.warning("Alias conflicts detected:")
        for c in conflicts:
            logger.warning("  %s", c)
    else:
        logger.info("No alias conflicts detected.")

    # Sort
    cards = sort_cards(cards)

    if not dry_run:
        save_cards(cards, cards_path)
        logger.info("Saved %d cards to %s", len(cards), cards_path)

    return cards


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli():
    import argparse

    parser = argparse.ArgumentParser(
        description="TokenPak Tiered Lazy Term-Card Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # build
    p_build = sub.add_parser("build", help="Run full Tier 0/1 build pipeline")
    p_build.add_argument("--source-root", default=str(Path(__file__).resolve().parents[3]),
                         help="Root of source tree to scan for Tier 1 candidates")
    p_build.add_argument("--cards-path", default=str(TERM_CARDS_PATH))
    p_build.add_argument("--no-tier1", action="store_true", help="Skip Tier 1 auto-extraction")
    p_build.add_argument("--dry-run", action="store_true", help="Do not write to disk")

    # validate
    p_val = sub.add_parser("validate", help="Run validation report")
    p_val.add_argument("--cards-path", default=str(TERM_CARDS_PATH))

    # lazy-add
    p_lazy = sub.add_parser("lazy-add", help="Lazy-add a Tier 2 stub for a missing term")
    p_lazy.add_argument("term", help="Term to add")
    p_lazy.add_argument("--cards-path", default=str(TERM_CARDS_PATH))
    p_lazy.add_argument("--source-refs", nargs="*", default=[])

    # report
    p_rep = sub.add_parser("report", help="Print validation report and exit")
    p_rep.add_argument("--cards-path", default=str(TERM_CARDS_PATH))

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    if args.cmd == "build":
        cards = build(
            source_root=Path(args.source_root),
            cards_path=Path(args.cards_path),
            add_tier1=not args.no_tier1,
            dry_run=args.dry_run,
        )
        print(f"\nBuild complete. Total cards: {len(cards)}")
        report = validation_report(cards)
        print("\n" + report)

    elif args.cmd == "validate":
        cards = load_cards(Path(args.cards_path))
        report = validation_report(cards)
        print(report)
        conflicts = detect_alias_conflicts(cards)
        all_errors = [e for card in cards.values() for e in validate_card(card)]
        sys.exit(1 if (all_errors or conflicts) else 0)

    elif args.cmd == "lazy-add":
        cards = load_cards(Path(args.cards_path))
        card = lazy_add(args.term, cards, args.source_refs, Path(args.cards_path))
        print(f"Term-card for '{args.term}':")
        print(json.dumps(card, indent=2))

    elif args.cmd == "report":
        cards = load_cards(Path(args.cards_path))
        print(validation_report(cards))


if __name__ == "__main__":
    _cli()
