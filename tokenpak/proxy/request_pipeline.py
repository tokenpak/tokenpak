"""
tokenpak.proxy.request_pipeline — Router wiring, route engine singletons,
intent classification, and style contract (protected content detection).

Extracted from runtime/proxy.py (L1589-2144) during the proxy restructure.
Later extended with: _resolve_session_id (session-id resolution),
_apply_budget, _shadow_validate.
"""

from __future__ import annotations

__all__ = (
    "PROTECTED_MARKERS",
    "ROUTER_ENABLED",
    "VALIDATION_GATE_BUDGET_CAP",
    "VALIDATION_GATE_ENABLED",
    "can_compress",
    "classify_message_risk",
    "is_protected_content",
)


import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

from .config import (
    ROUTER_ENABLED,
    VALIDATION_GATE_BUDGET_CAP,
    VALIDATION_GATE_ENABLED,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tokenpak.core.validation_gate import ValidationGate
    from tokenpak.orchestration.precondition_gates import PreconditionGates
    from tokenpak.routing.rules import RouteEngine, RouteRule
    from tokenpak.telemetry.budget_controller import BudgetController


class _RouterProtocol(Protocol):
    def route(self, user_text: str, session_id: str = "") -> _RouterResult: ...

    def health_components(self) -> dict[str, bool]: ...


class _HealthCache(TypedDict):
    ts: float
    data: dict[str, object] | None


class _RouteRulesCache(TypedDict):
    rules: list[RouteRule] | None
    mtime: float
    ts: float


# Sentinel stored in a lazy-singleton slot when construction has been attempted
# and failed. Distinct from ``None`` (never attempted) so the failed init is
# NOT retried on every request — the proxy degrades gracefully but the failure
# is logged exactly once.
_INIT_FAILED = object()

# ---------------------------------------------------------------------------
# Router wiring — DeterministicRouter integration (feature-flagged)
# ---------------------------------------------------------------------------
_ROUTER_INSTANCE: _RouterProtocol | None = None
_ROUTER_LOCK = threading.Lock()


def _get_router() -> _RouterProtocol | None:
    """Return the DeterministicRouter singleton, or None if unavailable/disabled."""
    global _ROUTER_INSTANCE
    if not ROUTER_ENABLED:
        return None
    with _ROUTER_LOCK:
        if _ROUTER_INSTANCE is None:
            try:
                from tokenpak.compression.pipeline import CompressionPipeline
                from tokenpak.compression.recipes import RecipeEngine
                from tokenpak.compression.slot_filler import SlotFiller
                from tokenpak.proxy.intent_policy import decide as _policy_decide

                class _DeterministicRouter:
                    """Classifier-first router: intent → slots → deterministic recipe/action."""

                    def __init__(self) -> None:
                        self._pipeline = CompressionPipeline()
                        self._slot_filler = SlotFiller()
                        self._recipe_engine = RecipeEngine()
                        self._gate = _get_validation_gate()

                    def route(self, user_text: str, session_id: str = "") -> "_RouterResult":
                        t0 = time.time()
                        try:
                            # Phase 0.5: Semantic metadata dict (populated by _classify_intent)
                            _sem_meta: dict[str, object] = {}

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
                                    candidate = pipeline_result.messages[-1].get(
                                        "content", user_text
                                    )
                                    if isinstance(candidate, str):
                                        compressed = candidate

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

                    def health_components(self) -> dict[str, bool]:
                        return {
                            "slot_filler": self._slot_filler is not None,
                            "recipe_engine": self._recipe_engine is not None,
                            "validation_gate": self._gate is not None,
                        }

                _ROUTER_INSTANCE = _DeterministicRouter()
            except Exception as _router_init_err:
                print(f"  ⚠️ Router init failed: {_router_init_err}")
                return None
        return _ROUTER_INSTANCE


_VALIDATION_GATE_INSTANCE: ValidationGate | None = None
_VALIDATION_GATE_LOCK = threading.Lock()


def _has_validation_gate() -> bool:
    try:
        from tokenpak.core import validation_gate as _validation_gate

        if _validation_gate is None:
            return False
        return True
    except Exception:
        return False


def _get_validation_gate() -> ValidationGate | None:
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


@dataclass
class _RouterResult:
    """Lightweight result object from router.route()."""

    ok: bool
    fallback: bool
    intent: str
    recipe_id: str
    slots: dict[str, object]
    elapsed_ms: int
    compressed_text: str = ""
    capsule: object | None = None
    error: str = ""
    fallback_reason: str = ""
    # Keys: intent_alias, intent_canonical, match_type, entity_aliases, normalized
    semantic_meta: dict[str, object] = field(default_factory=dict)


def _classify_intent(text: str, _semantic_meta: dict[str, object] | None = None) -> str:
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
    if not isinstance(data, dict):
        return ""
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        return ""
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
                    candidate = block.get("text", "")
                    if isinstance(candidate, str):
                        parts.append(candidate)
            return " ".join(parts)
    return ""


def _run_router(body_bytes: bytes, session_id: str = "") -> tuple[bytes, dict[str, object] | None]:
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
        meta: dict[str, object] = {
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


def _router_health() -> dict[str, object]:
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

    return {"enabled": True, "components": router.health_components()}


# ---------------------------------------------------------------------------
# Health endpoint response cache (1-second TTL to reduce per-request overhead)
# ---------------------------------------------------------------------------
_health_cache: _HealthCache = {"ts": 0.0, "data": None}
_HEALTH_CACHE_TTL = 1.0  # seconds

# ---------------------------------------------------------------------------
# Singleton for RouteEngine (PERF OPT #1 — avoid per-request construction + YAML I/O)
# RouteStore reads routes.yaml on every store.list() call — cache with mtime guard.
# ---------------------------------------------------------------------------
_ROUTE_ENGINE_INSTANCE: RouteEngine | object | None = None
_ROUTE_ENGINE_LOCK = threading.Lock()
_ROUTE_RULES_CACHE: _RouteRulesCache = {"rules": None, "mtime": 0.0, "ts": 0.0}
_ROUTE_RULES_CACHE_TTL = 5.0  # seconds — refresh rules at most every 5s


def _get_route_engine() -> RouteEngine | None:
    """Return the RouteEngine singleton, creating it lazily."""
    global _ROUTE_ENGINE_INSTANCE
    if _ROUTE_ENGINE_INSTANCE is None:
        with _ROUTE_ENGINE_LOCK:
            if _ROUTE_ENGINE_INSTANCE is None:
                try:
                    from tokenpak.routing.rules import RouteEngine

                    _ROUTE_ENGINE_INSTANCE = RouteEngine()
                except Exception:
                    logger.warning(
                        "RouteEngine init failed; route rules disabled for this "
                        "process (failure logged once, not retried).",
                        exc_info=True,
                    )
                    _ROUTE_ENGINE_INSTANCE = _INIT_FAILED
    if _ROUTE_ENGINE_INSTANCE is _INIT_FAILED:
        return None
    return cast("RouteEngine", _ROUTE_ENGINE_INSTANCE)


def _get_cached_route_rules() -> list[RouteRule]:
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
_PRECOND_GATES_INSTANCE: PreconditionGates | object | None = None
_PRECOND_GATES_LOCK = threading.Lock()


def _get_precond_gates() -> PreconditionGates | None:
    """Return the PreconditionGates singleton."""
    global _PRECOND_GATES_INSTANCE
    if _PRECOND_GATES_INSTANCE is None:
        with _PRECOND_GATES_LOCK:
            if _PRECOND_GATES_INSTANCE is None:
                try:
                    from tokenpak.orchestration.precondition_gates import PreconditionGates

                    _PRECOND_GATES_INSTANCE = PreconditionGates()
                except Exception:
                    logger.warning(
                        "PreconditionGates init failed; precondition gating "
                        "disabled for this process (failure logged once, not "
                        "retried).",
                        exc_info=True,
                    )
                    _PRECOND_GATES_INSTANCE = _INIT_FAILED
    if _PRECOND_GATES_INSTANCE is _INIT_FAILED:
        return None
    return cast("PreconditionGates", _PRECOND_GATES_INSTANCE)


# ---------------------------------------------------------------------------
# Singleton for BudgetController (PERF OPT #3 — avoid per-request import + init)
# ---------------------------------------------------------------------------
_BUDGET_CTRL_INSTANCE: BudgetController | object | None = None
_BUDGET_CTRL_LOCK = threading.Lock()


def _get_budget_controller() -> BudgetController | None:
    """Return the BudgetController singleton."""
    global _BUDGET_CTRL_INSTANCE
    if _BUDGET_CTRL_INSTANCE is None:
        with _BUDGET_CTRL_LOCK:
            if _BUDGET_CTRL_INSTANCE is None:
                try:
                    from tokenpak.telemetry.budget_controller import BudgetController

                    _BUDGET_CTRL_INSTANCE = BudgetController()
                except Exception:
                    logger.warning(
                        "BudgetController init failed; spend/budget enforcement "
                        "disabled for this process (failure logged once, not "
                        "retried).",
                        exc_info=True,
                    )
                    _BUDGET_CTRL_INSTANCE = _INIT_FAILED
    if _BUDGET_CTRL_INSTANCE is _INIT_FAILED:
        return None
    return cast("BudgetController", _BUDGET_CTRL_INSTANCE)


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


def classify_message_risk(msg: Mapping[str, object]) -> str:
    role = msg.get("role", "")
    content = msg.get("content", "")

    if isinstance(content, list):
        text_parts = [
            text
            for p in content
            if isinstance(p, dict) and isinstance((text := p.get("text")), str)
        ]
        content_text = "\n".join(text_parts)
    elif isinstance(content, str):
        content_text = content
    else:
        return "narrative"

    if role in {"system", "developer"}:
        return "protected"
    if is_protected_content(content_text):
        return "protected"
    if role == "tool" or msg.get("type") == "tool_result":
        return "config"
    if "```" in content_text or content_text.count("    ") > 5:
        return "code"
    return "narrative"


def can_compress(risk_class: str, mode: str) -> bool:
    if mode in ("strict", "safe"):  # safe mode disables compression
        return False
    if risk_class == "protected":
        return False
    if mode == "hybrid":
        return risk_class == "narrative"
    return True


# ---------------------------------------------------------------------------
# Stable/volatile partition + fingerprinting (TOKENPAK_MODE=safe)
# ---------------------------------------------------------------------------


def _partition_stable_volatile(body: bytes) -> tuple[bytes, bytes]:
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

    if not isinstance(data, dict):
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
# Session ID resolution
# Transferred from monolith (lines 1189–1212)
# ---------------------------------------------------------------------------


def _resolve_session_id(headers: object, model: str) -> str:
    """Resolve session id with Claude Code priority.

    Order: X-Claude-Code-Session-Id (Claude Code) -> X-TokenPak-Session
    -> model name (last-resort fallback).
    """

    # Case-insensitive header lookup
    def _h(name: str) -> str | None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            get_header = cast(Callable[[str], object], getter)
            # Try common cases first; many header collections are
            # case-insensitive but some test contexts use plain dicts.
            for variant in (name, name.lower(), name.title()):
                v = get_header(variant)
                if isinstance(v, str) and v:
                    return v
        return None

    cc_id = _h("X-Claude-Code-Session-Id")
    if cc_id:
        return cc_id
    oc_id = _h("X-TokenPak-Session")
    if oc_id:
        return oc_id
    return model


def _header_items(headers: object) -> Iterable[tuple[object, object]]:
    items = getattr(headers, "items", None)
    if not callable(items):
        return ()
    get_items = cast(Callable[[], Iterable[tuple[object, object]]], items)
    return get_items()


def _resolve_agent_id(headers: object) -> str:
    """Resolve the agent id from the ``X-Tokenpak-Agent`` header.

    Case-insensitive lookup, lower-cased value — matches the spend_guard
    rolling-caps attribution convention (``spend_guard/orchestrator.py``) so
    the persisted monitor.db ``agent_id`` and the live cap accounting agree.
    Returns ``""`` (the unknown-attribution sentinel, classified ``unknown``
    downstream) when no caller set the header. Never fabricated.
    """
    try:
        for hk, hv in _header_items(headers):
            if str(hk).lower() == "x-tokenpak-agent":
                return str(hv).strip().lower()
    except Exception:
        pass
    return ""


def _resolve_cycle_id(headers: object) -> str:
    """Resolve the cycle id from the ``X-Tokenpak-Cycle`` header.

    No caller stamps this header today; it is resolved here so
    a future worker that sets it is captured with no code change. Until then
    the ``""`` sentinel is written and classified ``unknown`` —
    never fabricated.
    """
    try:
        for hk, hv in _header_items(headers):
            if str(hk).lower() == "x-tokenpak-cycle":
                return str(hv).strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Budget controller — enforce per-bucket token limits
# Transferred from monolith (TPK-CONSOLIDATION-A2c, lines 2070–2082)
# ---------------------------------------------------------------------------


def _apply_budget(
    components: dict[str, object], total_tokens: int | None = None
) -> dict[str, object]:
    """Apply Budgeter allocation policy to context components."""
    try:
        from tokenpak.core.config_loader import get as _cfg

        configured_total = _cfg("budget_total_tokens", 100_000)
        if configured_total is None:
            configured_total = 100_000
        budget_total = total_tokens or int(configured_total)
    except Exception:
        budget_total = total_tokens or 100_000
    try:
        from tokenpak.telemetry.budgeter import Budgeter

        b = Budgeter()
        b.total_tokens = budget_total
        return b.allocate(components)
    except Exception:
        return components  # fail-open
