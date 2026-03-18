from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    budget_used: int = 0
    budget_limit: int = 0
    fingerprint: str = ""
    dry_run: bool = False
    plan: Dict[str, Any] = field(default_factory=dict)


class ValidationGate:
    """Final pre-forward guardrail checks for deterministic routing paths."""

    def __init__(self, enabled: bool = True, token_budget_cap: int = 120000):
        self.enabled = bool(enabled)
        self.token_budget_cap = int(token_budget_cap)

    def validate(self, capsule: Any, dry_run: bool = False) -> ValidationResult:
        """Compatibility entry point used by telemetry capsule endpoint."""
        if not self.enabled:
            return ValidationResult(valid=True)

        budget_used = int(getattr(capsule, "token_count", 0) or getattr(capsule, "tokens", 0) or 0)
        errors: List[str] = []
        if self.token_budget_cap > 0 and budget_used > self.token_budget_cap:
            errors.append(f"token budget exceeded: {budget_used} > {self.token_budget_cap}")

        return ValidationResult(
            valid=not errors,
            errors=errors,
            budget_used=budget_used,
            budget_limit=self.token_budget_cap,
            dry_run=bool(dry_run),
        )

    def validate_request(
        self,
        request_body: bytes,
        model: str,
        input_tokens: int,
        router_meta: Optional[Mapping[str, Any]] = None,
    ) -> ValidationResult:
        if not self.enabled:
            return ValidationResult(valid=True)

        errors: List[str] = []
        warnings: List[str] = []
        router_meta = dict(router_meta or {})

        try:
            payload = json.loads(request_body)
        except Exception as exc:
            return ValidationResult(valid=False, errors=[f"invalid JSON payload: {exc}"], budget_used=input_tokens, budget_limit=self.token_budget_cap)

        budget_used = int(input_tokens or 0)
        if self.token_budget_cap > 0 and budget_used > self.token_budget_cap:
            errors.append(f"token budget exceeded: {budget_used} > {self.token_budget_cap}")

        dry_run = self._extract_dry_run(payload)
        deterministic_requested = self._is_deterministic(payload, router_meta)

        if deterministic_requested and not self._has_context_block(payload):
            errors.append("deterministic request missing required context block")

        fingerprint = self._compute_fingerprint(router_meta, payload)

        plan = {
            "model": model,
            "input_tokens": budget_used,
            "budget_limit": self.token_budget_cap,
            "deterministic": deterministic_requested,
            "intent": router_meta.get("intent") or "query",
            "recipe": router_meta.get("recipe_used") or "pipeline-v1",
            "forward": not dry_run,
        }

        if not deterministic_requested:
            warnings.append("deterministic metadata absent; fingerprint based on fallback fields")

        return ValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            budget_used=budget_used,
            budget_limit=self.token_budget_cap,
            fingerprint=fingerprint,
            dry_run=dry_run,
            plan=plan,
        )

    @staticmethod
    def _extract_dry_run(payload: Mapping[str, Any]) -> bool:
        if bool(payload.get("dry_run", False)):
            return True
        tokenpak = payload.get("tokenpak")
        if isinstance(tokenpak, Mapping) and bool(tokenpak.get("dry_run", False)):
            return True
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping) and bool(metadata.get("dry_run", False)):
            return True
        return False

    @staticmethod
    def _is_deterministic(payload: Mapping[str, Any], router_meta: Mapping[str, Any]) -> bool:
        if router_meta.get("intent") and not router_meta.get("fallback", False):
            return True
        tokenpak = payload.get("tokenpak")
        return isinstance(tokenpak, Mapping) and bool(tokenpak.get("deterministic", False))

    @staticmethod
    def _has_context_block(payload: Mapping[str, Any]) -> bool:
        tokenpak = payload.get("tokenpak")
        if isinstance(tokenpak, Mapping):
            ctx = tokenpak.get("context_block")
            if isinstance(ctx, str) and ctx.strip():
                return True
            if isinstance(ctx, Mapping) and len(ctx) > 0:
                return True
        context = payload.get("context")
        if isinstance(context, str) and context.strip():
            return True
        if isinstance(context, Mapping) and len(context) > 0:
            return True
        return False

    @staticmethod
    def _compute_fingerprint(router_meta: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
        intent = str(router_meta.get("intent") or "query")
        recipe = str(router_meta.get("recipe_used") or "pipeline-v1")
        slots = router_meta.get("slots") or {}
        if not isinstance(slots, Mapping):
            slots = {}
        recipe_hash = hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:12]
        blob = json.dumps({"intent": intent, "slots": slots, "recipe_hash": recipe_hash}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]
