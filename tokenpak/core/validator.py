# SPDX-License-Identifier: Apache-2.0
"""TokenPak Protocol v1.0 Validator.

Validates TokenPak JSON files against the v1.0 schema and protocol rules.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

# ── Schema location ──────────────────────────────────────────────────────────

_SCHEMA_DIR = Path(__file__).parent.parent / "schemas"
_SCHEMA_PATH = _SCHEMA_DIR / "tokenpak-v1.0.json"


# ── Result types ─────────────────────────────────────────────────────────────


class ValidationIssue:
    """A single validation error or warning."""

    def __init__(self, level: str, field: str, message: str) -> None:
        self.level = level  # "error" | "warning" | "info"
        self.field = field  # JSON path, e.g. "header.version"
        self.message = message

    def __str__(self) -> str:
        icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(self.level, "?")
        return f"  {icon} [{self.field}] {self.message}"

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "field": self.field, "message": self.message}


class ValidationResult:
    """Complete result of a pack validation."""

    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []
        self._valid: bool | None = None

    def error(self, field: str, message: str) -> None:
        self.issues.append(ValidationIssue("error", field, message))

    def warning(self, field: str, message: str) -> None:
        self.issues.append(ValidationIssue("warning", field, message))

    def info(self, field: str, message: str) -> None:
        self.issues.append(ValidationIssue("info", field, message))

    @property
    def valid(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]

    def summary(self) -> str:
        e = len(self.errors)
        w = len(self.warnings)
        status = "✓ VALID" if self.valid else "✗ INVALID"
        return f"{status} — {e} error(s), {w} warning(s)"

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
        }


# ── Validator ─────────────────────────────────────────────────────────────────


class TokenPakValidator:
    """Validates TokenPak packs against the v1.0 protocol spec."""

    SUPPORTED_VERSIONS = {"1.0"}
    BLOCK_TYPES = {
        "instructions",
        "code",
        "knowledge",
        "memory",
        "conversation",
        "evidence",
        "system",
    }
    PRIORITY_VALUES = {"critical", "high", "medium", "low", "internal"}
    TRUST_LEVELS = {"verified", "unverified", "generated"}
    TRANSFORM_TYPES = {"merge", "compact", "filter", "enrich", "sign"}
    COMPACTION_MODES = {"lossless", "balanced", "aggressive", "semantic"}
    WORKFLOW_STATUSES = {"not_started", "in_progress", "done", "failed"}
    BLOCK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")

    def validate(self, pack: Mapping[str, object], verbose: bool = False) -> ValidationResult:
        """Validate a parsed pack dict. Returns a ValidationResult."""
        result = ValidationResult()
        self._check_header(pack, result)
        self._check_metadata(pack, result)
        self._check_blocks(pack, result)
        if "capabilities" in pack:
            self._check_capabilities(pack["capabilities"], result)
        if "constraints" in pack:
            self._check_constraints(pack["constraints"], result)
        if "state" in pack:
            self._check_state(pack["state"], result)
        if "provenance" in pack:
            self._check_provenance(pack["provenance"], result)
        if "policies" in pack:
            self._check_policies(pack["policies"], result)
        if "embeddings" in pack:
            self._check_embeddings(pack, result)
        if verbose:
            self._check_quality_hints(pack, result)
        return result

    def validate_file(self, path: str | Path, verbose: bool = False) -> ValidationResult:
        """Load and validate a JSON file."""
        result = ValidationResult()
        path = Path(path)
        if not path.exists():
            result.error("file", f"File not found: {path}")
            return result
        try:
            with open(path, encoding="utf-8") as f:
                decoded: object = json.load(f)
        except json.JSONDecodeError as e:
            result.error("file", f"Invalid JSON: {e}")
            return result
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            result.error("file", "Pack root must be a JSON object with string keys.")
            return result
        return self.validate(cast(dict[str, object], decoded), verbose=verbose)

    # ── Section checkers ─────────────────────────────────────────────────────

    def _check_header(self, pack: Mapping[str, object], result: ValidationResult) -> None:
        if "header" not in pack:
            result.error("header", "Missing required section 'header'.")
            return
        h = pack["header"]
        if not isinstance(h, dict):
            result.error("header", "Must be an object.")
            return

        # version
        if "version" not in h:
            result.error("header.version", "Missing required field 'version'.")
        else:
            v = h["version"]
            if not isinstance(v, str):
                result.error("header.version", "Must be a string.")
            elif not re.match(r"^\d+\.\d+$", v):
                result.error(
                    "header.version", f"Invalid version format '{v}'. Expected 'MAJOR.MINOR'."
                )
            else:
                major = v.split(".")[0]
                if major != "1":
                    result.error(
                        "header.version",
                        f"Unsupported major version '{major}'. Only major version 1 is supported.",
                    )
                elif v not in self.SUPPORTED_VERSIONS:
                    result.warning(
                        "header.version",
                        f"Unknown minor version '{v}'. Processing with best-effort compatibility.",
                    )

        # id
        if "id" not in h:
            result.error("header.id", "Missing required field 'id'.")
        else:
            id_ = h["id"]
            if not isinstance(id_, str) or len(id_) < 4:
                result.error("header.id", "Must be a non-empty string (min 4 chars).")

        # created
        if "created" not in h:
            result.error("header.created", "Missing required field 'created'.")
        else:
            self._check_iso8601("header.created", h["created"], result)

    def _check_metadata(self, pack: Mapping[str, object], result: ValidationResult) -> None:
        if "metadata" not in pack:
            result.error("metadata", "Missing required section 'metadata'.")
            return
        m = pack["metadata"]
        if not isinstance(m, dict):
            result.error("metadata", "Must be an object.")
            return

        if "task" not in m:
            result.error("metadata.task", "Missing required field 'task'.")
        elif not isinstance(m["task"], str) or not m["task"].strip():
            result.error("metadata.task", "Must be a non-empty string.")

        if "source" not in m:
            result.error("metadata.source", "Missing required field 'source'.")
        elif not isinstance(m["source"], str) or not m["source"].strip():
            result.error("metadata.source", "Must be a non-empty string.")

        if "expires" in m:
            self._check_iso8601("metadata.expires", m["expires"], result, check_future=True)

        if "tags" in m:
            if not isinstance(m["tags"], list):
                result.error("metadata.tags", "Must be an array.")
            elif len(set(m["tags"])) != len(m["tags"]):
                result.warning("metadata.tags", "Duplicate tags found.")

    def _check_blocks(self, pack: Mapping[str, object], result: ValidationResult) -> None:
        if "blocks" not in pack:
            result.error("blocks", "Missing required section 'blocks'.")
            return
        blocks = pack["blocks"]
        if not isinstance(blocks, list):
            result.error("blocks", "Must be an array.")
            return
        if len(blocks) == 0:
            result.error("blocks", "At least one block is required.")
            return

        seen_ids = set()
        for i, block in enumerate(blocks):
            prefix = f"blocks[{i}]"
            if not isinstance(block, dict):
                result.error(prefix, "Each block must be an object.")
                continue

            # type
            if "type" not in block:
                result.error(f"{prefix}.type", "Missing required field 'type'.")
            elif block["type"] not in self.BLOCK_TYPES:
                result.error(
                    f"{prefix}.type",
                    f"Unknown block type '{block['type']}'. Valid: {sorted(self.BLOCK_TYPES)}",
                )

            # id
            if "id" not in block:
                result.error(f"{prefix}.id", "Missing required field 'id'.")
            else:
                bid = block["id"]
                if not isinstance(bid, str) or not self.BLOCK_ID_PATTERN.match(bid):
                    result.error(f"{prefix}.id", f"Invalid id '{bid}'. Must match [a-zA-Z0-9_\\-.]")
                elif bid in seen_ids:
                    result.error(f"{prefix}.id", f"Duplicate block id '{bid}'.")
                else:
                    seen_ids.add(bid)

            # content
            if "content" not in block:
                result.error(f"{prefix}.content", "Missing required field 'content'.")
            elif not isinstance(block["content"], str):
                result.error(f"{prefix}.content", "Must be a string.")

            # optional fields
            if "priority" in block and block["priority"] not in self.PRIORITY_VALUES:
                result.error(
                    f"{prefix}.priority",
                    f"Unknown priority '{block['priority']}'. Valid: {sorted(self.PRIORITY_VALUES)}",
                )

            if "quality" in block:
                q = block["quality"]
                if not isinstance(q, (int, float)) or not (0.0 <= q <= 1.0):
                    result.error(f"{prefix}.quality", "Must be a float between 0.0 and 1.0.")

            if "tokens" in block:
                if not isinstance(block["tokens"], int) or block["tokens"] < 0:
                    result.error(f"{prefix}.tokens", "Must be a non-negative integer.")

    def _check_capabilities(self, caps: object, result: ValidationResult) -> None:
        if not isinstance(caps, dict):
            result.error("capabilities", "Must be an object.")
            return
        tools = caps.get("tools")
        if isinstance(tools, list):
            for i, tool in enumerate(tools):
                p = f"capabilities.tools[{i}]"
                if not isinstance(tool, dict):
                    result.error(p, "Each tool must be an object.")
                    continue
                if "name" not in tool:
                    result.error(f"{p}.name", "Missing required field 'name'.")
                if "description" not in tool:
                    result.error(f"{p}.description", "Missing required field 'description'.")
        servers = caps.get("mcp_servers")
        if isinstance(servers, list):
            for i, srv in enumerate(servers):
                p = f"capabilities.mcp_servers[{i}]"
                if not isinstance(srv, dict):
                    result.error(p, "Each MCP server must be an object.")
                    continue
                if "uri" not in srv:
                    result.error(f"{p}.uri", "Missing required field 'uri'.")
                if "name" not in srv:
                    result.error(f"{p}.name", "Missing required field 'name'.")

    def _check_constraints(self, constraints: object, result: ValidationResult) -> None:
        if not isinstance(constraints, dict):
            result.error("constraints", "Must be an object.")
            return
        if "guardrails" in constraints:
            g = constraints["guardrails"]
            if not isinstance(g, dict):
                result.error("constraints.guardrails", "Must be an object.")
                return
            if "max_cost_usd" in g and not isinstance(g["max_cost_usd"], (int, float)):
                result.error("constraints.guardrails.max_cost_usd", "Must be a number.")
            if "timeout_seconds" in g and (
                not isinstance(g["timeout_seconds"], int) or g["timeout_seconds"] < 1
            ):
                result.error(
                    "constraints.guardrails.timeout_seconds", "Must be a positive integer."
                )

    def _check_state(self, state: object, result: ValidationResult) -> None:
        if not isinstance(state, dict):
            result.error("state", "Must be an object.")
            return
        if "status" in state and state["status"] not in self.WORKFLOW_STATUSES:
            result.error(
                "state.status",
                f"Unknown status '{state['status']}'. Valid: {sorted(self.WORKFLOW_STATUSES)}",
            )
        if "step_index" in state and (
            not isinstance(state["step_index"], int) or state["step_index"] < 0
        ):
            result.error("state.step_index", "Must be a non-negative integer.")

    def _check_provenance(self, prov: object, result: ValidationResult) -> None:
        if not isinstance(prov, dict):
            result.error("provenance", "Must be an object.")
            return
        if "trust_level" in prov and prov["trust_level"] not in self.TRUST_LEVELS:
            result.error(
                "provenance.trust_level",
                f"Unknown trust level '{prov['trust_level']}'. Valid: {sorted(self.TRUST_LEVELS)}",
            )
        transforms = prov.get("transforms")
        if isinstance(transforms, list):
            for i, t in enumerate(transforms):
                if not isinstance(t, dict):
                    result.error(f"provenance.transforms[{i}]", "Must be an object.")
                    continue
                if "type" not in t:
                    result.error(
                        f"provenance.transforms[{i}].type", "Missing required field 'type'."
                    )
                elif t["type"] not in self.TRANSFORM_TYPES:
                    result.warning(
                        f"provenance.transforms[{i}].type", f"Unknown transform type '{t['type']}'."
                    )

    def _check_policies(self, policies: object, result: ValidationResult) -> None:
        if not isinstance(policies, dict):
            result.error("policies", "Must be an object.")
            return
        if "compaction" in policies:
            c = policies["compaction"]
            if not isinstance(c, dict):
                result.error("policies.compaction", "Must be an object.")
                c = {}
            if "mode" in c and c["mode"] not in self.COMPACTION_MODES:
                result.error(
                    "policies.compaction.mode",
                    f"Unknown mode '{c['mode']}'. Valid: {sorted(self.COMPACTION_MODES)}",
                )
            if "max_tokens" in c and (not isinstance(c["max_tokens"], int) or c["max_tokens"] < 1):
                result.error("policies.compaction.max_tokens", "Must be a positive integer.")
        if "budget" in policies:
            b = policies["budget"]
            if not isinstance(b, dict):
                result.error("policies.budget", "Must be an object.")
                return
            if "total" in b and "per_block_max" in b:
                if isinstance(b["total"], int) and isinstance(b["per_block_max"], int):
                    if b["per_block_max"] > b["total"]:
                        result.warning(
                            "policies.budget.per_block_max", "per_block_max exceeds total budget."
                        )

    def _check_embeddings(self, pack: Mapping[str, object], result: ValidationResult) -> None:
        emb = pack.get("embeddings", {})
        if not isinstance(emb, dict):
            result.error("embeddings", "Must be an object.")
            return
        if "block_vectors" in emb:
            raw_blocks = pack.get("blocks", [])
            blocks = raw_blocks if isinstance(raw_blocks, list) else []
            block_ids = {b.get("id") for b in blocks if isinstance(b, dict)}
            block_vectors = emb["block_vectors"]
            if not isinstance(block_vectors, dict):
                result.error("embeddings.block_vectors", "Must be an object.")
                return
            for vid in block_vectors:
                if vid not in block_ids:
                    result.warning(
                        f"embeddings.block_vectors.{vid}",
                        f"Vector references unknown block id '{vid}'.",
                    )

    def _check_quality_hints(self, pack: Mapping[str, object], result: ValidationResult) -> None:
        """Non-fatal quality checks shown in verbose mode."""
        m = pack.get("metadata", {})
        if not isinstance(m, dict):
            return
        if not m.get("target"):
            result.info("metadata.target", "No target specified. Pack may be broadcast.")
        if not m.get("tags"):
            result.info(
                "metadata.tags", "No tags specified. Tags improve routing and searchability."
            )
        if not m.get("expires"):
            result.info(
                "metadata.expires", "No expiry set. Consider adding TTL for time-sensitive packs."
            )
        blocks = pack.get("blocks", [])
        if not isinstance(blocks, list):
            return
        typed_blocks = [b for b in blocks if isinstance(b, dict)]
        if not any(b.get("type") == "instructions" for b in typed_blocks):
            result.info(
                "blocks", "No 'instructions' block found. Consider adding one for agent context."
            )
        has_evidence = any(b.get("type") == "evidence" for b in typed_blocks)
        if has_evidence and "provenance" not in pack:
            result.info(
                "provenance",
                "Pack has evidence blocks but no provenance section. Consider adding trust_level.",
            )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _check_iso8601(
        self,
        field: str,
        value: object,
        result: ValidationResult,
        check_future: bool = False,
    ) -> None:
        if not isinstance(value, str):
            result.error(field, "Must be an ISO 8601 string.")
            return
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if check_future:
                now = datetime.now(timezone.utc)
                if dt < now:
                    result.warning(
                        field, f"Timestamp '{value}' is in the past. Pack may be expired."
                    )
        except ValueError:
            result.error(field, f"Invalid ISO 8601 timestamp: '{value}'.")


# ── Test vectors ──────────────────────────────────────────────────────────────

VALID_PACK_MINIMAL = {
    "header": {"version": "1.0", "id": "pak_test001", "created": "2026-03-07T00:00:00Z"},
    "metadata": {"task": "test", "source": "agent:test"},
    "blocks": [{"type": "knowledge", "id": "ctx", "content": "test content"}],
}

INVALID_PACK_MISSING_HEADER = {
    "metadata": {"task": "test", "source": "agent:test"},
    "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
}

INVALID_PACK_BAD_VERSION = {
    "header": {"version": "2.0", "id": "pak_test002", "created": "2026-03-07T00:00:00Z"},
    "metadata": {"task": "test", "source": "agent:test"},
    "blocks": [{"type": "knowledge", "id": "ctx", "content": "test"}],
}

INVALID_PACK_NO_BLOCKS = {
    "header": {"version": "1.0", "id": "pak_test003", "created": "2026-03-07T00:00:00Z"},
    "metadata": {"task": "test", "source": "agent:test"},
    "blocks": [],
}

INVALID_PACK_BAD_BLOCK_TYPE = {
    "header": {"version": "1.0", "id": "pak_test004", "created": "2026-03-07T00:00:00Z"},
    "metadata": {"task": "test", "source": "agent:test"},
    "blocks": [{"type": "unknown_type", "id": "ctx", "content": "test"}],
}

INVALID_PACK_DUPLICATE_BLOCK_IDS = {
    "header": {"version": "1.0", "id": "pak_test005", "created": "2026-03-07T00:00:00Z"},
    "metadata": {"task": "test", "source": "agent:test"},
    "blocks": [
        {"type": "knowledge", "id": "same_id", "content": "block 1"},
        {"type": "knowledge", "id": "same_id", "content": "block 2"},
    ],
}
