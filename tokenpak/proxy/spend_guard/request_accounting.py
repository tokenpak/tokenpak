# SPDX-License-Identifier: Apache-2.0
"""Own accounting from request preflight through the actual provider sends."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, replace

from .contracts import GuardOutcome
from .reservation import (
    Projection,
    ReservationStore,
    ReservationUnavailable,
    pessimistic_output_reservation,
)
from .rolling_caps import RollingCapsConfig

_log = logging.getLogger(__name__)


def _request_config():
    from tokenpak.proxy.guard_snapshot_endpoint import _effective_config

    from .policy import SpendGuardConfig, _coerce_bool, load_config

    # Explicitly disabled accounting must not add config I/O to forwarding.
    # The existing guard still resolves its own policy when durable mode is off.
    switches = ("TOKENPAK_SPEND_GUARD_ENABLED", "TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED")
    if any(name in os.environ and not _coerce_bool(os.environ[name]) for name in switches):
        return SpendGuardConfig(reservations_enabled=False)
    try:
        return _effective_config()
    except FileNotFoundError:
        # Legacy forwarding accepts an absent config. An explicit durable opt-in
        # must instead refuse a missing configured file, whose limits are unknown.
        if _coerce_bool(os.environ.get("TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED")):
            raise
        return load_config(raw_config={})


class RequestAccounting:
    def __init__(self, owner, headers):
        from tokenpak.proxy.request_pipeline import _resolve_agent_id, _resolve_session_id

        self.config = _request_config()
        from .policy import validate_accounting_basis

        validate_accounting_basis(self.config)
        self.store = None
        self.ref = None
        self.attempts = 0
        self.workload = None
        self.tokens = None
        self.credential_kind = None
        self.price = None
        self.force = False
        self.session_id = _resolve_session_id(headers, "")
        self.agent_id = _resolve_agent_id(headers)
        if not self.config.enabled or not self.config.reservations_enabled:
            return
        self.owner_id = owner._guard_snapshot_owner_id
        session_headers = {
            "x-claude-code-session-id",
            "x-tokenpak-session",
            "session-id",
            "thread-id",
        }
        if any(
            name.lower() in session_headers and value != self.session_id
            for name, value in headers.items()
        ):
            raise ValueError("contradictory session attribution")
        if owner.monitor is None:
            raise ReservationUnavailable("native monitor unavailable")
        self.store = ReservationStore(
            self.config.audit_db_path,
            owner.monitor.db_path,
            history_seconds=self.config.reservation_history_seconds,
            max_records=self.config.reservation_max_records,
            accounting_basis=self.config.accounting_basis,
        )
        self.coverage_id = self.store.begin_request(
            self.session_id, self.owner_id, self.config.rolling_caps_window_seconds
        )

    @property
    def preflight_config(self):
        # The final provider body receives the durable rolling check. Early
        # TIP/pending/context policy still runs, without a second local hold.
        return replace(self.config, rolling_caps_enabled=False)

    def observe_directive(self, body: bytes) -> None:
        from .tip_header import parse_and_strip_tip_header

        directive, _ = parse_and_strip_tip_header(body)
        if (
            self.config.accounting_basis == "provider_tokens"
            and directive
            and directive.max_cost_usd is not None
        ):
            raise ReservationUnavailable(
                "provider-token accounting cannot enforce a request monetary cap"
            )
        self.force = bool(directive and (directive.bypass or directive.allow_scope is not None))

    def admit(self, body: bytes, model: str, request_id: str, headers) -> GuardOutcome | None:
        if self.store is None:
            return None
        from tokenpak.proxy.request_pipeline import _resolve_agent_id, _resolve_session_id

        if (
            _resolve_session_id(headers, "") != self.session_id
            or _resolve_agent_id(headers) != self.agent_id
        ):
            raise ValueError("forward attribution changed during preflight")
        from ._context_window import get_model_max_context
        from .block_response import hard_block
        from .estimator import estimate
        from .policy import decide

        try:
            est = estimate(body, model)
        except Exception:
            if self.config.accounting_basis == "provider_tokens":
                raise ReservationUnavailable(
                    "provider-token admission requires a measurable request"
                ) from None
            # Preserve the estimator's explicit passthrough exception. The
            # send is still persisted as unmeasurable coverage, never zero.
            return None
        context = get_model_max_context(model)
        decision = decide(est, self.config, model_max_context_tokens=context)
        if decision.decision == "hard_block":
            return GuardOutcome(
                kind="hard_block",
                response_body=hard_block(decision),
                http_status=402,
                decision=decision,
            )
        payload = json.loads(body)
        declared = [
            payload[k]
            for k in ("max_tokens", "max_completion_tokens", "max_output_tokens")
            if k in payload
        ]
        generation = payload.get("generationConfig")
        if isinstance(generation, dict) and "maxOutputTokens" in generation:
            declared.append(generation["maxOutputTokens"])
        if (
            any(type(value) is not int or value <= 0 for value in declared)
            or len(set(declared)) > 1
        ):
            raise ValueError("ambiguous or invalid output-token limit")
        output = pessimistic_output_reservation(
            declared[0] if declared else None, context, est.projected_input_tokens
        )
        # Bound the projection against every known token-rate band, including
        # long-context and one-hour writes. Assigned tier/region and extra fees
        # may still be unknown; this is a projection, not a billed-cost bound.
        # A predicted cache hit must never lower the pending reservation.
        from tokenpak.models import get_pricing

        pricing = get_pricing(model)
        input_rate = est.rates["input"]
        output_rate = est.rates["output"]
        if pricing is not None:
            for rates in (pricing, *pricing.rate_bands):
                input_rate = max(
                    input_rate,
                    rates.input_per_mtok,
                    rates.cache_write_per_mtok or 0,
                    rates.cache_read_per_mtok or 0,
                )
                output_rate = max(output_rate, rates.output_per_mtok)
        reserved_input = est.projected_input_tokens
        if self.config.accounting_basis == "provider_tokens":
            if (
                type(context) is not int
                or context <= 0
                or not declared
                or pricing is None
                or input_rate <= 0
                or output_rate <= 0
            ):
                raise ReservationUnavailable(
                    "provider-token admission requires a known context, rates and explicit output bound"
                )
            # A full context bound avoids treating the character heuristic as
            # a maximum actual token count. The cost stays only a projection.
            reserved_input = context
        projection = Projection(
            (reserved_input * input_rate + output * output_rate) / 1_000_000,
            reserved_input,
            output,
            reserved_input,
        )
        caps = RollingCapsConfig(
            enabled=self.config.rolling_caps_enabled,
            window_seconds=self.config.rolling_caps_window_seconds,
            **{
                key.removeprefix("rolling_caps_"): value
                for key, value in asdict(self.config).items()
                if key.startswith("rolling_caps_per_")
            },
        )
        self.ref, breach = self.store.reserve(
            session_id=self.session_id,
            agent_id=self.agent_id,
            owner_instance_id=self.owner_id,
            request_id=request_id,
            projection=projection,
            caps=caps,
            ttl_seconds=self.config.reservation_ttl_seconds,
            force=self.force,
        )
        if breach:
            payload = {
                "error": {
                    "type": "tokenpak_spend_guard_reservation_blocked",
                    "message": "Concurrent reserved and recorded usage would exceed the configured cap.",
                    **asdict(breach),
                    "retryable": True,
                    "approval_prompt_available": False,
                }
            }
            return GuardOutcome(
                kind="block",
                response_body=json.dumps(payload, allow_nan=False).encode(),
                http_status=402,
                audit_event="reservation_block",
            )
        return None

    def before_send(self, *, body=None, url=None, headers=None) -> None:
        self.price = None
        if self.store is not None:
            from .request_workload import observe_request

            self.workload = observe_request(body, url, headers)
            if self.config.accounting_basis == "provider_tokens":
                if self.attempts:
                    raise ReservationUnavailable(
                        "provider-token accounting does not permit automatic retries"
                    )
                from .request_tokens import observe_request as observe_tokens

                self.tokens = observe_tokens(
                    body, url, headers, credential_kind=self.credential_kind
                )
                if self.ref is None or self.tokens.token_reason_codes != ("response_unavailable",):
                    raise ReservationUnavailable(
                        "final forwarding is unsupported for provider-token accounting"
                    )
            self.store.attempted_send(self.coverage_id, self.ref, workload=self.workload)
            if self.tokens is not None:
                self.store.record_tokens(self.coverage_id, self.tokens)
        self.attempts += 1

    def record_response(self, raw: bytes, *, streaming: bool, complete: bool, status: int) -> None:
        if self.store is None or self.workload is None:
            return
        from .request_workload import observe_response

        # A failed metadata write leaves the original request-only observation
        # unavailable. It must not prevent the ordinary usage row being logged.
        try:
            observed = observe_response(
                self.workload, raw, streaming=streaming, complete=complete, status=status
            )
            self.store.record_workload(self.coverage_id, observed)
            self.workload = observed
            if self.tokens is not None:
                from .request_tokens import observe_response as observe_tokens_response

                tokens = observe_tokens_response(
                    self.tokens, raw, streaming=streaming, complete=complete, status=status
                )
                self.store.record_tokens(self.coverage_id, tokens)
                self.tokens = tokens
        except Exception:
            _log.warning("request workload observation remains unavailable", exc_info=True)

    def price_usage(self, cost_observation: dict, *, model: str):
        """Return a price only when it binds the exact normalized ledger counts."""
        self.price = None
        if (
            self.config.accounting_basis == "provider_tokens"
            or not self.ref
            or self.attempts != 1
            or self.workload is None
        ):
            return None
        if cost_observation["cost_basis"] != "provider_usage_rate_estimate":
            return None
        from .request_pricing import price_request

        try:
            price = price_request(self.workload)
            price.require_row(
                model=model,
                cost=price.cost_usd,
                **{
                    key: cost_observation[key]
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "cache_read_tokens",
                        "cache_creation_tokens",
                    )
                },
            )
            self.price = price
        except (ValueError, TypeError, OverflowError):
            # Preserve ordinary scalar telemetry with no receipt. Durable
            # monetary admission and native eligibility reject unpriced rows.
            return None
        return price

    def complete_usage(
        self, cost_observation: dict, provider_usage: dict, *, response_complete: bool = True
    ) -> bool:
        return bool(
            self.config.accounting_basis == "priced_usage"
            and self.ref
            and response_complete
            and self.attempts == 1
            and cost_observation["cost_basis"] == "provider_usage_rate_estimate"
            and cost_observation["pricing_source"] in ("seed", "discovered")
            and provider_usage["provider_usage_source"] == "provider_usage_object"
            and provider_usage["provider_cache_read_tokens"] is not None
            and provider_usage["provider_cache_creation_tokens"] is not None
        )

    def complete_token_usage(self, provider_usage: dict, *, response_complete: bool) -> bool:
        if not (
            self.config.accounting_basis == "provider_tokens"
            and self.ref
            and self.attempts == 1
            and response_complete
            and self.tokens is not None
            and self.tokens.token_usage_complete
        ):
            return False
        if provider_usage.get("provider_usage_source") != "provider_usage_object":
            return False
        return all(
            type(provider_usage.get(key)) is int and provider_usage[key] == expected
            for key, expected in (
                ("provider_input_tokens", self.tokens.uncached_input_tokens),
                ("provider_output_tokens", self.tokens.output_tokens),
                ("provider_cache_read_tokens", self.tokens.cache_read_tokens),
                ("provider_cache_creation_tokens", self.tokens.cache_creation_tokens),
            )
        )

    def record_credential_kind(self, platform: str, kind: str) -> None:
        """Receive only router-owned enums; never a credential or client claim."""
        self.credential_kind = (
            kind if platform == "anthropic" and kind in ("oauth", "api_key") else None
        )

    @staticmethod
    def stream_completed(sse_bytes: bytes) -> bool:
        from tokenpak.proxy.streaming import iter_sse_events

        # The currently supported complete accounting path requires a native
        # terminal event. A start-event usage object is not a final response.
        return any(
            isinstance(event, dict) and event.get("type") in ("message_stop", "response.completed")
            for event in iter_sse_events(sse_bytes)
        )

    def finish(self) -> None:
        if self.store is None:
            return
        try:
            if self.ref is not None and self.attempts == 0:
                self.store.release_unsent(self.ref)
            self.store.finish_request(self.coverage_id)
        except Exception:
            # The durable preflight/hold remains unresolved. Never convert a
            # cleanup failure into a successful accounting observation.
            _log.warning("request accounting cleanup remains unresolved", exc_info=True)
