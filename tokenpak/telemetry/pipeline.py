"""
TokenPak Telemetry Pipeline — orchestrates processing stages.

Pipeline stages:
INGRESS → DETECT_PROVIDER → NORMALIZE → STORE
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .models import TelemetryEvent
from .storage import TelemetryDB

logger = logging.getLogger(__name__)
# --- Shadow hook + intent classifier (fail-silent) ---
try:
    from tokenpak.shadow_hook import ShadowHook as _ShadowHookClass

    _shadow_hook = _ShadowHookClass()
except Exception:
    _shadow_hook = None  # type: ignore

try:
    from tokenpak.complexity import classify_intent as _classify_intent  # type: ignore
except Exception:
    _classify_intent = None  # type: ignore  # type: ignore

try:
    from tokenpak.shadow_reader import validate as _shadow_validate
except Exception:
    _shadow_validate = None  # type: ignore


class PipelineStage(str, Enum):
    """Pipeline processing stages."""

    INGRESS = "ingress"
    DETECT_PROVIDER = "detect_provider"
    NORMALIZE = "normalize"
    STORE = "store"


@dataclass
class StageResult:
    """Result from a pipeline stage."""

    stage: PipelineStage
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class PipelineResult:
    """Complete pipeline execution result."""

    success: bool
    event_id: Optional[str] = None
    stages: list[StageResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    partial_data_stored: bool = False
    error: Optional[str] = None


class TelemetryPipeline:
    """
    Orchestrates telemetry event processing through stages.

    Errors at any stage don't block storage of partial data.
    """

    def __init__(self, storage: TelemetryDB):
        self.storage = storage

    def process(self, raw_event: dict[str, Any]) -> PipelineResult:
        """Process a raw telemetry event through all pipeline stages."""
        start_time = time.perf_counter()
        stages: list[StageResult] = []
        current_data = raw_event
        event_id = None
        partial_stored = False

        stage_handlers: list[tuple[PipelineStage, Callable]] = [
            (PipelineStage.INGRESS, self._stage_ingress),
            (PipelineStage.DETECT_PROVIDER, self._stage_detect_provider),
            (PipelineStage.NORMALIZE, self._stage_normalize),
            (PipelineStage.STORE, self._stage_store),
        ]

        for stage, handler in stage_handlers:
            stage_start = time.perf_counter()
            try:
                current_data = handler(current_data)
                duration_ms = (time.perf_counter() - stage_start) * 1000
                stages.append(
                    StageResult(
                        stage=stage, success=True, data=current_data, duration_ms=duration_ms
                    )
                )
                if stage == PipelineStage.STORE and current_data:
                    event_id = current_data.get("event_id")
            except Exception as e:
                duration_ms = (time.perf_counter() - stage_start) * 1000
                logger.warning(f"Pipeline stage {stage.value} failed: {e}")
                stages.append(
                    StageResult(stage=stage, success=False, error=str(e), duration_ms=duration_ms)
                )
                if stage != PipelineStage.STORE:
                    try:
                        partial_result = self._store_partial(current_data, stage)
                        if partial_result:
                            partial_stored = True
                            event_id = partial_result.get("event_id")
                    except Exception:
                        pass
                return PipelineResult(
                    success=False,
                    event_id=event_id,
                    stages=stages,
                    total_duration_ms=(time.perf_counter() - start_time) * 1000,
                    partial_data_stored=partial_stored,
                    error=str(e),
                )

        return PipelineResult(
            success=True,
            event_id=event_id,
            stages=stages,
            total_duration_ms=(time.perf_counter() - start_time) * 1000,
        )

    def _stage_ingress(self, raw_event: dict) -> dict:
        if not isinstance(raw_event, dict):
            raise ValueError("Event must be a dictionary")
        if "ingress_ts" not in raw_event:
            raw_event["ingress_ts"] = time.time()
        return raw_event

    def _stage_detect_provider(self, event: dict) -> dict:
        provider = "unknown"
        # Prefer explicit provider when caller supplies it (e.g. openai-codex)
        explicit = event.get("provider") or event.get("_provider")
        if explicit:
            provider = str(explicit).lower()
            event["_detected_provider"] = provider
            return event
        if event.get("type") == "message" or "claude" in str(event.get("model", "")).lower():
            provider = "anthropic"
        elif (
            event.get("object") == "chat.completion" or "gpt" in str(event.get("model", "")).lower()
        ):
            provider = "openai"
        elif "gemini" in str(event.get("model", "")).lower():
            provider = "google"
        event["_detected_provider"] = provider
        return event

    def _stage_normalize(self, event: dict) -> dict:
        provider = event.get("_detected_provider", "unknown")
        usage = event.get("usage", {})
        # OpenClaw session JSONL sometimes uses: input/output/cacheRead/cacheWrite
        if "input" in usage and "prompt_tokens" not in usage:
            try:
                usage = dict(usage)
                usage["prompt_tokens"] = int(usage.get("input") or 0)
                usage["completion_tokens"] = int(usage.get("output") or 0)
            except Exception:
                pass
        if "cacheRead" in usage and "cache_read_input_tokens" not in usage:
            try:
                usage = dict(usage)
                usage["cache_read_input_tokens"] = int(usage.get("cacheRead") or 0)
                usage["cache_creation_input_tokens"] = int(usage.get("cacheWrite") or 0)
            except Exception:
                pass

        if provider == "anthropic":
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)
        elif provider == "openai":
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            cache_read = cache_write = 0
        else:
            input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
            output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
            cache_read = cache_write = 0
        event["_normalized"] = {
            "provider": provider,
            "model": event.get("model", "unknown"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
        }
        return event

    def _stage_store(self, event: dict) -> dict:
        import uuid

        normalized = event.get("_normalized", {})
        model = normalized.get("model", "unknown")

        # Classify intent (fail-silent)
        intent = "unknown"
        if _classify_intent is not None:  # type: ignore
            try:
                query = str(event.get("query", event.get("messages", "")))[:500]
                ic = _classify_intent(query)  # type: ignore
                intent = ic.value if hasattr(ic, "value") else str(ic)
            except Exception:
                pass

        # Shadow hook: pre-store (fail-silent)
        _txn_key = None
        if _shadow_hook is not None:
            try:
                _txn_key = _shadow_hook.record_request(
                    model=model,
                    query=str(event.get("query", ""))[:500],
                    context_tokens=normalized.get("input_tokens", 0),
                )
            except Exception:
                pass

        agent_id = (
            event.get("agent_id")
            or event.get("agent")
            or event.get("worker")
            or normalized.get("agent_id")
            or "unknown"
        )
        session_id = str(event.get("session_id") or "")
        duration_ms = float(event.get("duration_ms") or event.get("latency_ms") or 0.0)

        trace_id = str(event.get("trace_id") or uuid.uuid4())
        request_id = str(event.get("request_id") or event.get("id") or uuid.uuid4())

        telemetry_event = TelemetryEvent(
            trace_id=trace_id,
            request_id=request_id,
            event_type="request_end",
            ts=event.get("ingress_ts", time.time()),
            provider=normalized.get("provider", "unknown"),
            model=model,
            agent_id=str(agent_id),
            status=str(event.get("status") or "ok"),
            session_id=session_id,
            duration_ms=duration_ms,
            payload={
                "input_tokens": normalized.get("input_tokens", 0),
                "output_tokens": normalized.get("output_tokens", 0),
                "total_tokens": normalized.get("total_tokens", 0),
                "cache_read_tokens": normalized.get("cache_read_tokens", 0),
                "cache_write_tokens": normalized.get("cache_write_tokens", 0),
                "intent": intent,
                "raw": event,
            },
        )
        self.storage.insert_event(telemetry_event)

        # Also persist usage + cost rows so dashboard tables can show tokens/$.
        try:
            from .models import Cost, Usage

            usage_raw = event.get("usage") or {}
            u = Usage(
                trace_id=trace_id,
                usage_source=(
                    "provider_reported"
                    if (normalized.get("input_tokens") or normalized.get("output_tokens"))
                    else "unknown"
                ),
                confidence=(
                    "high"
                    if (normalized.get("input_tokens") or normalized.get("output_tokens"))
                    else "low"
                ),
                input_billed=int(normalized.get("input_tokens") or 0),
                output_billed=int(normalized.get("output_tokens") or 0),
                input_est=int(normalized.get("input_tokens") or 0),
                output_est=int(normalized.get("output_tokens") or 0),
                cache_read=int(normalized.get("cache_read_tokens") or 0),
                cache_write=int(normalized.get("cache_write_tokens") or 0),
                total_tokens=int(normalized.get("total_tokens") or 0),
                total_tokens_billed=int(normalized.get("total_tokens") or 0),
                total_tokens_est=int(normalized.get("total_tokens") or 0),
                provider_usage_raw=json.dumps(usage_raw, default=str),
            )
            self.storage.insert_usage(u)

            # cost: accept provider-reported breakdown when available, else 0/unknown
            cst = (usage_raw.get("cost") or {}) if isinstance(usage_raw, dict) else {}
            c = Cost(
                trace_id=trace_id,
                cost_input=float(cst.get("input") or 0.0),
                cost_output=float(cst.get("output") or 0.0),
                cost_cache_read=float(cst.get("cacheRead") or cst.get("cache_read") or 0.0),
                cost_cache_write=float(cst.get("cacheWrite") or cst.get("cache_write") or 0.0),
                cost_total=float(cst.get("total") or 0.0),
                cost_source="provider" if float(cst.get("total") or 0.0) > 0 else "unknown",
                baseline_cost=0.0,
                actual_cost=float(cst.get("total") or 0.0),
                savings_total=0.0,
                savings_qmd=0.0,
                savings_tp=0.0,
            )
            self.storage.insert_cost(c)
        except Exception:
            pass

        # Shadow hook: post-store (fail-silent)
        if _shadow_hook is not None and _txn_key is not None:
            try:
                _shadow_hook.record_response(
                    txn_key=_txn_key,
                    response_text=str(event.get("response", ""))[:200],
                    response_tokens=normalized.get("output_tokens", 0),
                )
            except Exception:
                pass

        # Shadow reader: validate compression if available (fail-silent)
        if _shadow_validate is not None:
            _c = event.get("_compressed")
            _o = event.get("_original")
            if _c and _o:
                try:
                    _shadow_validate(_o, _c)  # type: ignore
                except Exception:
                    pass

        return {"event_id": telemetry_event.trace_id, "stored": True, "intent": intent}

    def _store_partial(self, event: dict, failed_stage: PipelineStage) -> Optional[dict]:
        import uuid

        try:
            normalized = event.get("_normalized", {})
            partial_event = TelemetryEvent(
                trace_id=str(uuid.uuid4()),
                request_id=str(uuid.uuid4()),
                event_type="partial",
                ts=event.get("ingress_ts", time.time()),
                provider=event.get("_detected_provider", "unknown"),
                model=normalized.get("model", event.get("model", "unknown")),
                agent_id="tokenpak",
                status="partial",
                payload={"partial": True, "failed_stage": failed_stage.value, "raw": event},
            )
            self.storage.insert_event(partial_event)
            return {"event_id": partial_event.trace_id, "partial": True}
        except Exception:
            return None
