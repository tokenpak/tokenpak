# SPDX-License-Identifier: Apache-2.0
"""Phase 2.1 — ``tokenpak intent policy-preview`` renderers.

Read-only. Mirrors :mod:`tokenpak.proxy.intent_doctor` in shape so
operators running both back-to-back get a coherent reading.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def collect_latest() -> Optional[Dict[str, Any]]:
    """Return the most recent ``intent_policy_decisions`` row.

    Returns ``None`` when no decision has been recorded yet.
    """
    from tokenpak.proxy.intent_policy_telemetry import get_default_policy_store

    return get_default_policy_store().fetch_latest()


def render_human(payload: Optional[Dict[str, Any]] = None) -> str:
    p = payload if payload is not None else collect_latest()
    if p is None:
        return (
            "\nTOKENPAK  |  Intent policy preview (Phase 2.1 dry-run)\n"
            "──────────────────────────────\n"
            "\n"
            "  No policy decisions yet.\n"
            "\n"
            "  The dry-run engine writes one row per classified request.\n"
            "  Send a request via `tokenpak proxy` and re-run this command.\n"
            "  See `tokenpak doctor --intent` for activation state.\n"
        )

    lines: List[str] = []
    lines.append("")
    lines.append("TOKENPAK  |  Intent policy preview (Phase 2.1 dry-run)")
    lines.append("──────────────────────────────")
    lines.append("")
    lines.append(f"  decision_id:               {p.get('decision_id')}")
    lines.append(f"  request_id:                {p.get('request_id')}")
    lines.append(f"  contract_id:               {p.get('contract_id')}")
    lines.append(f"  timestamp:                 {p.get('timestamp')}")
    lines.append("")
    lines.append(f"  mode:                      {p.get('mode')}")
    lines.append(f"  intent_class:              {p.get('intent_class')}")
    lines.append(f"  confidence:                {p.get('intent_confidence', 0.0):.4f}")
    lines.append(f"  action:                    {p.get('action')}")
    lines.append(f"  decision_reason:           {p.get('decision_reason')}")
    flags = p.get("safety_flags") or []
    flags_str = ", ".join(flags) if flags else "(none)"
    lines.append(f"  safety_flags:              {flags_str}")
    lines.append("")
    suggestion_block: List[str] = []
    if p.get("recommended_provider"):
        suggestion_block.append(
            f"  recommended_provider:      {p['recommended_provider']}"
        )
    if p.get("recommended_model"):
        suggestion_block.append(
            f"  recommended_model:         {p['recommended_model']}"
        )
    if p.get("compression_profile"):
        suggestion_block.append(
            f"  compression_profile:       {p['compression_profile']}"
        )
    if p.get("cache_strategy"):
        suggestion_block.append(
            f"  cache_strategy:            {p['cache_strategy']}"
        )
    if p.get("delivery_strategy"):
        suggestion_block.append(
            f"  delivery_strategy:         {p['delivery_strategy']}"
        )
    if p.get("budget_action"):
        suggestion_block.append(
            f"  budget_action:             {p['budget_action']}"
        )
    if suggestion_block:
        lines.append("  Suggestion fields:")
        lines.extend(suggestion_block)
        lines.append("")
    if p.get("warning_message"):
        lines.append(f"  warning_message:           {p['warning_message']}")
        lines.append("")
    lines.append("  Config snapshot at decision time:")
    lines.append(f"    mode:                      {p.get('config_mode')}")
    lines.append(f"    dry_run:                   {p.get('config_dry_run')}")
    lines.append(f"    allow_auto_routing:        {p.get('config_allow_auto_routing')}")
    lines.append(f"    allow_unverified_providers: {p.get('config_allow_unverified_providers')}")
    lines.append(f"    low_confidence_threshold:  {p.get('config_low_confidence_threshold')}")
    lines.append("")
    lines.append("  Phase 2.1 is dry-run. Decisions are observational only;")
    lines.append("  no routing, no model swap, no body mutation. See")
    lines.append("  docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md")
    lines.append("  for the rollout plan.")
    lines.append("")
    return "\n".join(lines)


def render_json(payload: Optional[Dict[str, Any]] = None) -> str:
    p = payload if payload is not None else collect_latest()
    if p is None:
        return json.dumps({"decision": None}, indent=2)
    return json.dumps(p, indent=2, sort_keys=True, default=str)


__all__ = ["collect_latest", "render_human", "render_json"]
