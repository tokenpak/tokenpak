# SPDX-License-Identifier: Apache-2.0
"""StateManager — compact session state that persists across turns.

STATE_JSON keeps current task context in a concise, patchable format.
Wire budget: 8-15% of total context. Keep under 2K tokens.

Wire format in request payload:
  STATE_JSON:
  {"goal":"...","constraints":[...],"current_task":"...","done":[...],"open":[...],"next":[...],"defs":{}}
"""

import json
from pathlib import Path
from typing import Any

try:
    from jsonschema import ValidationError, validate  # noqa: F401

    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False


_SCHEMA_PATH = Path(__file__).parent / "state_schema.json"


class StateManager:
    """
    Manages compact JSON session state for TokenPak Protocol.

    Persists to: .tokenpak/state/session_<id>.state.json
    Wire format: compact JSON (no whitespace), prefixed with STATE_JSON:
    """

    def __init__(self, session_id: str, base_dir: str = ".tokenpak"):
        self.session_id = session_id
        self.base_dir = Path(base_dir)
        state_dir = self.base_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = state_dir / f"session_{session_id}.state.json"
        self.state = self.load()

    # ── Init / Load ──────────────────────────────────────────────────────────

    def load(self) -> dict:
        """Load state from disk, or initialize empty state."""
        if self.state_path.exists():
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass  # Corrupt/missing — fall through to init
        return self._init_state()

    def _init_state(self) -> dict:
        return {
            "goal": "",
            "constraints": [],
            "current_task": "",
            "done": [],
            "open": [],
            "next": [],
            "defs": {},
        }

    # ── Validation ───────────────────────────────────────────────────────────

    def validate(self) -> None:
        """Validate state against schema. Raises ValidationError on failure."""
        if not _HAS_JSONSCHEMA:
            # Manual required-field check if jsonschema not installed
            for field in ("goal", "current_task"):
                if field not in self.state:
                    raise ValueError(f"STATE_JSON missing required field: {field}")
            return

        if _SCHEMA_PATH.exists():
            with open(_SCHEMA_PATH, encoding="utf-8") as f:
                schema = json.load(f)
            validate(instance=self.state, schema=schema)

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> None:
        """Validate then persist state to disk."""
        self.validate()
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    # ── Mutation helpers ─────────────────────────────────────────────────────

    def set_goal(self, goal: str) -> None:
        self.state["goal"] = goal

    def set_current_task(self, task: str) -> None:
        self.state["current_task"] = task

    def mark_done(self, item: str) -> None:
        """Move item from open → done (if present), or just append to done."""
        if item in self.state.get("open", []):
            self.state["open"].remove(item)
        if item not in self.state.get("done", []):
            self.state.setdefault("done", []).append(item)

    def add_open(self, item: str) -> None:
        self.state.setdefault("open", []).append(item)

    def add_next(self, item: str) -> None:
        self.state.setdefault("next", []).append(item)

    def add_constraint(self, constraint: str) -> None:
        self.state.setdefault("constraints", []).append(constraint)

    def set_def(self, key: str, value: Any) -> None:
        self.state.setdefault("defs", {})[key] = value

    def apply_patch(self, patch: dict) -> None:
        """
        Apply a Phase 3 patch operation.

        Supported ops:
          {"op": "ADD",    "path": "done",         "value": "item"}
          {"op": "REMOVE", "path": "open",          "value": "item"}
          {"op": "SET",    "path": "current_task",  "value": "..."}
          {"op": "SET",    "path": "goal",          "value": "..."}
        """
        op = patch.get("op", "").upper()
        path = patch.get("path", "")
        value = patch.get("value")

        if op == "ADD":
            target = self.state.get(path, [])
            if isinstance(target, list) and value not in target:
                target.append(value)
                self.state[path] = target
        elif op == "REMOVE":
            target = self.state.get(path, [])
            if isinstance(target, list) and value in target:
                target.remove(value)
                self.state[path] = target
        elif op == "SET":
            self.state[path] = value
        else:
            raise ValueError(f"Unknown patch op: {op!r}")

    # ── Wire format ──────────────────────────────────────────────────────────

    def to_wire_format(self) -> str:
        """Compact JSON for LLM payload (no whitespace)."""
        return json.dumps(self.state, separators=(",", ":"))

    def to_wire_section(self) -> str:
        """Full STATE_JSON section ready to embed in request payload."""
        return f"STATE_JSON:\n{self.to_wire_format()}"

    # ── Round-trip ───────────────────────────────────────────────────────────

    @classmethod
    def from_wire(
        cls, wire_text: str, session_id: str, base_dir: str = ".tokenpak"
    ) -> "StateManager":
        """
        Parse a STATE_JSON wire section back into a StateManager.

        wire_text: full section string, e.g. 'STATE_JSON:\\n{...}'
        """
        lines = wire_text.strip().splitlines()
        json_text = "\n".join(lines[1:]) if len(lines) > 1 else "{}"
        state_dict = json.loads(json_text)
        sm = cls(session_id, base_dir)
        sm.state = state_dict
        return sm


# ---------------------------------------------------------------------------
# Multi-schema support — intent-specific state
# ---------------------------------------------------------------------------

import copy as _copy

try:
    from jsonschema import ValidationError as _ValidationError  # noqa: F401, F811
    from jsonschema import validate as _validate

    _HAS_JSONSCHEMA_MS = True
except ImportError:
    _HAS_JSONSCHEMA_MS = False

# Lazy import to avoid circular dependencies
_SCHEMAS_DIR_CACHE = None


def _get_schemas_dir():
    global _SCHEMAS_DIR_CACHE
    if _SCHEMAS_DIR_CACHE is None:
        _SCHEMAS_DIR_CACHE = Path(__file__).parent / "agent" / "state_schemas"
    return _SCHEMAS_DIR_CACHE


class IntentStateManager:
    """
    Intent-specific state manager.

    Maintains a separate compact state blob per intent, injecting only
    the fields relevant to that intent into the LLM context.

    Persists to: .tokenpak/state/session_<id>.<intent>.state.json
    Wire format: compact JSON, prefixed with STATE_JSON[<intent>]:
    """

    # Intent → default empty state initializer
    _DEFAULTS = {
        "debug": {
            "error": "",
            "affected_files": [],
            "changed_files": [],
            "failing_tests": [],
            "recent_deploy": "",
        },
        "create": {
            "audience": "",
            "tone": "",
            "cta": "",
            "brand_constraints": [],
            "source_points": [],
        },
        "plan": {
            "objective": "",
            "constraints": [],
            "options": [],
            "blockers": [],
            "deadline": "",
        },
        "execute": {
            "service_status": {},
            "recent_changes": [],
            "health_checks": [],
            "env_drift": [],
        },
        "query": {
            "schema": {},
            "source_type": "",
            "output_format": "json",
        },
        "search": {
            "schema": {},
            "source_type": "",
            "output_format": "json",
        },
    }

    def __init__(self, session_id, intent, base_dir=".tokenpak"):
        self.session_id = session_id
        self.intent = intent
        self.base_dir = Path(base_dir)
        state_dir = self.base_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = state_dir / f"session_{session_id}.{intent}.state.json"
        self._schema = self._load_schema()
        self.state = self.load()

    # ── Schema ───────────────────────────────────────────────────────────────

    def _load_schema(self):
        """Load the JSON schema file for this intent (if it exists)."""
        schemas_dir = _get_schemas_dir()
        try:
            from tokenpak.agent.state_schemas import INTENT_SCHEMA_MAP

            filename = INTENT_SCHEMA_MAP.get(self.intent)
            if filename:
                schema_path = schemas_dir / filename
                if schema_path.exists():
                    with open(schema_path, encoding="utf-8") as f:
                        return json.load(f)
        except ImportError:
            pass
        return None

    # ── Init / Load ──────────────────────────────────────────────────────────

    def load(self):
        """Load persisted state or initialize from defaults."""
        if self.state_path.exists():
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return self._init_state()

    def _init_state(self):
        defaults = self._DEFAULTS.get(self.intent, {})
        return _copy.deepcopy(defaults)

    # ── Validation ───────────────────────────────────────────────────────────

    def validate(self):
        """Validate intent state against its JSON schema."""
        if not _HAS_JSONSCHEMA_MS or self._schema is None:
            return
        _validate(instance=self.state, schema=self._schema)

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self):
        """Validate and persist state."""
        self.validate()
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    # ── Mutation ─────────────────────────────────────────────────────────────

    def set(self, key, value):
        """Set a field in the current intent state."""
        self.state[key] = value

    def get(self, key, default=None):
        """Get a field from the current intent state."""
        return self.state.get(key, default)

    def update(self, patch):
        """Shallow-merge a dict of updates into state."""
        self.state.update(patch)

    # ── Wire format ──────────────────────────────────────────────────────────

    def to_wire_format(self):
        """Compact JSON (no whitespace) — only fields relevant to this intent."""
        return json.dumps(self.state, separators=(",", ":"))

    def to_wire_section(self):
        """Full STATE_JSON section tagged with intent."""
        return f"STATE_JSON[{self.intent}]:\n{self.to_wire_format()}"

    def __repr__(self):
        return f"<IntentStateManager session={self.session_id!r} intent={self.intent!r}>"


class MultiSchemaStateManager:
    """
    Facade that manages multiple IntentStateManagers for a session.

    Usage::

        mgr = MultiSchemaStateManager("sess-abc123")
        mgr.for_intent("debug").set("error", "NullPointerException in auth.py")
        mgr.for_intent("plan").set("objective", "migrate database to Postgres")
        wire = mgr.build_wire_section("debug")  # only injects debug fields
    """

    def __init__(self, session_id, base_dir=".tokenpak"):
        self.session_id = session_id
        self.base_dir = base_dir
        self._managers = {}

    def for_intent(self, intent):
        """Get or create the IntentStateManager for a given intent."""
        if intent not in self._managers:
            self._managers[intent] = IntentStateManager(self.session_id, intent, self.base_dir)
        return self._managers[intent]

    def save_all(self):
        """Persist all active intent states."""
        for mgr in self._managers.values():
            mgr.save()

    def build_wire_section(self, intent):
        """
        Build the STATE_JSON wire section for the given intent.

        Only includes fields relevant to that intent — no cross-contamination.
        """
        return self.for_intent(intent).to_wire_section()

    def active_intents(self):
        """Return the list of intents with active state managers."""
        return list(self._managers.keys())

    def __repr__(self):
        return (
            f"<MultiSchemaStateManager session={self.session_id!r} active={self.active_intents()}>"
        )


def select_state_manager(session_id, intent, base_dir=".tokenpak"):
    """
    Factory: select and return the appropriate IntentStateManager for a classified intent.

    This is the primary auto-selection entry point used by the proxy router:

        mgr = select_state_manager(session_id, classified_intent)
        # mgr already has the correct schema + default state for that intent
    """
    return IntentStateManager(session_id, intent, base_dir)
