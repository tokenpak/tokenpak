"""Intent-specific state schemas for TokenPak.

Each intent maintains a separate compact state blob with only the fields
relevant to that intent's context requirements.

Available schemas:
  - debug_state.json     — error, affected_files, changed_files, failing_tests, recent_deploy
  - writing_state.json   — audience, tone, cta, brand_constraints, source_points
  - planning_state.json  — objective, constraints, options, blockers, deadline
  - ops_state.json       — service_status, recent_changes, health_checks, env_drift
  - extraction_state.json — schema, source_type, output_format
"""

from pathlib import Path

SCHEMAS_DIR = Path(__file__).parent

# Map of intent → schema filename
INTENT_SCHEMA_MAP: dict[str, str] = {
    "debug": "debug_state.json",
    "create": "writing_state.json",
    "plan": "planning_state.json",
    "execute": "ops_state.json",
    "query": "extraction_state.json",
    "search": "extraction_state.json",
}


def get_schema_path(intent: str) -> Path | None:
    """Return the Path to the JSON schema file for a given intent, or None."""
    filename = INTENT_SCHEMA_MAP.get(intent)
    if filename is None:
        return None
    return SCHEMAS_DIR / filename


__all__ = ["SCHEMAS_DIR", "INTENT_SCHEMA_MAP", "get_schema_path"]
