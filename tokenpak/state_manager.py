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
    from jsonschema import validate, ValidationError
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False


_SCHEMA_PATH = Path(__file__).parent / "state_schema.json"


class StateManager:
    """
    Manages compact JSON session state for OCP protocol.

    Persists to: .ocp/state/session_<id>.state.json
    Wire format: compact JSON (no whitespace), prefixed with STATE_JSON:
    """

    def __init__(self, session_id: str, base_dir: str = ".ocp"):
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
            "defs": {}
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
    def from_wire(cls, wire_text: str, session_id: str, base_dir: str = ".ocp") -> "StateManager":
        """
        Parse a STATE_JSON wire section back into a StateManager.

        wire_text: full section string, e.g. 'STATE_JSON:\\n{...}'
        """
        lines = wire_text.strip().splitlines()
        json_lines = [l for l in lines if not l.startswith("STATE_JSON")]
        raw = "\n".join(json_lines).strip()
        state = json.loads(raw)

        mgr = cls.__new__(cls)
        mgr.session_id = session_id
        mgr.base_dir = Path(base_dir)
        state_dir = mgr.base_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        mgr.state_path = state_dir / f"session_{session_id}.state.json"
        mgr.state = state
        return mgr

    def __repr__(self) -> str:
        goal_preview = repr(self.state.get("goal", "")[:40])
        return f"<StateManager session={self.session_id!r} goal={goal_preview}>"
