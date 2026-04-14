"""
tokenpak.proxy.request_pipeline — Router wiring, route engine singletons,
intent classification, and style contract (protected content detection).

Extracted from runtime/proxy.py (L1589-2144) as part of TPK-RESTRUCTURE-005.
Extended in TPK-CONSOLIDATION-A2c with: _resolve_session_id (CCG-03),
_apply_budget, _shadow_validate.
"""

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import (
    _cfg,
    ROUTER_ENABLED,
    VALIDATION_GATE_ENABLED,
    VALIDATION_GATE_BUDGET_CAP,
)

# ---------------------------------------------------------------------------
# Router wiring — DeterministicRouter integration (feature-flagged)
# ---------------------------------------------------------------------------
_ROUTER_INSTANCE = None
_ROUTER_LOCK = threading.Lock()


def _get_router():
    """Return the DeterministicRouter singleton, or None if unavailable/disabled."""
    global _ROUTER_INSTANCE
    if not ROUTER_ENABLED:
        return None
    with _ROUTER_LOCK:
        if _ROUTER_INSTANCE is None:
            try:
                sys.path.insert(
                    0,
                    str(Path.home() / "vault" / "01_PROJECTS" / "tokenpak" / "packages" / "pypi"),
                )
                from tokenpak.compression.pipeline import CompressionPipeline
                from tokenpak.compression.recipes import RecipeEngine
                from tokenpak.compression.slot_filler import SlotFiller
                from tokenpak.proxy.intent_policy import decide as _policy_decide

                try:
                    from tokenpak.core.validation_gate import ValidationGate
                except ImportError:
                    ValidationGate = None  # type: ignore[assignment,misc]

                class _DeterministicRouter:
                    """Classifier-first router: intent → slots → deterministic recipe/action."""

                    def __init__(self):
                        self._pipeline = CompressionPipeline()
                        self._slot_filler = SlotFiller()
                        self._recipe_engine = RecipeEngine()
                        self._gate = (
                            ValidationGate(
                                enabled=VALIDATION_GATE_ENABLED,
                                token_budget_cap=VALIDATION_GATE_BUDGET_CAP,
                            )
                            if ValidationGate is not None
                            and _has_validation_gate()
                            and VALIDATION_GATE_ENABLED
                            else None
                        )

                    def route(self, user_text: str, session_id: str = "") -> "_RouterResult":
                        t0 = time.time()
                        try:
                            # Phase 0.5: Semantic metadata dict (populated by _classify_intent)
                            _sem_meta: dict = {}

                            # Phase 1: Classify intent (semantic resolver runs first internally)
                            intent = _classify_intent(user_text, _semantic_meta=_sem_meta)

                            # Phase 2: Fill slots for this intent
                            filled = self._slot_filler.fill(intent, user_text)

                            # Phase 3: Deterministic policy decision (intent + slots → recipe + action)
                            decision = _policy_decide(intent, filled.slots, filled.confidence)

                            # Phase 4: Compress via pipeline (skipped for low-cost intents)
                            compressed = user_text
                            if decision.action.compress:
                                msgs = [{"role": "user", "content": user_text}]
                                pipeline_result = self._pipeline.run(msgs)
                                if pipeline_result.messages:
                                    compressed = pipeline_result.messages[-1].get(
                                        "content", user_text
                                    )

                            elapsed = int((time.time() - t0) * 1000)
                            result = _RouterResult(
                                ok=True,
                                fallback=decision.fallback,
                                intent=decision.intent,
                                recipe_id=decision.recipe_id,
                                slots=decision.slots_used,
                                elapsed_ms=elapsed,
                                compressed_text=compressed,
                                capsule=None,
                                fallback_reason=decision.fallback_reason,
                            )
                            # Attach semantic resolution metadata for debug/tracing
                            result.semantic_meta = _sem_meta
                            return result
                        except Exception as e:
                            elapsed = int((time.time() - t0) * 1000)
                            return _RouterResult(
                                ok=False,
                                fallback=True,
                                intent="unknown",
                                recipe_id="pipeline-v1",
                                slots={},
                                elapsed_ms=elapsed,
                                compressed_text="",
                                capsule=None,
                                error=str(e),
                                fallback_reason=f"exception:{type(e).__name__}",
                            )

                _ROUTER_INSTANCE = _DeterministicRouter()
            except Exception as _router_init_err:
                print(f"  ⚠️ Router init failed: {_router_init_err}")
                return None
        return _ROUTER_INSTANCE


_VALIDATION_GATE_INSTANCE = None
_VALIDATION_GATE_LOCK = threading.Lock()


def _has_validation_gate() -> bool:
    try:
        from tokenpak.core.validation_gate import ValidationGate  # noqa

        return True
    except Exception:
        return False


def _get_validation_gate():
    global _VALIDATION_GATE_INSTANCE
    if not VALIDATION_GATE_ENABLED:
        return None
    with _VALIDATION_GATE_LOCK:
        if _VALIDATION_GATE_INSTANCE is None:
            try:
                from tokenpak.core.validation_gate import ValidationGate

                _VALIDATION_GATE_INSTANCE = ValidationGate(
                    enabled=True,
                    token_budget_cap=VALIDATION_GATE_BUDGET_CAP,
                )
            except Exception:
                return None
        return _VALIDATION_GATE_INSTANCE


class _RouterResult:
    """Lightweight result object from router.route()."""

    def __init__(
        self,
        ok,
        fallback,
        intent,
        recipe_id,
        slots,
        elapsed_ms,
        compressed_text="",
        capsule=None,
        error="",
        fallback_reason="",
    ):
        self.ok = ok
        self.fallback = fallback
        self.intent = intent
        self.recipe_id = recipe_id
        self.slots = slots
        self.elapsed_ms = elapsed_ms
        self.compressed_text = compressed_text
        self.capsule = capsule
        self.error = error
        self.fallback_reason = fallback_reason
        # Semantic resolution metadata (set by route() when SemanticResolver runs)
        # Keys: intent_alias, intent_canonical, match_type, entity_aliases, normalized
        self.semantic_meta: dict = {}


def _classify_intent(text: str, _semantic_meta: "dict | None" = None) -> str:
    """Keyword-based intent classification — canonical intent set.

    Phase 0: Semantic resolver preprocessing — maps alias variants to canonical
             intents deterministically before keyword matching (faster path +
             handles wording variants not in the keyword lists).
    Priority order matters: more specific checks run first.
    Returns one of: status, usage, execute, debug, summarize, plan,
                    explain, search, create, query (fallback).

    Args:
        text: Raw user input text.
        _semantic_meta: Optional dict populated with semantic resolution metadata
                        for router debug/tracing. Keys: intent_alias, intent_canonical,
                        entity_aliases, normalized.
    """
    # Phase 0: Semantic alias resolution (deterministic, no LLM)
    try:
        from tokenpak.vault.semantic.resolver import get_default_resolver as _get_resolver

        _resolver = _get_resolver()
        _sem_result = _resolver.resolve_intent(text)
        if _sem_result is not None:
            # Populate metadata for caller inspection
            if _semantic_meta is not None:
                _semantic_meta["intent_alias"] = _sem_result.alias_matched
                _semantic_meta["intent_canonical"] = _sem_result.canonical
                _semantic_meta["match_type"] = _sem_result.match_type
            return _sem_result.canonical
    except Exception:
        pass  # Semantic layer is best-effort; fall through to keyword matching

    t = text.lower()
    # status — health/liveness checks (check before debug to avoid "error" overlap)
    if any(
        k in t
        for k in (
            "status",
            "health",
            "is it running",
            "is it up",
            "ping",
            "uptime",
            "alive",
            "reachable",
            "available",
        )
    ):
        return "status"
    # usage — cost/token analytics (check before search/query)
    if any(
        k in t
        for k in ("usage", "cost", "spend", "how much", "token count", "billing", "how many tokens")
    ):
        return "usage"
    # execute — imperative run/deploy/start commands
    if any(
        k in t
        for k in ("run ", "execute", "start ", "deploy", "launch", "trigger", "kick off", "fire")
    ):
        return "execute"
    # debug — error diagnosis
    if any(
        k in t
        for k in (
            "fix",
            "debug",
            "error",
            "bug",
            "broken",
            "failing",
            "exception",
            "traceback",
            "crash",
            "why is",
        )
    ):
        return "debug"
    # summarize — condensing content
    if any(
        k in t for k in ("summarize", "tldr", "brief", "recap", "summary", "condense", "digest")
    ):
        return "summarize"
    # plan — architecture / design / roadmap
    if any(
        k in t
        for k in (
            "plan",
            "design",
            "architect",
            "roadmap",
            "strategy",
            "approach",
            "what should i",
            "how should i",
        )
    ):
        return "plan"
    # explain — knowledge / conceptual questions
    if any(
        k in t
        for k in (
            "explain",
            "what is",
            "how does",
            "describe",
            "tell me about",
            "what does",
            "how do",
        )
    ):
        return "explain"
    # search — lookups and finding things
    if any(k in t for k in ("find", "search", "look up", "where", "locate", "which", "list all")):
        return "search"
    # create — code / artifact generation
    if any(
        k in t
        for k in ("write", "create", "generate", "build", "implement", "make a", "add a", "new ")
    ):
        return "create"
    # query — safe catch-all fallback
    return "query"


def _extract_user_text(body_bytes: bytes) -> str:
    """Extract the last user message text from a request body."""
    try:
        data = json.loads(body_bytes)
    except Exception:
        return ""
    messages = data.get("messages", [])
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return " ".join(parts)
    return ""


def _run_router(body_bytes: bytes, session_id: str = "") -> Tuple[bytes, Optional[dict]]:
    """
    Run the DeterministicRouter on the request body.
    Returns (possibly-modified body, meta dict or None).
    """
    user_text = _extract_user_text(body_bytes)
    if not user_text:
        return body_bytes, None

    router = _get_router()
    if router is None:
        return body_bytes, None

    try:
        result = router.route(user_text, session_id=session_id)
        meta: Dict[str, Any] = {
            "intent": result.intent,
            "recipe_used": result.recipe_id,
            "fallback": result.fallback,
            "total_ms": result.elapsed_ms,
        }
        # Surface slot extraction for debugging and downstream consumers
        if result.slots:
            meta["slots"] = result.slots
        if hasattr(result, "fallback_reason") and result.fallback_reason:
            meta["fallback_reason"] = result.fallback_reason
        if hasattr(result, "error") and result.error:
            meta["error"] = result.error
        return body_bytes, meta
    except Exception as e:
        return body_bytes, {
            "fallback": True,
            "error": str(e),
            "intent": "unknown",
            "recipe_used": "pipeline-v1",
            "total_ms": 0,
        }


def _router_health() -> dict:
    """Return router health/status dict for the /health endpoint."""
    components = {
        "slot_filler": False,
        "recipe_engine": False,
        "validation_gate": False,
    }
    if not ROUTER_ENABLED:
        return {"enabled": False, "components": components}

    router = _get_router()
    if router is None:
        return {"enabled": True, "components": components}

    return {
        "enabled": True,
        "components": {
            "slot_filler": hasattr(router, "_slot_filler") and router._slot_filler is not None,
            "recipe_engine": hasattr(router, "_recipe_engine")
            and router._recipe_engine is not None,
            "validation_gate": hasattr(router, "_gate") and router._gate is not None,
        },
    }


# ---------------------------------------------------------------------------
# Health endpoint response cache (1-second TTL to reduce per-request overhead)
# ---------------------------------------------------------------------------
_health_cache: dict = {"ts": 0.0, "data": None}
_HEALTH_CACHE_TTL = 1.0  # seconds

# ---------------------------------------------------------------------------
# Singleton for RouteEngine (PERF OPT #1 — avoid per-request construction + YAML I/O)
# RouteStore reads routes.yaml on every store.list() call — cache with mtime guard.
# ---------------------------------------------------------------------------
_ROUTE_ENGINE_INSTANCE = None
_ROUTE_ENGINE_LOCK = threading.Lock()
_ROUTE_RULES_CACHE: dict = {"rules": None, "mtime": 0.0, "ts": 0.0}
_ROUTE_RULES_CACHE_TTL = 5.0  # seconds — refresh rules at most every 5s


def _get_route_engine():
    """Return the RouteEngine singleton, creating it lazily."""
    global _ROUTE_ENGINE_INSTANCE
    if _ROUTE_ENGINE_INSTANCE is None:
        with _ROUTE_ENGINE_LOCK:
            if _ROUTE_ENGINE_INSTANCE is None:
                try:
                    from tokenpak.routing.rules import RouteEngine

                    _ROUTE_ENGINE_INSTANCE = RouteEngine()
                except Exception:
                    pass
    return _ROUTE_ENGINE_INSTANCE


def _get_cached_route_rules():
    """Return cached list of RouteRules, refreshing only when routes.yaml changes."""
    now = time.time()
    cache = _ROUTE_RULES_CACHE
    if cache["rules"] is not None and (now - cache["ts"]) < _ROUTE_RULES_CACHE_TTL:
        return cache["rules"]
    engine = _get_route_engine()
    if engine is None:
        return []
    try:
        routes_path = engine.store.path
        try:
            mtime = routes_path.stat().st_mtime if routes_path.exists() else 0.0
        except OSError:
            mtime = 0.0
        if cache["rules"] is not None and mtime == cache["mtime"]:
            cache["ts"] = now
            return cache["rules"]
        rules = engine.store.list()
        cache["rules"] = rules
        cache["mtime"] = mtime
        cache["ts"] = now
        return rules
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Singleton for PreconditionGates (PERF OPT #2 — avoid per-request import + init)
# ---------------------------------------------------------------------------
_PRECOND_GATES_INSTANCE = None
_PRECOND_GATES_LOCK = threading.Lock()


def _get_precond_gates():
    """Return the PreconditionGates singleton."""
    global _PRECOND_GATES_INSTANCE
    if _PRECOND_GATES_INSTANCE is None:
        with _PRECOND_GATES_LOCK:
            if _PRECOND_GATES_INSTANCE is None:
                try:
                    from tokenpak.orchestration.precondition_gates import PreconditionGates

                    _PRECOND_GATES_INSTANCE = PreconditionGates()
                except Exception:
                    pass
    return _PRECOND_GATES_INSTANCE


# ---------------------------------------------------------------------------
# Singleton for BudgetController (PERF OPT #3 — avoid per-request import + init)
# ---------------------------------------------------------------------------
_BUDGET_CTRL_INSTANCE = None
_BUDGET_CTRL_LOCK = threading.Lock()


def _get_budget_controller():
    """Return the BudgetController singleton."""
    global _BUDGET_CTRL_INSTANCE
    if _BUDGET_CTRL_INSTANCE is None:
        with _BUDGET_CTRL_LOCK:
            if _BUDGET_CTRL_INSTANCE is None:
                try:
                    from tokenpak.telemetry.budget_controller import BudgetController

                    _BUDGET_CTRL_INSTANCE = BudgetController()
                except Exception:
                    pass
    return _BUDGET_CTRL_INSTANCE


# ---------------------------------------------------------------------------
# Style Contract: Protected content detection
# ---------------------------------------------------------------------------
PROTECTED_MARKERS = [
    "SOUL.md",
    "AGENTS.md",
    "IDENTITY.md",
    "USER.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "MEMORY.md",
    "BOOTSTRAP.md",
    "You are",
    "Your role is",
    "## Core Truths",
    "## Boundaries",
    "## Response Mode",
    "## Safety",
    "## Vibe",
    '"type": "function"',
    '"parameters":',
    '"required":',
    "## Runtime",
    "## Workspace Files",
    "## Silent Replies",
    "## Heartbeats",
    "## Messaging",
]


def is_protected_content(text: str) -> bool:
    if not text or len(text) < 50:
        return False
    marker_hits = sum(1 for m in PROTECTED_MARKERS if m in text)
    return marker_hits >= 2


def classify_message_risk(msg: dict) -> str:
    role = msg.get("role", "")
    content = msg.get("content", "")

    if isinstance(content, list):
        text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and "text" in p]
        content_text = "\n".join(text_parts)
    elif isinstance(content, str):
        content_text = content
    else:
        return "narrative"

    if role == "system":
        return "protected"
    if is_protected_content(content_text):
        return "protected"
    if role == "tool" or msg.get("type") == "tool_result":
        return "config"
    if "```" in content_text or content_text.count("    ") > 5:
        return "code"
    return "narrative"


def can_compress(risk_class: str, mode: str) -> bool:
    if mode in ("strict", "safe"):  # CCG-10: safe mode disables compression
        return False
    if risk_class == "protected":
        return False
    if mode == "hybrid":
        return risk_class == "narrative"
    return True


# ---------------------------------------------------------------------------
# CCG-10: Stable/volatile partition + fingerprinting (TOKENPAK_MODE=safe)
# ---------------------------------------------------------------------------

def _partition_stable_volatile(body: bytes) -> tuple:
    """Partition request body into (stable_bytes, volatile_bytes) for sha256 fingerprinting.

    Spec Component 8 partition rules:
      Stable region  — tools array + system array + all messages except the newest turn.
                       These fields are byte-equal across consecutive turns when the user
                       has not changed tools/system/persistent instructions.
      Volatile region — newest user turn (messages[-1]).
                        Changes every request; not suitable for cache-prefix preservation.

    Both regions are serialized as deterministic JSON (sort_keys=True, utf-8) so the
    sha256 digest is reproducible independent of dict insertion order.

    Returns:
        (stable_bytes, volatile_bytes) — both are bytes objects ready for sha256().
        If the body cannot be parsed as JSON, returns (b"", body) so volatile_hash
        is non-empty and stable_hash is empty — callers record both as-is.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return b"", body

    messages = data.get("messages", [])
    tools = data.get("tools", [])
    system = data.get("system", [])

    # Stable: tools + system + settled turns (all messages except the newest turn)
    stable_messages = messages[:-1] if len(messages) > 1 else []
    stable_part = {
        "tools": tools,
        "system": system,
        "messages": stable_messages,
    }

    # Volatile: newest turn (the user message or most recent content)
    volatile_messages = messages[-1:] if messages else []
    volatile_part = {
        "messages": volatile_messages,
    }

    stable_bytes = json.dumps(stable_part, sort_keys=True, ensure_ascii=False).encode("utf-8")
    volatile_bytes = json.dumps(volatile_part, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return stable_bytes, volatile_bytes


# ---------------------------------------------------------------------------
# CCG-03: Session ID resolution
# Transferred from monolith (TPK-CONSOLIDATION-A2c, lines 1189–1212)
# ---------------------------------------------------------------------------

def _resolve_session_id(headers: Any, model: str) -> str:
    """Resolve session id with Claude Code priority.

    Order: X-Claude-Code-Session-Id (Claude Code) -> X-TokenPak-Session
    -> model name (last-resort fallback).
    """
    # Case-insensitive header lookup
    def _h(name: str) -> Optional[str]:
        if hasattr(headers, "get"):
            # Try common cases first; many header collections are
            # case-insensitive but some test contexts use plain dicts.
            for variant in (name, name.lower(), name.title()):
                v = headers.get(variant)
                if v:
                    return v
        return None

    cc_id = _h("X-Claude-Code-Session-Id")
    if cc_id:
        return cc_id
    oc_id = _h("X-TokenPak-Session")
    if oc_id:
        return oc_id
    return model


# ---------------------------------------------------------------------------
# Budget controller — enforce per-bucket token limits
# Transferred from monolith (TPK-CONSOLIDATION-A2c, lines 2070–2082)
# ---------------------------------------------------------------------------

def _apply_budget(components: Dict[str, Any], total_tokens: Optional[int] = None) -> Dict[str, Any]:
    """Apply Budgeter allocation policy to context components."""
    try:
        from .config import _cfg
        budget_total = total_tokens or int(_cfg.get("budget_total_tokens", 100_000))
    except Exception:
        budget_total = total_tokens or 100_000
    try:
        sys.path.insert(
            0, str(Path.home() / "vault" / "01_PROJECTS" / "tokenpak" / "packages" / "pypi")
        )
        from tokenpak.telemetry.budgeter import Budgeter

        b = Budgeter()
        return b.allocate(components, total_tokens=budget_total)
    except Exception:
        return components  # fail-open
