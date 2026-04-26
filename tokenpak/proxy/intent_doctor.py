# SPDX-License-Identifier: Apache-2.0
"""Doctor / explain renderers for Intent Layer Phase 0.

Two diagnostic views, both surfaced via ``tokenpak doctor``:

  - :func:`render_intent_view` (``--intent``) — operator-readable
    snapshot of: classifier activation, proxy self-capability
    publication, every registered adapter's declaration of
    ``tip.intent.contract-headers-v1``, and whether wire-emission is
    currently enabled for any adapter.

  - :func:`render_explain_last` (``--explain-last``) — most recent
    ``intent_events`` row rendered with every field the proposal
    §5.3 schema declares (contract_id, intent_class, confidence,
    slots present/missing, catch_all_reason, tip_headers_emitted,
    tip_headers_stripped, plus join helpers).

Read-only — never writes to the telemetry store, never mutates any
adapter state, never invokes a provider. Safe to run on any host.

Privacy contract: only the ``raw_prompt_hash`` (sha256 hex digest)
is rendered. The raw prompt body never leaves the per-request log
per Architecture §7.1.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def _intent_db_path() -> Path:
    """Resolved telemetry.db path; falls back to ~/.tokenpak/telemetry.db.

    Mirrors the resolution in :mod:`tokenpak.proxy.intent_contract`
    (single source of truth there). Re-derived here to avoid pulling
    in the writer's lifecycle when only the read path is needed.
    """
    from tokenpak.proxy.intent_contract import _DEFAULT_DB_PATH

    return _DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# --intent view
# ---------------------------------------------------------------------------


def collect_intent_view() -> Dict[str, Any]:
    """Gather the data points rendered by :func:`render_intent_view`.

    Returned shape is JSON-serialisable so callers that pass
    ``--json`` can dump the structure as-is. Keys:

      ``classifier_active`` — Phase 0 always classifies; this is
      ``True`` if the classifier module imports cleanly.

      ``intent_source`` — the value the Phase 0 classifier stamps on
      every ``intent_events`` row.

      ``classify_threshold`` — minimum normalized score for a
      non-catch-all classification.

      ``proxy_publishes_label`` — whether the proxy
      ``SELF_CAPABILITIES_PROXY`` set declares the gate label
      (``tip.intent.contract-headers-v1``). Per proposal §5.2,
      declaring without publishing is an audit finding.

      ``adapters`` — list of ``{name, source_format, declares_label,
      capabilities}`` dicts for every adapter in the default
      registry. ``declares_label`` is the per-adapter §4.3 gate
      result.

      ``would_emit_headers`` — ``True`` iff at least one registered
      adapter declares the gate label. False = every request runs
      telemetry-only on this host.

      ``intent_events_db`` — path + row count of the
      ``intent_events`` SQLite table on this host. ``None`` for the
      count if the DB doesn't exist yet (no requests classified
      since last reset).
    """
    out: Dict[str, Any] = {
        "classifier_active": False,
        "intent_source": None,
        "classify_threshold": None,
        "proxy_publishes_label": False,
        "adapters": [],
        "would_emit_headers": False,
        "intent_events_db": {"path": None, "row_count": None},
        "errors": [],
        # Phase 2.4.3 — active policy config snapshot.
        "policy_config": None,
        "policy_config_path": None,
    }

    try:
        from tokenpak.proxy.intent_classifier import (
            CLASSIFY_THRESHOLD,
            INTENT_SOURCE_V0,
        )

        out["classifier_active"] = True
        out["intent_source"] = INTENT_SOURCE_V0
        out["classify_threshold"] = CLASSIFY_THRESHOLD
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"classifier import failed: {exc!r}")

    try:
        from tokenpak.core.contracts.capabilities import SELF_CAPABILITIES_PROXY
        from tokenpak.proxy.intent_contract import GATE_CAPABILITY

        out["proxy_publishes_label"] = GATE_CAPABILITY in SELF_CAPABILITIES_PROXY
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"capability set unreachable: {exc!r}")

    try:
        from tokenpak.proxy.adapters import build_default_registry
        from tokenpak.proxy.intent_contract import GATE_CAPABILITY

        registry = build_default_registry()
        adapters: List[Dict[str, Any]] = []
        for ad in registry.adapters():
            declares = GATE_CAPABILITY in ad.capabilities
            adapters.append({
                "name": ad.__class__.__name__,
                "source_format": ad.source_format,
                "declares_label": declares,
                "capabilities": sorted(ad.capabilities),
            })
        out["adapters"] = adapters
        out["would_emit_headers"] = any(a["declares_label"] for a in adapters)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"adapter registry unreachable: {exc!r}")

    # Phase 2.4.3 — active policy config snapshot.
    try:
        from tokenpak.proxy.intent_policy_config_loader import (
            resolve_active_config_path,
        )
        from tokenpak.proxy.intent_policy_engine import load_default_config

        cfg = load_default_config()
        out["policy_config"] = cfg.to_dict()
        cpath = resolve_active_config_path()
        out["policy_config_path"] = str(cpath) if cpath is not None else None
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"policy config unreachable: {exc!r}")

    try:
        db_path = _intent_db_path()
        out["intent_events_db"]["path"] = str(db_path)
        if db_path.is_file():
            with sqlite3.connect(str(db_path)) as conn:
                # Table only exists once the writer has committed at
                # least once. Treat absence as 0 rows (not an error)
                # — it's the common state on a fresh install.
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='intent_events'"
                ).fetchone()
                if exists is None:
                    out["intent_events_db"]["row_count"] = 0
                else:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM intent_events"
                    ).fetchone()
                    out["intent_events_db"]["row_count"] = int(row[0]) if row else 0
        else:
            out["intent_events_db"]["row_count"] = 0
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"intent_events read failed: {exc!r}")

    return out


def render_intent_view(view: Optional[Dict[str, Any]] = None) -> str:
    """Format an :func:`collect_intent_view` payload for stdout.

    Plain text, no escape codes. Mirrors the visual rhythm of
    ``--privacy`` so operators don't have to context-switch.
    """
    v = view if view is not None else collect_intent_view()
    lines: List[str] = []
    lines.append("")
    lines.append("TOKENPAK  |  Doctor (Intent Layer Phase 0)")
    lines.append("──────────────────────────────")
    lines.append("")

    classifier = "active" if v["classifier_active"] else "import-failed"
    src = v["intent_source"] or "?"
    thr = v["classify_threshold"]
    lines.append(f"  Classifier:                {classifier} (source={src}, threshold={thr})")
    lines.append("")

    pub = "yes" if v["proxy_publishes_label"] else "NO  ← audit finding"
    lines.append(f"  Proxy publishes label:     {pub}")
    lines.append("    tip.intent.contract-headers-v1 (Standard #23 §4.3)")
    lines.append("")

    lines.append("  Registered adapters (gate declaration):")
    if not v["adapters"]:
        lines.append("    (none registered)")
    else:
        for a in v["adapters"]:
            mark = "✓" if a["declares_label"] else "·"
            lines.append(
                f"    {mark} {a['name']:<40s} ({a['source_format']})"
            )
    lines.append("")

    if v["would_emit_headers"]:
        lines.append("  Wire emission:             ENABLED for adapters above marked ✓")
        lines.append("    Other adapters route telemetry-only (local intent_events row).")
    else:
        lines.append("  Wire emission:             telemetry-only on this host")
        lines.append("    No registered adapter declares the gate label, so no")
        lines.append("    request emits TIP intent / contract headers on the wire.")
        lines.append("    All classifications are recorded locally in intent_events.")
    lines.append("")

    db = v["intent_events_db"]
    rows = db["row_count"]
    rows_str = "(db not yet initialized)" if rows is None else f"{rows} row(s)"
    lines.append(f"  intent_events store:       {db['path']}")
    lines.append(f"                             {rows_str}")
    lines.append("")

    if v["errors"]:
        lines.append("  Diagnostic errors:")
        for e in v["errors"]:
            lines.append(f"    ! {e}")
        lines.append("")

    # Phase 2.4.3 — active config snapshot.
    cfg = v.get("policy_config")
    cpath = v.get("policy_config_path")
    lines.append("  Active policy config (Phase 2.4.3):")
    lines.append(f"    config_path:               {cpath or '(none — using defaults)'}")
    if cfg:
        surface = cfg.get("suggestion_surface", {})
        active_label = (
            "Suggest mode active"
            if cfg.get("mode") == "suggest"
            else "observe_only (default)"
        )
        lines.append(f"    mode:                      {cfg.get('mode')}  ({active_label})")
        lines.append(f"    dry_run:                   {cfg.get('dry_run')}  (locked True in 2.4.3)")
        lines.append(f"    allow_auto_routing:        {cfg.get('allow_auto_routing')}  (locked False in 2.4.3)")
        lines.append(f"    allow_unverified_providers:{cfg.get('allow_unverified_providers')}")
        lines.append(f"    show_suggestions:          {cfg.get('show_suggestions')}")
        lines.append(f"    suggestion_surface.cli:        {surface.get('cli')}")
        lines.append(f"    suggestion_surface.dashboard:  {surface.get('dashboard')}")
        lines.append(f"    suggestion_surface.api:        {surface.get('api')}")
        lines.append(f"    suggestion_surface.response_headers: {surface.get('response_headers')}  (locked False in 2.4.3)")
        lines.append(f"    low_confidence_threshold:  {cfg.get('low_confidence_threshold')}")
    lines.append("")
    lines.append("    Safety posture: dry_run=True, allow_auto_routing=False,")
    lines.append("    response_headers=False — no routing / model / provider /")
    lines.append("    request mutation regardless of mode.")
    lines.append("    TokenPak has not changed routing.")
    lines.append("")

    lines.append("  Run `tokenpak doctor --explain-last` to inspect the most")
    lines.append("  recent classification (full intent_events row).")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# --explain-last view
# ---------------------------------------------------------------------------


def collect_explain_last(*, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read the most recent ``intent_events`` row plus its linked
    ``intent_policy_decisions`` row (Phase 2.2).

    Returns ``None`` when the events DB doesn't exist or the events
    table is empty. The policy-decision linkage is best-effort —
    if the policy table doesn't exist or doesn't carry a row for
    the same ``contract_id``, the returned dict has
    ``policy_decision = None``.
    """
    path = db_path if db_path is not None else _intent_db_path()
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(str(path)) as conn:
            # Be quiet when the table doesn't exist yet — the
            # renderer treats ``None`` as "no rows" and prints the
            # operator-friendly message rather than a stack trace.
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='intent_events'"
            ).fetchone()
            if exists is None:
                return None
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT request_id, contract_id, timestamp, raw_prompt_hash, "
                "intent_class, intent_confidence, intent_slots_present, "
                "intent_slots_missing, intent_source, catch_all_reason, "
                "tip_headers_emitted, tip_headers_stripped, "
                "tokens_in, tokens_out, latency_ms "
                "FROM intent_events "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            contract_id = row["contract_id"]

            # Phase 2.2: pull the linked policy decision if present.
            policy_decision: Optional[Dict[str, Any]] = None
            policy_table = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='intent_policy_decisions'"
            ).fetchone()
            if policy_table is not None and contract_id:
                p = conn.execute(
                    "SELECT decision_id, request_id, contract_id, timestamp, "
                    "mode, intent_class, intent_confidence, action, "
                    "decision_reason, safety_flags, recommended_provider, "
                    "recommended_model, budget_action, compression_profile, "
                    "cache_strategy, delivery_strategy, warning_message, "
                    "requires_user_confirmation "
                    "FROM intent_policy_decisions "
                    "WHERE contract_id = ? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (contract_id,),
                ).fetchone()
                if p is not None:
                    try:
                        flags = json.loads(p["safety_flags"] or "[]")
                    except (TypeError, json.JSONDecodeError):
                        flags = []
                    policy_decision = {
                        "decision_id": p["decision_id"],
                        "request_id": p["request_id"],
                        "contract_id": p["contract_id"],
                        "timestamp": p["timestamp"],
                        "mode": p["mode"],
                        "intent_class": p["intent_class"],
                        "intent_confidence": p["intent_confidence"],
                        "action": p["action"],
                        "decision_reason": p["decision_reason"],
                        "safety_flags": flags,
                        "recommended_provider": p["recommended_provider"],
                        "recommended_model": p["recommended_model"],
                        "budget_action": p["budget_action"],
                        "compression_profile": p["compression_profile"],
                        "cache_strategy": p["cache_strategy"],
                        "delivery_strategy": p["delivery_strategy"],
                        "warning_message": p["warning_message"],
                        "requires_user_confirmation": bool(
                            p["requires_user_confirmation"]
                        ),
                    }

            # Phase 2.4.2 — pull every linked policy suggestion.
            # Linked by decision_id since 2.4.1 wrote one row per
            # eligible decision. Best-effort: missing table or empty
            # set renders as the friendly "(none)" line in the
            # suggestion section.
            policy_suggestions: list[dict[str, Any]] = []
            sugg_table = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='intent_suggestions'"
            ).fetchone()
            if (
                sugg_table is not None
                and policy_decision is not None
                and policy_decision.get("decision_id")
            ):
                for srow in conn.execute(
                    "SELECT suggestion_id, decision_id, contract_id, "
                    "timestamp, suggestion_type, title, message, "
                    "recommended_action, confidence, safety_flags, "
                    "requires_confirmation, user_visible, expires_at, "
                    "source FROM intent_suggestions "
                    "WHERE decision_id = ? "
                    "ORDER BY timestamp DESC",
                    (policy_decision["decision_id"],),
                ).fetchall():
                    try:
                        sflags = json.loads(srow["safety_flags"] or "[]")
                    except (TypeError, json.JSONDecodeError):
                        sflags = []
                    policy_suggestions.append({
                        "suggestion_id": srow["suggestion_id"],
                        "decision_id": srow["decision_id"],
                        "contract_id": srow["contract_id"],
                        "timestamp": srow["timestamp"],
                        "suggestion_type": srow["suggestion_type"],
                        "title": srow["title"],
                        "message": srow["message"],
                        "recommended_action": srow["recommended_action"],
                        "confidence": srow["confidence"],
                        "safety_flags": sflags,
                        "requires_confirmation": bool(srow["requires_confirmation"]),
                        "user_visible": bool(srow["user_visible"]),
                        "expires_at": srow["expires_at"],
                        "source": srow["source"],
                    })
    except sqlite3.DatabaseError:
        return None

    return {
        "request_id": row["request_id"],
        "contract_id": row["contract_id"],
        "timestamp": row["timestamp"],
        "raw_prompt_hash": row["raw_prompt_hash"],
        "intent_class": row["intent_class"],
        "intent_confidence": row["intent_confidence"],
        "intent_slots_present": json.loads(row["intent_slots_present"] or "[]"),
        "intent_slots_missing": json.loads(row["intent_slots_missing"] or "[]"),
        "intent_source": row["intent_source"],
        "catch_all_reason": row["catch_all_reason"],
        "tip_headers_emitted": bool(row["tip_headers_emitted"]),
        "tip_headers_stripped": bool(row["tip_headers_stripped"]),
        "tokens_in": row["tokens_in"],
        "tokens_out": row["tokens_out"],
        "latency_ms": row["latency_ms"],
        "policy_decision": policy_decision,
        "policy_suggestions": policy_suggestions,
        "policy_config": _resolve_policy_config_snapshot(),
    }


def _resolve_policy_config_snapshot() -> Optional[Dict[str, Any]]:
    """Best-effort load of the active config for explain-last.

    Pulled out so the explain return shape is stable — a config-
    loader failure renders ``None`` instead of breaking the row.
    """
    try:
        from tokenpak.proxy.intent_policy_engine import load_default_config

        return load_default_config().to_dict()
    except Exception:  # noqa: BLE001
        return None


def render_explain_last(payload: Optional[Dict[str, Any]] = None) -> str:
    """Render the latest intent_events row in operator-readable form.

    When ``payload is None`` the renderer emits a clear "no rows
    yet" message so a fresh install doesn't look broken.
    """
    p = payload if payload is not None else collect_explain_last()
    if p is None:
        return (
            "\nTOKENPAK  |  Doctor (Intent Layer — explain last)\n"
            "──────────────────────────────\n"
            "\n"
            "  No intent_events rows yet.\n"
            "\n"
            "  The classifier writes one row per request that flows through\n"
            "  the proxy. Send a request via `tokenpak proxy` and re-run\n"
            "  this command. See `tokenpak doctor --intent` for activation\n"
            "  state.\n"
        )

    lines: List[str] = []
    lines.append("")
    lines.append("TOKENPAK  |  Doctor (Intent Layer — explain last)")
    lines.append("──────────────────────────────")
    lines.append("")
    lines.append(f"  request_id:                {p['request_id']}")
    lines.append(f"  contract_id:               {p['contract_id']}")
    lines.append(f"  timestamp:                 {p['timestamp']}")
    lines.append("")
    lines.append(f"  intent_class:              {p['intent_class']}")
    lines.append(f"  confidence:                {p['intent_confidence']:.4f}")
    lines.append(f"  intent_source:             {p['intent_source']}")
    catch = p.get("catch_all_reason")
    catch_str = catch if catch else "(none — non-catch-all classification)"
    lines.append(f"  catch_all_reason:          {catch_str}")
    lines.append("")
    lines.append(f"  slots_present:             {p['intent_slots_present']}")
    lines.append(f"  slots_missing:             {p['intent_slots_missing']}")
    lines.append("")
    lines.append(f"  tip_headers_emitted:       {p['tip_headers_emitted']}")
    lines.append(f"  tip_headers_stripped:      {p['tip_headers_stripped']}")
    if p["tip_headers_emitted"]:
        lines.append("    → resolved request adapter declared")
        lines.append("      'tip.intent.contract-headers-v1'; the five wire")
        lines.append("      headers were attached to the outbound request.")
    elif p["tip_headers_stripped"]:
        lines.append("    → adapter did not declare the gate label;")
        lines.append("      contract stayed in local telemetry only (this row).")
    lines.append("")
    lines.append(f"  raw_prompt_hash:           {p['raw_prompt_hash']}")
    lines.append("    (sha256 dedup digest only — prompts stay in the")
    lines.append("     per-request log per Architecture §7.1.)")
    lines.append("")
    if any(p[k] is not None for k in ("tokens_in", "tokens_out", "latency_ms")):
        lines.append("  Joined cost / latency (Phase 0 best-effort):")
        lines.append(f"    tokens_in:               {p['tokens_in']}")
        lines.append(f"    tokens_out:              {p['tokens_out']}")
        lines.append(f"    latency_ms:              {p['latency_ms']}")
        lines.append("")

    # Phase 2.2 — linked policy decision (dry-run / preview only).
    pd = p.get("policy_decision")
    if pd is None:
        lines.append("  Linked policy decision:    (none recorded yet)")
        lines.append("    The Phase 2.1 dry-run engine writes one decision per")
        lines.append("    classified request. If this row predates Phase 2.1, no")
        lines.append("    linkage is expected.")
        lines.append("")
    else:
        lines.append("  Linked policy decision (Phase 2.2 dry-run / preview only):")
        lines.append(f"    decision_id:               {pd['decision_id']}")
        lines.append(f"    mode:                      {pd['mode']}")
        lines.append(f"    action:                    {pd['action']}")
        lines.append(f"    decision_reason:           {pd['decision_reason']}")
        flags = pd.get("safety_flags") or []
        flags_str = ", ".join(flags) if flags else "(none)"
        lines.append(f"    safety_flags:              {flags_str}")
        if pd.get("recommended_provider"):
            lines.append(f"    recommended_provider:      {pd['recommended_provider']}")
        if pd.get("recommended_model"):
            lines.append(f"    recommended_model:         {pd['recommended_model']}")
        if pd.get("budget_action"):
            lines.append(f"    budget_action:             {pd['budget_action']}")
        if pd.get("compression_profile"):
            lines.append(f"    compression_profile:       {pd['compression_profile']}")
        if pd.get("cache_strategy"):
            lines.append(f"    cache_strategy:            {pd['cache_strategy']}")
        if pd.get("delivery_strategy"):
            lines.append(f"    delivery_strategy:         {pd['delivery_strategy']}")
        lines.append(
            f"    requires_user_confirmation: {pd['requires_user_confirmation']}"
        )
        if pd.get("warning_message"):
            lines.append(f"    warning_message:           {pd['warning_message']}")
        lines.append("")
        lines.append("    DRY-RUN / PREVIEW ONLY — no routing decision was made.")
        lines.append("")

    # Phase 2.4.2 — render every linked policy suggestion. Section
    # is always emitted (with "(none)" when empty) so the operator
    # has a stable layout to scan.
    suggestions = p.get("policy_suggestions") or []
    lines.append(
        "  Policy Suggestions (Phase 2.4.2 — advisory / no-op / default-off):"
    )
    if not suggestions:
        lines.append("    (none recorded yet for this decision)")
    else:
        for s in suggestions:
            lines.append("")
            lines.append(f"    [{s['suggestion_type']}] {s['title']}")
            lines.append(f"      suggestion_id:        {s['suggestion_id']}")
            lines.append(f"      confidence:           {s['confidence']:.4f}")
            sflags = s.get("safety_flags") or []
            sflags_str = ", ".join(sflags) if sflags else "(none)"
            lines.append(f"      safety_flags:         {sflags_str}")
            if s.get("recommended_action"):
                lines.append(
                    f"      recommended_action:   {s['recommended_action']}"
                )
            lines.append(f"      requires_confirmation: {s['requires_confirmation']}")
            lines.append(f"      user_visible:         {s['user_visible']}")
            if s.get("expires_at"):
                lines.append(f"      expires_at:           {s['expires_at']}")
            lines.append("      message:")
            lines.append(f"        {s['message']}")
    lines.append("")
    lines.append(
        "    Suggestions are advisory only. TokenPak has not changed routing."
    )
    lines.append("")

    # Phase 2.4.3 — active config snapshot at the bottom of the
    # explain output so the operator sees what gating was in effect
    # for this row.
    cfg = p.get("policy_config")
    if cfg:
        surface = cfg.get("suggestion_surface", {})
        active = (
            "Suggest mode active"
            if cfg.get("mode") == "suggest"
            else "observe_only (default)"
        )
        lines.append(
            "  Active policy config snapshot (Phase 2.4.3):"
        )
        lines.append(f"    mode:                      {cfg.get('mode')}  ({active})")
        lines.append(f"    show_suggestions:          {cfg.get('show_suggestions')}")
        lines.append(
            f"    suggestion surfaces enabled: cli={surface.get('cli')}, "
            f"dashboard={surface.get('dashboard')}, api={surface.get('api')}"
        )
        lines.append(
            f"    response_headers:          {surface.get('response_headers')}  "
            f"(locked False in 2.4.3)"
        )
        lines.append(f"    dry_run:                   {cfg.get('dry_run')}  (locked True)")
        lines.append(f"    allow_auto_routing:        {cfg.get('allow_auto_routing')}  (locked False)")
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "collect_explain_last",
    "collect_intent_view",
    "render_explain_last",
    "render_intent_view",
]
