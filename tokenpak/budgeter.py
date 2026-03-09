# SPDX-License-Identifier: MIT
"""Budgeter — token allocation and trim policy for OCP Phase 2.

Enforces a hard token budget across context buckets:
  STATE_JSON  8-15%   critical
  RECENT      10-20%  high
  EVIDENCE    50-70%  medium
  TOOLS       0-25%   variable

Trim order (lower priority trimmed first):
  1. older_history       — keep last N turns, drop earlier
  2. low_score_evidence  — drop bottom 20% by score
  3. verbose_evidence    — shorten spans to max_tokens
  4. nonessential_skills — replace tool schemas with refs
  5. prose_background    — compress/summarize background

Usage:
    budgeter = Budgeter()  # loads budget_config.yaml if available
    trimmed = budgeter.allocate(components)

    # components: {
    #   'state':    {'text': '...', 'priority': 'critical'},
    #   'recent':   {'text': '...', 'priority': 'high'},
    #   'evidence': {'items': [...], 'priority': 'medium'},
    #   'tools':    {'text': '...', 'priority': 'variable'},
    # }
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tiktoken

    _enc = tiktoken.encoding_for_model("gpt-4")

    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))

    def _truncate_tokens(text: str, max_tokens: int) -> str:
        tokens = _enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return _enc.decode(tokens[:max_tokens]) + "..."

except ImportError:

    def _count_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    def _truncate_tokens(text: str, max_tokens: int) -> str:
        approx_chars = max_tokens * 4
        return text[:approx_chars] + ("..." if len(text) > approx_chars else "")


# Default config (used when budget_config.yaml is unavailable)
_DEFAULT_CONFIG = {
    "total_tokens": 12000,
    "buckets": {
        "state": {"min_pct": 8, "max_pct": 15, "priority": "critical"},
        "recent": {"min_pct": 10, "max_pct": 20, "priority": "high"},
        "evidence": {"min_pct": 50, "max_pct": 70, "priority": "medium"},
        "tools": {"min_pct": 0, "max_pct": 25, "priority": "variable"},
    },
    "trim_order": [
        "older_history",
        "low_score_evidence",
        "verbose_evidence",
        "nonessential_skills",
        "prose_background",
    ],
    "thresholds": {
        "evidence_keep_pct": 0.80,
        "evidence_max_span_tokens": 30,
        "recent_keep_turns": 5,
    },
}


def _load_config(config_path: str) -> Dict[str, Any]:
    """Load budget config. Supports YAML (if pyyaml installed) or JSON."""
    path = Path(config_path)
    if not path.exists():
        return _DEFAULT_CONFIG

    text = path.read_text(encoding="utf-8")

    # Try JSON first (faster, no dep)
    try:
        from typing import cast as _cast_t
        return _cast_t(Dict[str, Any], json.loads(text))
    except json.JSONDecodeError:
        pass

    # Try YAML if pyyaml is available
    try:
        import yaml
        from typing import cast as _cast_t
        return _cast_t(Dict[str, Any], yaml.safe_load(text))
    except ImportError:
        pass

    # Parse simple YAML manually for key: value and list items
    return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """
    Minimal YAML parser for our specific config shape.
    Handles:
      - top-level key: value (int, float, str)
      - nested key:\n  sub_key: value
      - list items: - item
    Falls back to default config on parse error.
    """
    try:
        result = {}
        current_section = None
        current_sub = None
        lines = text.splitlines()

        for raw_line in lines:
            line = raw_line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())
            stripped = line.strip()

            if stripped.startswith("- "):
                # List item (strip inline comment)
                value = stripped[2:].strip()
                if "  #" in value:
                    value = value[: value.index("  #")].strip()
                if current_section and current_sub and indent >= 4:
                    section = result.setdefault(current_section, {})
                    sub = section.setdefault(current_sub, [])
                    if isinstance(sub, list):
                        sub.append(value)
                elif current_section and indent == 2:
                    target = result.setdefault(current_section, [])
                    if isinstance(target, list):
                        target.append(value)
                continue

            if ":" not in stripped:
                continue

            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            # Strip inline comments (  # comment)
            if "  #" in val:
                val = val[: val.index("  #")].strip()

            if indent == 0:
                if not val:
                    current_section = key
                    current_sub = None
                else:
                    result[key] = _cast(val)
                    current_section = None
            elif indent == 2:
                if not val:
                    current_sub = key
                    if current_section:
                        result.setdefault(current_section, {})
                else:
                    if current_section:
                        result.setdefault(current_section, {})[key] = _cast(val)
            elif indent >= 4:
                if current_section and current_sub:
                    sec = result.setdefault(current_section, {})
                    sub = sec.setdefault(current_sub, {})
                    if isinstance(sub, dict):
                        sub[key] = _cast(val)

        return result if result else _DEFAULT_CONFIG
    except Exception:
        return _DEFAULT_CONFIG


def _cast(val: str) -> Any:
    """Cast string to int, float, or string."""
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


class Budgeter:
    """
    Token budget allocator and trim controller.

    Hard limit: components will be trimmed until total_tokens is met.
    Never trims: STATE_JSON, output contract, CANON refs, current turn.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        if config_path is None:
            # Look for budget_config.yaml in same dir as this file
            default = Path(__file__).parent / "budget_config.yaml"
            config_path = str(default)

        self.config: Dict[str, Any] = _load_config(config_path)
        self.total_tokens: int = int(self.config.get("total_tokens", 12000))
        self.trim_order: List[str] = self.config.get("trim_order", _DEFAULT_CONFIG["trim_order"])
        self.thresholds: Dict[str, Any] = self.config.get("thresholds", _DEFAULT_CONFIG["thresholds"])

    # ── Token counting ───────────────────────────────────────────────────────

    def _component_tokens(self, comp: Dict[str, Any]) -> int:
        """Count tokens in a component dict."""
        if "items" in comp:
            # Evidence bucket: list of EvidenceItem-like objects
            return sum(
                _count_tokens(ev.text if hasattr(ev, "text") else ev.get("text", ""))
                for ev in comp["items"]
            )
        return _count_tokens(comp.get("text", ""))

    def _total_used(self, components: Dict[str, Any]) -> int:
        return sum(self._component_tokens(c) for c in components.values())

    # ── Trim actions ─────────────────────────────────────────────────────────

    def _trim_history(self, components: Dict[str, Any]) -> Dict[str, Any]:
        """Keep last N recent turns, drop earlier."""
        keep_turns = int(self.thresholds.get("recent_keep_turns", 5))
        recent_comp = components.get("recent")
        if not recent_comp:
            return components

        text = recent_comp.get("text", "")
        # Assume turns separated by double-newline
        turns = [t for t in text.split("\n\n") if t.strip()]
        if len(turns) > keep_turns:
            kept = turns[-keep_turns:]
            components["recent"] = {**recent_comp, "text": "\n\n".join(kept)}
        return components

    def _trim_evidence_by_score(self, components: Dict[str, Any]) -> Dict[str, Any]:
        """Drop lowest-scoring evidence items (keep top keep_pct)."""
        evidence = components.get("evidence")
        if not evidence or not evidence.get("items"):
            return components

        keep_pct = float(self.thresholds.get("evidence_keep_pct", 0.80))
        items = list(evidence["items"])
        items.sort(
            key=lambda x: x.score if hasattr(x, "score") else x.get("score", 0), reverse=True
        )
        keep_count = max(1, int(len(items) * keep_pct))
        components["evidence"] = {**evidence, "items": items[:keep_count]}
        return components

    def _trim_evidence_verbosity(self, components: Dict[str, Any]) -> Dict[str, Any]:
        """Truncate each evidence span to max_span_tokens."""
        evidence = components.get("evidence")
        if not evidence or not evidence.get("items"):
            return components

        max_span = int(self.thresholds.get("evidence_max_span_tokens", 30))
        new_items: List[Any] = []
        for ev in evidence["items"]:
            if hasattr(ev, "text"):
                # EvidenceItem object — create a copy-like proxy
                from .evidence_pack import EvidenceItem

                new_ev = EvidenceItem(
                    src=ev.src,
                    ref=ev.ref,
                    span=ev.span,
                    score=ev.score,
                    text=_truncate_tokens(ev.text, max_span),
                )
            else:
                # Plain dict
                new_ev_dict: Dict[str, Any] = {**ev, "text": _truncate_tokens(ev.get("text", ""), max_span)}
                new_items.append(new_ev_dict)
                continue
            new_items.append(new_ev)

        components["evidence"] = {**evidence, "items": new_items}
        return components

    def _trim_skills(self, components: Dict[str, Any]) -> Dict[str, Any]:
        """Replace tool/skill schemas with ref-only placeholders."""
        tools = components.get("tools")
        if not tools:
            return components

        text = tools.get("text", "")
        if not text:
            return components

        # Replace verbose JSON schemas with a one-liner stub
        # Heuristic: schemas contain { and are > 200 tokens
        if _count_tokens(text) > 200:
            components["tools"] = {
                **tools,
                "text": "[tool schemas omitted — send ref only; request full schemas if needed]",
            }
        return components

    def _trim_prose_background(self, components: Dict[str, Any]) -> Dict[str, Any]:
        """Truncate the recent/evidence sections further as a last resort."""
        for key in ("recent", "evidence"):
            comp = components.get(key)
            if not comp:
                continue
            if key == "recent":
                text = comp.get("text", "")
                # Halve it
                tokens = _count_tokens(text)
                components[key] = {**comp, "text": _truncate_tokens(text, tokens // 2)}
        return components

    # ── Main allocate API ────────────────────────────────────────────────────

    def allocate(self, components: Dict[str, Any]) -> Dict[str, Any]:
        """
        Allocate token budget across components, trimming as needed.

        Args:
            components: {
                'state':    {'text': ..., 'priority': 'critical'},
                'recent':   {'text': ..., 'priority': 'high'},
                'evidence': {'items': [...], 'priority': 'medium'},
                'tools':    {'text': ..., 'priority': 'variable'},
            }

        Returns:
            Trimmed components that fit within total_tokens budget.
        """
        # Deep copy to avoid mutating caller's data
        components = copy.deepcopy(components)
        total_used = self._total_used(components)

        if total_used <= self.total_tokens:
            return components  # Already within budget

        print(
            f"[Budgeter] Over budget: {total_used}/{self.total_tokens} tokens. "
            f"Applying trim policy..."
        )

        trim_actions = {
            "older_history": self._trim_history,
            "low_score_evidence": self._trim_evidence_by_score,
            "verbose_evidence": self._trim_evidence_verbosity,
            "nonessential_skills": self._trim_skills,
            "prose_background": self._trim_prose_background,
        }

        for action_name in self.trim_order:
            if total_used <= self.total_tokens:
                break

            action = trim_actions.get(action_name)
            if action:
                before = total_used
                components = action(components)
                total_used = self._total_used(components)
                saved = before - total_used
                if saved > 0:
                    print(
                        f"[Budgeter]   {action_name}: saved {saved} tokens "
                        f"(now {total_used}/{self.total_tokens})"
                    )

        if total_used > self.total_tokens:
            print(
                f"[Budgeter] WARNING: Still over budget after all trim actions "
                f"({total_used}/{self.total_tokens}). Hard truncation on evidence."
            )
            # Emergency: drop evidence entirely except top 1 item
            evidence = components.get("evidence")
            if evidence and evidence.get("items"):
                items = evidence["items"]
                items.sort(
                    key=lambda x: x.score if hasattr(x, "score") else x.get("score", 0),
                    reverse=True,
                )
                components["evidence"] = {**evidence, "items": items[:1]}

        return components

    # ── Stats ────────────────────────────────────────────────────────────────

    def budget_report(self, components: Dict[str, Any]) -> Dict[str, Any]:
        """Return token usage per bucket."""
        report = {}
        total = 0
        for key, comp in components.items():
            used = self._component_tokens(comp)
            total += used
            report[key] = {
                "tokens": used,
                "pct": round(used / self.total_tokens * 100, 1) if self.total_tokens else 0,
            }
        report["_total"] = {"tokens": total, "budget": self.total_tokens}
        return report

    def __repr__(self) -> str:
        return f"<Budgeter total_tokens={self.total_tokens}>"
