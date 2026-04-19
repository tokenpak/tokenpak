"""
TokenPak Manual Routing Rules
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Allows users to define routing rules that direct specific request patterns
to specific models/providers.

Rules are stored in ~/.tokenpak/routes.yaml and applied by the proxy
before forwarding requests.

Rule format:
    - id: <auto-uuid>
      pattern:
        model: "gpt-4*"          # glob on model name (optional)
        prefix: "Translate"      # prompt must start with this (optional)
        min_tokens: 1000         # estimated input token floor (optional)
        max_tokens: 4000         # estimated input token ceiling (optional)
      target: "anthropic/claude-3-haiku-20240307"
      priority: 10               # lower = higher priority (default 100)
      enabled: true
      created_at: "2026-03-05T22:20:00"
      description: ""
"""

from __future__ import annotations

import fnmatch
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Try PyYAML first, fall back to a minimal JSON-based store if unavailable.
try:
    import yaml as _yaml  # type: ignore

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

import json as _json

# ---------------------------------------------------------------------------
# Default store path
# ---------------------------------------------------------------------------
DEFAULT_ROUTES_PATH = str(Path.home() / ".tokenpak" / "routes.yaml")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RoutePattern:
    """Pattern conditions for a routing rule.

    At least one field must be set.  All set fields must match (AND logic).
    """

    model: Optional[str] = None  # glob, e.g. "gpt-4*" or "openai/*"
    prefix: Optional[str] = None  # prompt must start with this string (case-insensitive)
    min_tokens: Optional[int] = None  # estimated input token floor (inclusive)
    max_tokens: Optional[int] = None  # estimated input token ceiling (inclusive)

    def is_empty(self) -> bool:
        return all(v is None for v in (self.model, self.prefix, self.min_tokens, self.max_tokens))

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.model is not None:
            out["model"] = self.model
        if self.prefix is not None:
            out["prefix"] = self.prefix
        if self.min_tokens is not None:
            out["min_tokens"] = self.min_tokens
        if self.max_tokens is not None:
            out["max_tokens"] = self.max_tokens
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RoutePattern":
        return cls(
            model=d.get("model"),
            prefix=d.get("prefix"),
            min_tokens=d.get("min_tokens"),
            max_tokens=d.get("max_tokens"),
        )


@dataclass
class RouteRule:
    """A single routing rule."""

    id: str
    pattern: RoutePattern
    target: str  # "provider/model" or just "model"
    priority: int = 100  # lower = higher priority
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "pattern": self.pattern.to_dict(),
            "target": self.target,
            "priority": self.priority,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RouteRule":
        pattern_raw = d.get("pattern", {})
        if isinstance(pattern_raw, str):
            # Legacy: bare string treated as model glob
            pattern = RoutePattern(model=pattern_raw)
        else:
            pattern = RoutePattern.from_dict(pattern_raw)
        return cls(
            id=d.get("id", str(uuid.uuid4())[:8]),
            pattern=pattern,
            target=d["target"],
            priority=int(d.get("priority", 100)),
            enabled=bool(d.get("enabled", True)),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            description=d.get("description", ""),
        )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class RouteStore:
    """Persist routing rules to ~/.tokenpak/routes.yaml."""

    def __init__(self, path: str = DEFAULT_ROUTES_PATH):
        self.path = Path(path)

    # ── I/O ─────────────────────────────────────────────────────────────────

    def _load_raw(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        text = self.path.read_text()
        if not text.strip():
            return []
        if _HAS_YAML:
            data = _yaml.safe_load(text)
        else:
            # Fall back to JSON (user may have saved JSON in the .yaml file)
            data = _json.loads(text)
        if not isinstance(data, list):
            return []
        return data

    def _save_raw(self, rules: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if _HAS_YAML:
            text = _yaml.dump(rules, default_flow_style=False, allow_unicode=True, sort_keys=False)
        else:
            text = _json.dumps(rules, indent=2)
        self.path.write_text(text)

    # ── Public API ───────────────────────────────────────────────────────────

    def list(self) -> List[RouteRule]:
        """Return all rules, sorted by priority then created_at."""
        raw = self._load_raw()
        rules = [RouteRule.from_dict(r) for r in raw]
        rules.sort(key=lambda r: (r.priority, r.created_at))
        return rules

    def add(
        self,
        pattern: RoutePattern,
        target: str,
        priority: int = 100,
        description: str = "",
    ) -> RouteRule:
        """Add a new rule and persist it. Returns the created rule."""
        rule = RouteRule(
            id=str(uuid.uuid4())[:8],
            pattern=pattern,
            target=target,
            priority=priority,
            description=description,
        )
        raw = self._load_raw()
        raw.append(rule.to_dict())
        self._save_raw(raw)
        return rule

    def remove(self, rule_id: str) -> bool:
        """Remove rule by id. Returns True if found and removed."""
        raw = self._load_raw()
        new_raw = [r for r in raw if r.get("id") != rule_id]
        if len(new_raw) == len(raw):
            return False
        self._save_raw(new_raw)
        return True

    def get(self, rule_id: str) -> Optional[RouteRule]:
        """Return a single rule by id, or None."""
        for r in self.list():
            if r.id == rule_id:
                return r
        return None

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        """Enable or disable a rule by id. Returns True if found."""
        raw = self._load_raw()
        found = False
        for r in raw:
            if r.get("id") == rule_id:
                r["enabled"] = enabled
                found = True
                break
        if found:
            self._save_raw(raw)
        return found


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------


def _count_tokens_approx(text: str) -> int:
    """Very cheap token estimator: ~4 chars per token."""
    return max(1, len(text) // 4)


def _extract_prompt_text(payload: Dict[str, Any]) -> str:
    """Pull plain text from an OpenAI-style messages payload."""
    messages = payload.get("messages", [])
    parts: List[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n".join(parts)


class RouteEngine:
    """Evaluate routing rules against a request and return the first match."""

    def __init__(self, store: Optional[RouteStore] = None):
        self.store = store or RouteStore()

    def match(
        self,
        *,
        model: str = "",
        prompt: str = "",
        token_count: Optional[int] = None,
        rules: Optional[List[RouteRule]] = None,
    ) -> Optional[RouteRule]:
        """Return the first matching enabled rule (lowest priority wins).

        Args:
            model:       Requested model name (e.g. "gpt-4o", "openai/gpt-4o").
            prompt:      Combined prompt text (all messages flattened).
            token_count: Pre-computed token count; estimated from prompt if None.
            rules:       Override rule list (used in tests). Defaults to store.list().

        Returns:
            Matching RouteRule or None.
        """
        if rules is None:
            rules = self.store.list()
        else:
            # Always evaluate in priority order (lower number = higher priority)
            rules = sorted(rules, key=lambda r: (r.priority, r.created_at))

        if token_count is None and prompt:
            token_count = _count_tokens_approx(prompt)

        for rule in rules:
            if not rule.enabled:
                continue
            if self._matches(rule.pattern, model=model, prompt=prompt, token_count=token_count):
                return rule
        return None

    def match_payload(self, payload: Dict[str, Any]) -> Optional[RouteRule]:
        """Convenience wrapper that accepts a raw OpenAI-style request dict."""
        model = payload.get("model", "")
        prompt = _extract_prompt_text(payload)
        return self.match(model=model, prompt=prompt)

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _matches(
        pattern: RoutePattern,
        *,
        model: str,
        prompt: str,
        token_count: Optional[int],
    ) -> bool:
        """Return True iff ALL non-None pattern fields match."""
        # model glob
        if pattern.model is not None:
            # Support "provider/model" glob matching: try matching both the
            # full "provider/model" string and just the model part.
            pat = pattern.model
            model_short = model.split("/")[-1] if "/" in model else model
            if not (fnmatch.fnmatch(model, pat) or fnmatch.fnmatch(model_short, pat)):
                return False

        # prefix (case-insensitive)
        if pattern.prefix is not None:
            if not prompt.lower().startswith(pattern.prefix.lower()):
                return False

        # token range
        if pattern.min_tokens is not None or pattern.max_tokens is not None:
            tc = token_count if token_count is not None else _count_tokens_approx(prompt)
            if pattern.min_tokens is not None and tc < pattern.min_tokens:
                return False
            if pattern.max_tokens is not None and tc > pattern.max_tokens:
                return False

        return True


# ---------------------------------------------------------------------------
# Helpers for CLI
# ---------------------------------------------------------------------------


def parse_pattern_args(
    model: Optional[str] = None,
    prefix: Optional[str] = None,
    min_tokens: Optional[int] = None,
    max_tokens: Optional[int] = None,
) -> RoutePattern:
    """Build a RoutePattern from CLI args; raises ValueError if all None."""
    pat = RoutePattern(
        model=model,
        prefix=prefix,
        min_tokens=min_tokens,
        max_tokens=max_tokens,
    )
    if pat.is_empty():
        raise ValueError(
            "At least one pattern condition is required: "
            "--model, --prefix, --min-tokens, or --max-tokens"
        )
    return pat
