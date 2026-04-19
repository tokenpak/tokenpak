# SPDX-License-Identifier: Apache-2.0
"""Tiered Fidelity Representations for TokenPak.

Stores the same source at multiple compression levels (L0–L4) and serves
the cheapest sufficient level based on task complexity and remaining budget.

Tier ladder
-----------
L0_RAW        Full source text (no compression)
L1_SIGNATURES Function / class signatures only (names + args, no bodies)
L2_ANNOTATED  Signatures + inline comments + docstrings
L3_CHANGED    Only changed / relevant blocks (requires diff context)
L4_SUMMARY    Compact natural-language summary (key facts only)

Selection policy
----------------
Low complexity  + tight budget  → L4
High complexity + ample budget  → L0
Medium                          → L2 or L3

Usage
-----
>>> from tokenpak.agent.compression.fidelity_tiers import (
...     FidelityTier, TierGenerator, TierSelector, TieredBlock,
... )
>>> block = TierGenerator.generate("def foo(x):\n    '''Docs.'''\n    return x*2")
>>> tier = TierSelector.select(complexity_score=3.0, budget_remaining=0.15)
>>> text = block.get(tier)
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------


class FidelityTier(str, Enum):
    L0_RAW = "raw"          # Full source text
    L1_SIGNATURES = "signatures"  # Function/class signatures only
    L2_ANNOTATED = "annotated"    # Signatures + comments + docstrings
    L3_CHANGED = "changed"        # Only changed/relevant blocks
    L4_SUMMARY = "summary"        # Compact summary (key facts only)

    # Convenience: ordered cheapest → richest
    @classmethod
    def ascending(cls) -> List["FidelityTier"]:
        return [cls.L4_SUMMARY, cls.L3_CHANGED, cls.L2_ANNOTATED,
                cls.L1_SIGNATURES, cls.L0_RAW]

    @classmethod
    def descending(cls) -> List["FidelityTier"]:
        return list(reversed(cls.ascending()))


# Approximate token-cost multipliers (relative to L0)
TIER_COST_FACTOR: Dict[FidelityTier, float] = {
    FidelityTier.L0_RAW:        1.00,
    FidelityTier.L1_SIGNATURES: 0.20,
    FidelityTier.L2_ANNOTATED:  0.40,
    FidelityTier.L3_CHANGED:    0.55,
    FidelityTier.L4_SUMMARY:    0.10,
}


# ---------------------------------------------------------------------------
# TieredBlock — container for all representations of one source block
# ---------------------------------------------------------------------------


@dataclass
class TieredBlock:
    """Holds all fidelity representations for a single source block.

    Parameters
    ----------
    source_id:
        Unique identifier for the source (file path, chunk id, etc.).
    tiers:
        Mapping of FidelityTier → text representation.
    metadata:
        Optional free-form metadata dict (file path, language, etc.).
    """

    source_id: str
    tiers: Dict[FidelityTier, str] = field(default_factory=dict)
    metadata: Dict[str, object] = field(default_factory=dict)

    def get(self, tier: FidelityTier, *, fallback: bool = True) -> str:
        """Return the text for *tier*, falling back to the next richer tier if missing.

        Parameters
        ----------
        tier:
            Desired fidelity tier.
        fallback:
            When ``True`` (default) and *tier* is absent, tries progressively
            richer tiers until one is found. Raises ``KeyError`` only if all
            tiers are absent.
        """
        if tier in self.tiers:
            return self.tiers[tier]
        if not fallback:
            raise KeyError(f"Tier {tier!r} not available for block {self.source_id!r}")
        # Walk up the ladder toward L0_RAW
        for richer in FidelityTier.descending():
            if richer in self.tiers:
                return self.tiers[richer]
        raise KeyError(f"No tiers available for block {self.source_id!r}")

    def available_tiers(self) -> List[FidelityTier]:
        """Return tiers present in this block, sorted cheapest → richest."""
        ordered = FidelityTier.ascending()
        return [t for t in ordered if t in self.tiers]

    def token_estimate(self, tier: FidelityTier) -> int:
        """Rough token estimate for *tier* (4 chars ≈ 1 token)."""
        text = self.get(tier)
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# TierGenerator — builds all tiers from raw source text
# ---------------------------------------------------------------------------


class TierGenerator:
    """Generates all fidelity tiers from a raw source block.

    Supports Python source (AST-based extraction) and plain text
    (regex / heuristic extraction).
    """

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @classmethod
    def generate(
        cls,
        source: str,
        source_id: str = "",
        *,
        changed_lines: Optional[List[int]] = None,
        language: str = "python",
        metadata: Optional[Dict[str, object]] = None,
    ) -> TieredBlock:
        """Generate all tiers for *source* and return a :class:`TieredBlock`.

        Parameters
        ----------
        source:
            Raw source text.
        source_id:
            Identifier for this block (used in the returned TieredBlock).
        changed_lines:
            Line numbers (1-indexed) considered "changed" for L3_CHANGED
            extraction. If ``None``, L3_CHANGED falls back to L2_ANNOTATED.
        language:
            Source language hint. Only ``"python"`` uses AST; others use
            regex heuristics.
        metadata:
            Extra metadata forwarded to the TieredBlock.
        """
        meta = dict(metadata or {})
        meta["language"] = language

        if language == "python":
            tiers = cls._generate_python(source, changed_lines=changed_lines)
        else:
            tiers = cls._generate_text(source, changed_lines=changed_lines)

        return TieredBlock(source_id=source_id, tiers=tiers, metadata=meta)

    # ------------------------------------------------------------------ #
    # Python AST-based extraction
    # ------------------------------------------------------------------ #

    @classmethod
    def _generate_python(
        cls,
        source: str,
        changed_lines: Optional[List[int]] = None,
    ) -> Dict[FidelityTier, str]:
        tiers: Dict[FidelityTier, str] = {}

        # L0 — raw
        tiers[FidelityTier.L0_RAW] = source

        # Parse AST (best-effort; fall back gracefully on syntax errors)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None

        if tree is not None:
            tiers[FidelityTier.L1_SIGNATURES] = cls._extract_signatures(source, tree)
            tiers[FidelityTier.L2_ANNOTATED] = cls._extract_annotated(source, tree)
        else:
            # Fall back to regex for broken/partial Python
            tiers[FidelityTier.L1_SIGNATURES] = cls._regex_signatures(source)
            tiers[FidelityTier.L2_ANNOTATED] = tiers[FidelityTier.L1_SIGNATURES]

        # L3 — changed blocks
        if changed_lines:
            tiers[FidelityTier.L3_CHANGED] = cls._extract_changed(
                source, changed_lines
            )
        else:
            tiers[FidelityTier.L3_CHANGED] = tiers[FidelityTier.L2_ANNOTATED]

        # L4 — summary
        tiers[FidelityTier.L4_SUMMARY] = cls._summarize_python(source, tree)

        return tiers

    @staticmethod
    def _extract_signatures(source: str, tree: ast.AST) -> str:
        """Return function / class header lines only (no bodies)."""
        lines = source.splitlines()
        sig_lines: List[str] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Grab just the def/class line(s) up to the colon
                lineno = node.lineno - 1
                end_lineno = getattr(node, "end_lineno", lineno)
                # Find the line that ends with ':'
                collected: List[str] = []
                for i in range(lineno, min(end_lineno, lineno + 10)):
                    collected.append(lines[i])
                    if lines[i].rstrip().endswith(":"):
                        break
                sig_lines.extend(collected)
                sig_lines.append("")  # blank separator

        return "\n".join(sig_lines).strip()

    @staticmethod
    def _extract_annotated(source: str, tree: ast.AST) -> str:
        """Return signatures plus inline comments and docstrings."""
        lines = source.splitlines()
        keep: set[int] = set()

        for node in ast.walk(tree):
            # Signature lines
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lineno = node.lineno - 1
                end_lineno = getattr(node, "end_lineno", lineno)
                # Add signature lines
                for i in range(lineno, min(end_lineno, lineno + 10)):
                    keep.add(i)
                    if lines[i].rstrip().endswith(":"):
                        break
                # Add docstring (first Expr/Constant child)
                for child in ast.iter_child_nodes(node):
                    if (
                        isinstance(child, ast.Expr)
                        and isinstance(getattr(child, "value", None), ast.Constant)
                        and isinstance(child.value.value, str)
                    ):
                        doc_start = child.lineno - 1
                        doc_end = getattr(child, "end_lineno", doc_start)
                        for i in range(doc_start, doc_end + 1):
                            keep.add(i)
                        break

        # Always include comment lines (#)
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                keep.add(i)

        result_lines = [lines[i] for i in sorted(keep)]
        return "\n".join(result_lines).strip()

    @staticmethod
    def _extract_changed(source: str, changed_lines: List[int]) -> str:
        """Return lines within ±3 context lines of any changed line."""
        lines = source.splitlines()
        total = len(lines)
        context = 3
        keep: set[int] = set()

        for ln in changed_lines:
            idx = ln - 1  # 0-indexed
            for i in range(max(0, idx - context), min(total, idx + context + 1)):
                keep.add(i)

        result_lines = [lines[i] for i in sorted(keep)]
        return "\n".join(result_lines).strip()

    @staticmethod
    def _summarize_python(source: str, tree: Optional[ast.AST]) -> str:
        """One-line-per-symbol summary of the source."""
        parts: List[str] = []

        if tree is None:
            # Fallback: count lines
            n = len(source.splitlines())
            return f"[Python source: ~{n} lines, parse failed]"

        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        funcs = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        imports = [
            n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        ]

        if classes:
            names = ", ".join(c.name for c in classes[:5])
            parts.append(f"Classes: {names}")
        if funcs:
            names = ", ".join(f.name for f in funcs[:8])
            parts.append(f"Functions: {names}")
        if imports:
            parts.append(f"Imports: {len(imports)} statements")

        lines = source.splitlines()
        parts.append(f"Size: {len(lines)} lines")

        return " | ".join(parts) if parts else f"[Python source: {len(lines)} lines]"

    # ------------------------------------------------------------------ #
    # Regex / plain-text heuristic extraction
    # ------------------------------------------------------------------ #

    @classmethod
    def _generate_text(
        cls,
        source: str,
        changed_lines: Optional[List[int]] = None,
    ) -> Dict[FidelityTier, str]:
        tiers: Dict[FidelityTier, str] = {}
        tiers[FidelityTier.L0_RAW] = source
        tiers[FidelityTier.L1_SIGNATURES] = cls._regex_signatures(source)
        tiers[FidelityTier.L2_ANNOTATED] = tiers[FidelityTier.L1_SIGNATURES]

        if changed_lines:
            tiers[FidelityTier.L3_CHANGED] = cls._extract_changed(
                source, changed_lines
            )
        else:
            tiers[FidelityTier.L3_CHANGED] = tiers[FidelityTier.L2_ANNOTATED]

        tiers[FidelityTier.L4_SUMMARY] = cls._summarize_text(source)
        return tiers

    @staticmethod
    def _regex_signatures(source: str) -> str:
        """Extract def / class lines via regex (language-agnostic fallback)."""
        pattern = re.compile(
            r"^[ \t]*((?:async\s+)?def\s+\w+[^:]*:|class\s+\w+[^:]*:)", re.MULTILINE
        )
        sigs = pattern.findall(source)
        return "\n".join(sigs) if sigs else source[:200]

    @staticmethod
    def _summarize_text(source: str) -> str:
        """First 3 non-empty lines + line count."""
        lines = [ln for ln in source.splitlines() if ln.strip()]
        preview = " / ".join(lines[:3])
        preview = textwrap.shorten(preview, width=200)
        return f"{preview} [{len(source.splitlines())} lines]"


# ---------------------------------------------------------------------------
# TierSelector — auto-selects the optimal tier
# ---------------------------------------------------------------------------


class TierSelector:
    """Selects the cheapest sufficient fidelity tier.

    Selection matrix
    ----------------
    complexity_score : float
        0.0–10.0 scale (compatible with ``tokenpak.complexity`` scorer).
    budget_remaining : float
        Fraction of token budget still available (0.0 = exhausted, 1.0 = full).
    relevance_score : float, optional
        0.0–1.0 relevance of this block to the current task.

    Policy
    ------
    - budget_remaining < 0.10 → always L4 (emergency)
    - complexity ≥ 7.0 and budget ≥ 0.5 → L0
    - complexity ≥ 7.0 and budget ≥ 0.25 → L1
    - complexity ≥ 4.0 and budget ≥ 0.4 → L2
    - complexity ≥ 4.0                   → L3
    - else                               → L4
    """

    @staticmethod
    def select(
        complexity_score: float,
        budget_remaining: float,
        relevance_score: float = 1.0,
    ) -> FidelityTier:
        """Return the recommended :class:`FidelityTier`.

        Parameters
        ----------
        complexity_score:
            Task complexity on a 0.0–10.0 scale.
        budget_remaining:
            Fraction of token budget remaining (0.0–1.0).
        relevance_score:
            How relevant this block is to the current task (0.0–1.0).
            Low-relevance blocks are downgraded one tier.
        """
        # Emergency: budget nearly exhausted
        if budget_remaining < 0.10:
            return FidelityTier.L4_SUMMARY

        # Adjust effective complexity by relevance
        effective_complexity = complexity_score * max(0.1, relevance_score)

        if effective_complexity >= 7.0:
            if budget_remaining >= 0.5:
                tier = FidelityTier.L0_RAW
            elif budget_remaining >= 0.25:
                tier = FidelityTier.L1_SIGNATURES
            else:
                tier = FidelityTier.L2_ANNOTATED
        elif effective_complexity >= 4.0:
            if budget_remaining >= 0.4:
                tier = FidelityTier.L2_ANNOTATED
            else:
                tier = FidelityTier.L3_CHANGED
        else:
            tier = FidelityTier.L4_SUMMARY

        return tier

    @staticmethod
    def select_for_block(
        block: TieredBlock,
        complexity_score: float,
        budget_remaining: float,
        relevance_score: float = 1.0,
    ) -> str:
        """Select the best available tier for *block* and return its text.

        Falls back to the nearest richer tier if the recommended tier is not
        stored in the block.
        """
        recommended = TierSelector.select(
            complexity_score, budget_remaining, relevance_score
        )
        return block.get(recommended, fallback=True)


# ---------------------------------------------------------------------------
# TierStore — in-memory registry of TieredBlocks
# ---------------------------------------------------------------------------


class TierStore:
    """In-memory store for indexed :class:`TieredBlock` objects.

    In production this would persist to disk / a database.  For now it acts
    as a lightweight dict-backed registry used during a session.
    """

    def __init__(self) -> None:
        self._store: Dict[str, TieredBlock] = {}

    # ------------------------------------------------------------------ #
    # Indexing
    # ------------------------------------------------------------------ #

    def index(self, block: TieredBlock) -> None:
        """Add or replace a TieredBlock in the store."""
        self._store[block.source_id] = block

    def index_source(
        self,
        source: str,
        source_id: str,
        *,
        changed_lines: Optional[List[int]] = None,
        language: str = "python",
        metadata: Optional[Dict[str, object]] = None,
    ) -> TieredBlock:
        """Generate tiers from *source* and index the resulting block.

        Returns the newly created :class:`TieredBlock`.
        """
        block = TierGenerator.generate(
            source,
            source_id=source_id,
            changed_lines=changed_lines,
            language=language,
            metadata=metadata,
        )
        self.index(block)
        return block

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #

    def get(self, source_id: str) -> Optional[TieredBlock]:
        """Return the :class:`TieredBlock` for *source_id*, or ``None``."""
        return self._store.get(source_id)

    def fetch(
        self,
        source_id: str,
        complexity_score: float,
        budget_remaining: float,
        relevance_score: float = 1.0,
    ) -> Optional[str]:
        """Return the cheapest-sufficient text for *source_id*, or ``None`` if unknown."""
        block = self.get(source_id)
        if block is None:
            return None
        return TierSelector.select_for_block(
            block, complexity_score, budget_remaining, relevance_score
        )

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, source_id: str) -> bool:
        return source_id in self._store

    def ids(self) -> List[str]:
        return list(self._store.keys())
